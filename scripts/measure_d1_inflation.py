#!/usr/bin/env python3
"""Mede por replay quanto o lookahead D1 alterava o sinal do IRAI."""

from __future__ import annotations

import argparse
import copy
import math
import random
import sqlite3
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.irai import engine as engine_module
from backend.irai.engine import IRAIEngine, IRAISnapshot
from backend.irai.timezones import brt_to_tickmill_offset_hours


DEFAULT_DB = "/tmp/claude-1000/-home-brenoperucchi-Devs-miqueias-rastro-irado/5492199e-05a7-45f3-bc41-2c65682106d5/scratchpad/irai_prod_snapshot.db"
DEFAULT_TARGETS = ("WIN$N", "WDO$N")
# O relógio gravado em ``timestamp_utc`` é o relógio histórico do servidor. O WIN
# encerra cinco minutos antes do WDO no trecho sazonal antigo do snapshot.
COMPLETE_SESSION_LAST_BAR = {"WIN$N": (17, 50), "WDO$N": (17, 55)}
LATE_START_HOUR_BRT = 13
FORWARD_HORIZONS = (3, 6, 20)
BOOTSTRAP_ITERATIONS = 10_000


class ShiftArm(str, Enum):
    WITH_BUG = "COM BUG"
    FIXED = "CORRIGIDO"


def compute_method_for_arm(arm: ShiftArm) -> Callable:
    """Cria o método com a geometria do braço contrafactual escolhido.

    O braço COM BUG existe exclusivamente para este contrafactual: ele restaura
    em memória a condição anterior ao fix D1, sem modificar engine.py.
    """
    original_compute = IRAIEngine.compute_from_db
    original_align = engine_module.align_market_bars

    def compute(self, session_date=None, target=None, version="v1", persist_state=True):
        selected_target = target or engine_module.TARGET
        cfg = self._get_model_config(selected_target)[4]
        data_target = cfg.get("data_proxy") or engine_module.resolve_symbol(selected_target)

        def align_for_arm(rows):
            if arm is ShiftArm.FIXED:
                return original_align(rows)
            bug_rows = []
            for row in rows:
                copied = dict(row)
                if copied["source"] == "br" and copied["symbol"] != data_target:
                    copied["source"] = "d1_bug_unshifted"
                bug_rows.append(copied)
            return original_align(bug_rows)

        with patch.object(engine_module, "align_market_bars", align_for_arm):
            return original_compute(
                self, session_date, target=target, version=version,
                persist_state=persist_state,
            )

    return compute


def readonly_connection(db_path: str | None = None) -> sqlite3.Connection:
    if not db_path:
        raise ValueError("o replay exige um caminho de banco explícito")
    path = Path(db_path).expanduser().resolve()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


@contextmanager
def readonly_engine(db_path: str, arm: ShiftArm, initial_states: dict[str, dict | None]):
    compute = compute_method_for_arm(arm)
    load_initial = lambda _conn, slug: copy.deepcopy(initial_states.get(slug))
    with patch.object(engine_module, "get_connection", readonly_connection), \
         patch.object(engine_module, "load_kalman_state", load_initial):
        instance = IRAIEngine(db_path=db_path)
        with patch.object(IRAIEngine, "compute_from_db", compute):
            yield instance


@dataclass(frozen=True)
class BarPoint:
    timestamp: str
    close: float
    p_up: float
    hour_brt: int


@dataclass(frozen=True)
class ForwardObservation:
    timestamp: str
    p_up: float
    actual_up: bool


@dataclass(frozen=True)
class BootstrapResult:
    delta_pp: float
    ci_low_pp: float
    ci_high_pp: float
    significant: bool


@dataclass(frozen=True)
class CandidateSessions:
    dates: tuple[str, ...]
    discarded: dict[str, str]


@dataclass
class ArmResult:
    sessions: dict[str, tuple[BarPoint, ...]]
    discarded: dict[str, str]


def _real_snapshots(snapshots: Iterable[IRAISnapshot]) -> list[IRAISnapshot]:
    return [s for s in snapshots if not getattr(s, "is_ghost", False)]


def score_session(date: str, snapshots: list[IRAISnapshot]) -> tuple[tuple[BarPoint, ...] | None, str | None]:
    if not snapshots:
        return None, "engine sem snapshots"
    real = _real_snapshots(snapshots)
    if not real:
        return None, "sem snapshots reais (todos ghost)"

    offset_h = brt_to_tickmill_offset_hours(datetime.fromisoformat(f"{date}T12:00:00"))
    points = []
    for snapshot in real:
        ts = datetime.fromisoformat(snapshot.timestamp.replace("Z", "+00:00"))
        brt_ts = ts - timedelta(hours=offset_h)
        close = float(snapshot.win_current)
        p_up = float(snapshot.p_up)
        if not math.isfinite(close) or close <= 0 or not math.isfinite(p_up):
            return None, "close ou P(up) real ausente/inválido"
        points.append(BarPoint(snapshot.timestamp, close, p_up, brt_ts.hour))
    return tuple(points), None


def forward_observations(snapshots: Iterable, horizon: int) -> list[ForwardObservation]:
    """Rotula somente barras reais, usando a barra i+h da mesma sessão."""
    real = _real_snapshots(snapshots)
    result = []
    for index in range(max(0, len(real) - horizon)):
        current = real[index]
        future = real[index + horizon]
        result.append(
            ForwardObservation(
                timestamp=current.timestamp,
                p_up=float(current.p_up),
                actual_up=float(future.win_current) > float(current.win_current),
            )
        )
    return result


def _forward_points(points: tuple[BarPoint, ...], horizon: int) -> list[ForwardObservation]:
    return [
        ForwardObservation(points[i].timestamp, points[i].p_up, points[i + horizon].close > points[i].close)
        for i in range(max(0, len(points) - horizon))
    ]


def replay_arm(
    db_path: str,
    target: str,
    dates: Iterable[str],
    arm: ShiftArm,
    version: str,
    initial_states: dict[str, dict | None],
) -> ArmResult:
    result = ArmResult(sessions={}, discarded={})
    with readonly_engine(db_path, arm, initial_states) as engine:
        for date in dates:
            snapshots = engine.compute_from_db(
                date, target=target, version=version, persist_state=False
            )
            score, reason = score_session(date, snapshots)
            if reason:
                result.discarded[date] = reason
            else:
                result.sessions[date] = score
    return result


def candidate_sessions(db_path: str, target: str, limit: int) -> CandidateSessions:
    conn = readonly_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT substr(timestamp_utc, 1, 10) AS session_date,
                      MAX(timestamp_utc) AS last_timestamp
               FROM market_bars
               WHERE symbol = ? AND timeframe = 'M5'
               GROUP BY session_date
               ORDER BY session_date DESC""",
            (target,),
        ).fetchall()
    finally:
        conn.close()
    selected = []
    discarded = {}
    latest = rows[0]["session_date"] if rows else None
    expected_last_bar = COMPLETE_SESSION_LAST_BAR.get(target, (17, 55))
    for row in rows:
        date = row["session_date"]
        last_time = datetime.fromisoformat(row["last_timestamp"].replace("Z", "+00:00")).time()
        if date == latest:
            discarded[date] = "última data descartada (sessão potencialmente parcial)"
        elif (last_time.hour, last_time.minute) < expected_last_bar:
            expected = f"{expected_last_bar[0]:02d}:{expected_last_bar[1]:02d}"
            discarded[date] = (
                f"última barra real {last_time.strftime('%H:%M')} < {expected} "
                f"no relógio histórico do servidor ({target})"
            )
        elif len(selected) < limit:
            selected.append(date)
        if len(selected) == limit:
            break
    return CandidateSessions(tuple(sorted(selected)), discarded)


def load_initial_states(db_path: str) -> dict[str, dict | None]:
    conn = readonly_connection(db_path)
    try:
        rows = conn.execute("SELECT slug FROM kalman_state").fetchall()
        return {row["slug"]: copy.deepcopy(engine_module.load_kalman_state(conn, row["slug"])) for row in rows}
    finally:
        conn.close()


def _accuracy(predictions: Iterable[tuple[float, bool]]) -> tuple[int, int, float]:
    values = list(predictions)
    hits = sum((p_up > 50.0) == actual_up for p_up, actual_up in values)
    total = len(values)
    return hits, total, (100.0 * hits / total if total else float("nan"))


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def bootstrap_accuracy_delta(
    by_session: dict[str, list[tuple[bool, bool]]],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = 20260713,
) -> BootstrapResult:
    sessions = sorted(by_session)
    if not sessions:
        return BootstrapResult(*(float("nan"),) * 3, significant=False)
    totals = {
        date: (sum(a for a, _ in pairs), sum(b for _, b in pairs), len(pairs))
        for date, pairs in by_session.items()
    }
    a_hits = sum(totals[d][0] for d in sessions)
    b_hits = sum(totals[d][1] for d in sessions)
    count = sum(totals[d][2] for d in sessions)
    delta = 100.0 * (a_hits - b_hits) / count
    rng = random.Random(seed)
    samples = []
    for _ in range(iterations):
        chosen = rng.choices(sessions, k=len(sessions))
        sample_a = sum(totals[d][0] for d in chosen)
        sample_b = sum(totals[d][1] for d in chosen)
        sample_n = sum(totals[d][2] for d in chosen)
        samples.append(100.0 * (sample_a - sample_b) / sample_n)
    low, high = _percentile(samples, 0.025), _percentile(samples, 0.975)
    return BootstrapResult(delta, low, high, low > 0 or high < 0)


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    widths = [max(len(header[i]), *(len(row[i]) for row in rows)) for i in range(len(header))]
    lines = ["  ".join(header[i].ljust(widths[i]) for i in range(len(header)))]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend("  ".join(row[i].ljust(widths[i]) for i in range(len(header))) for row in rows)
    return "\n".join(lines)


def _magnitude(values: list[float]) -> tuple[float, float, float, float, int]:
    return (
        sum(values) / len(values),
        _percentile(values, 0.5),
        _percentile(values, 0.95),
        max(values),
        len(values),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB, help="snapshot SQLite (aberto mode=ro)")
    parser.add_argument(
        "--target", nargs="+", choices=DEFAULT_TARGETS, default=list(DEFAULT_TARGETS),
        help="alvo(s); default: WIN$N WDO$N",
    )
    parser.add_argument("--sessions", type=int, default=120, help="sessões recentes por alvo")
    parser.add_argument(
        "--bootstrap", type=int, default=BOOTSTRAP_ITERATIONS,
        help="reamostragens do bootstrap pareado por sessão",
    )
    args = parser.parse_args()
    if args.sessions <= 0:
        parser.error("--sessions deve ser maior que zero")
    if args.bootstrap <= 0:
        parser.error("--bootstrap deve ser maior que zero")
    return args


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    magnitude_rows = []
    accuracy_rows = []
    summaries = []

    print("Braço A (COM BUG): isola D1 sobre o código de hoje e mantém o offset date-aware.")
    print("Ele NÃO reconstrói os números históricos: antes de 16d4661 havia também A6 (+6h fixo),")
    print("que desalinhava cerca de 33 das 120 sessões da janela.")
    print("Engines: v1 e v2; persist_state=False em toda chamada; SQLite mode=ro + query_only.")
    print("Estado v2: SEM memória entre sessões. O kalman_state do snapshot é posterior à")
    print("janela, e o gate de restore exige state_ts < session_start -- logo ele NUNCA é")
    print("aplicado: cada sessão parte dos pesos estáticos, nos dois braços igualmente.")
    print("Isso mantém o delta A-B limpo, mas NÃO reproduz o live (onde o estado encadeia")
    print("dia após dia, também contaminado) -- a contaminação medida é um PISO.")
    print("Forward: P_up > 50 prevê close[i+h] > close[i]; ghost bars e finais sem h barras são excluídos.")
    print()

    initial_states = load_initial_states(args.db)
    for target in args.target:
        candidates = candidate_sessions(args.db, target, args.sessions)
        dates = list(candidates.dates)
        for version in ("v1", "v2"):
            arm_a = replay_arm(args.db, target, dates, ShiftArm.WITH_BUG, version, initial_states)
            arm_b = replay_arm(args.db, target, dates, ShiftArm.FIXED, version, initial_states)
            common = sorted(set(arm_a.sessions) & set(arm_b.sessions))
            if common != dates:
                raise RuntimeError(f"{target}/{version}: sessões inválidas ou diferentes entre braços")
            buckets = {"todas": [], "<13h BRT": [], "13h+ BRT": [], "terminal": []}
            for date in common:
                a_points, b_points = arm_a.sessions[date], arm_b.sessions[date]
                if [p.timestamp for p in a_points] != [p.timestamp for p in b_points]:
                    raise RuntimeError(f"{target}/{version}/{date}: timestamps reais divergiram")
                for a_point, b_point in zip(a_points, b_points):
                    delta = abs(a_point.p_up - b_point.p_up)
                    buckets["todas"].append(delta)
                    bucket = "<13h BRT" if a_point.hour_brt < LATE_START_HOUR_BRT else "13h+ BRT"
                    buckets[bucket].append(delta)
                buckets["terminal"].append(abs(a_points[-1].p_up - b_points[-1].p_up))
            for bucket, values in buckets.items():
                mean, median, p95, maximum, n = _magnitude(values)
                magnitude_rows.append((target, version, bucket, f"{mean:.3f}", f"{median:.3f}", f"{p95:.3f}", f"{maximum:.3f}", str(n)))

            for horizon in FORWARD_HORIZONS:
                paired = {}
                all_a, all_b = [], []
                for date in common:
                    obs_a = _forward_points(arm_a.sessions[date], horizon)
                    obs_b = _forward_points(arm_b.sessions[date], horizon)
                    if [(o.timestamp, o.actual_up) for o in obs_a] != [(o.timestamp, o.actual_up) for o in obs_b]:
                        raise RuntimeError(f"{target}/{version}/{date}/h{horizon}: labels divergiram")
                    pairs = [
                        ((a.p_up > 50.0) == a.actual_up, (b.p_up > 50.0) == b.actual_up)
                        for a, b in zip(obs_a, obs_b)
                    ]
                    paired[date] = pairs
                    all_a.extend((a.p_up, a.actual_up) for a in obs_a)
                    all_b.extend((b.p_up, b.actual_up) for b in obs_b)
                acc_a, acc_b = _accuracy(all_a), _accuracy(all_b)
                boot = bootstrap_accuracy_delta(
                    paired, iterations=args.bootstrap,
                    seed=20260713 + horizon + (0 if version == "v1" else 100) + (0 if target == "WIN$N" else 1000),
                )
                significance = "SIM" if boot.significant else "NÃO (IC cruza zero)"
                accuracy_rows.append((
                    target, version, str(horizon), f"{acc_a[2]:.2f}%", f"{acc_b[2]:.2f}%",
                    f"{boot.delta_pp:+.2f} pp", f"[{boot.ci_low_pp:+.2f}, {boot.ci_high_pp:+.2f}]",
                    significance, f"{acc_a[0]}/{acc_b[0]}", str(acc_a[1]), str(len(common)),
                ))

        discard_text = "; ".join(f"{date}: {reason}" for date, reason in candidates.discarded.items())
        summaries.append(
            f"{target}: solicitadas={args.sessions}, usadas={len(dates)}, descartadas={len(candidates.discarded)}"
            + (f" ({discard_text})" if discard_text else "")
            + f"; janela={dates[0] if dates else '-'}..{dates[-1] if dates else '-'}"
        )

    print("MAGNITUDE DA CONTAMINAÇÃO |P_up A - P_up B| (pontos percentuais):")
    print(_table(("TARGET", "ENGINE", "FAIXA", "MÉDIA", "MEDIANA", "P95", "MÁX", "N"), magnitude_rows))
    print()
    print("ACURÁCIA FORWARD (horizonte em barras M5 reais; IC95% bootstrap pareado por sessão):")
    print(_table(("TARGET", "ENGINE", "H", "A", "B", "A-B", "IC95%", "SIGNIF.", "ACERTOS A/B", "N BARRAS", "N SESSÕES"), accuracy_rows))
    print()
    print("Sessões:")
    for summary in summaries:
        print(f"  {summary}")
    print(f"Tempo total: {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
