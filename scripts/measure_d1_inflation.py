#!/usr/bin/env python3
"""Mede por replay quanto o lookahead D1 alterava o sinal do IRAI."""

from __future__ import annotations

import argparse
import inspect
import math
import sqlite3
import sys
import textwrap
import time
from collections import Counter
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
LATE_START_HOUR_BRT = 13


class ShiftArm(str, Enum):
    WITH_BUG = "COM BUG"
    FIXED = "CORRIGIDO"


def compute_method_for_arm(arm: ShiftArm) -> Callable:
    """Cria uma cópia do método com a condição de shift do braço escolhido.

    O braço COM BUG existe exclusivamente para este contrafactual: ele restaura
    em memória a condição anterior ao fix D1, sem modificar engine.py.
    """
    source = textwrap.dedent(inspect.getsource(IRAIEngine.compute_from_db))
    current = 'if d["source"] == "br":\n'
    replacement = {
        ShiftArm.WITH_BUG: 'if d["source"] == "br" and d["symbol"] == data_target:\n',
        ShiftArm.FIXED: current,
    }[arm]
    if source.count(current) != 1:
        raise RuntimeError(
            "engine.py mudou: não foi possível localizar de forma única a condição do shift D1"
        )
    source = source.replace(current, replacement, 1)

    # A indireção mantém o get_connection monkeypatched como read-only no replay.
    namespace = dict(engine_module.__dict__)
    namespace["get_connection"] = lambda db_path=None: engine_module.get_connection(db_path)
    exec(compile(source, f"<compute_from_db:{arm.value}>", "exec"), namespace)
    return namespace["compute_from_db"]


def readonly_connection(db_path: str | None = None) -> sqlite3.Connection:
    if not db_path:
        raise ValueError("o replay exige um caminho de banco explícito")
    path = Path(db_path).expanduser().resolve()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


@contextmanager
def readonly_engine(db_path: str, arm: ShiftArm):
    compute = compute_method_for_arm(arm)
    with patch.object(engine_module, "get_connection", readonly_connection):
        instance = IRAIEngine(db_path=db_path)
        with patch.object(IRAIEngine, "compute_from_db", compute):
            yield instance


@dataclass(frozen=True)
class SessionScore:
    date: str
    actual_up: bool
    final_p_up: float
    late_p_up: tuple[float, ...]


@dataclass
class ArmResult:
    sessions: dict[str, SessionScore]
    discarded: dict[str, str]


def _real_snapshots(snapshots: Iterable[IRAISnapshot]) -> list[IRAISnapshot]:
    return [s for s in snapshots if not getattr(s, "is_ghost", False)]


def score_session(date: str, snapshots: list[IRAISnapshot]) -> tuple[SessionScore | None, str | None]:
    if not snapshots:
        return None, "engine sem snapshots"
    real = _real_snapshots(snapshots)
    if not real:
        return None, "sem snapshots reais (todos ghost)"

    first_open = float(real[0].win_open)
    last_close = float(real[-1].win_current)
    final_p_up = float(real[-1].p_up)
    if not all(math.isfinite(v) and v > 0 for v in (first_open, last_close)):
        return None, "open/close real ausente ou inválido"
    if not math.isfinite(final_p_up):
        return None, "P(up) final inválido"

    offset_h = brt_to_tickmill_offset_hours(datetime.fromisoformat(f"{date}T12:00:00"))
    late_values = []
    for snapshot in real:
        ts = datetime.fromisoformat(snapshot.timestamp.replace("Z", "+00:00"))
        brt_ts = ts - timedelta(hours=offset_h)
        p_up = float(snapshot.p_up)
        if brt_ts.hour >= LATE_START_HOUR_BRT and math.isfinite(p_up):
            late_values.append(p_up)
    if not late_values:
        return None, "sem snapshots reais a partir de 13:00 BRT"

    return SessionScore(
        date=date,
        actual_up=last_close > first_open,
        final_p_up=final_p_up,
        late_p_up=tuple(late_values),
    ), None


def replay_arm(db_path: str, target: str, dates: list[str], arm: ShiftArm) -> ArmResult:
    result = ArmResult(sessions={}, discarded={})
    with readonly_engine(db_path, arm) as engine:
        for date in dates:
            snapshots = engine.compute_from_db(
                date, target=target, version="v1", persist_state=False
            )
            score, reason = score_session(date, snapshots)
            if reason:
                result.discarded[date] = reason
            else:
                result.sessions[date] = score
    return result


def candidate_sessions(db_path: str, target: str, limit: int) -> list[str]:
    conn = readonly_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT DISTINCT substr(timestamp_utc, 1, 10) AS session_date
               FROM market_bars
               WHERE symbol = ? AND timeframe = 'M5'
               ORDER BY session_date DESC
               LIMIT ?""",
            (target, limit),
        ).fetchall()
    finally:
        conn.close()
    return sorted(row["session_date"] for row in rows)


def _accuracy(predictions: Iterable[tuple[float, bool]]) -> tuple[int, int, float]:
    values = list(predictions)
    hits = sum((p_up > 50.0) == actual_up for p_up, actual_up in values)
    total = len(values)
    return hits, total, (100.0 * hits / total if total else float("nan"))


def _format_table(rows: list[tuple[str, str, tuple[int, int, float], tuple[int, int, float]]]) -> str:
    header = ("TARGET", "MÉTRICA", "COM BUG", "CORRIGIDO", "A-B", "ACERTOS A/B", "N")
    rendered = []
    for target, metric, a, b in rows:
        n = str(a[1]) if a[1] == b[1] else f"{a[1]}/{b[1]}"
        rendered.append(
            (target, metric, f"{a[2]:.2f}%", f"{b[2]:.2f}%", f"{a[2]-b[2]:+.2f} pp",
             f"{a[0]}/{b[0]}", n)
        )
    widths = [max(len(header[i]), *(len(row[i]) for row in rendered)) for i in range(len(header))]
    lines = ["  ".join(header[i].ljust(widths[i]) for i in range(len(header)))]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend("  ".join(row[i].ljust(widths[i]) for i in range(len(header))) for row in rendered)
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB, help="snapshot SQLite (aberto mode=ro)")
    parser.add_argument(
        "--target", nargs="+", choices=DEFAULT_TARGETS, default=list(DEFAULT_TARGETS),
        help="alvo(s); default: WIN$N WDO$N",
    )
    parser.add_argument("--sessions", type=int, default=120, help="sessões recentes por alvo")
    args = parser.parse_args()
    if args.sessions <= 0:
        parser.error("--sessions deve ser maior que zero")
    return args


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    table_rows = []
    summaries = []

    print("Definição principal: por sessão, previsão = último P(up) não-ghost (>50 ALTA; senão BAIXA);")
    print("resultado = close da última barra real > open da primeira barra real (ALTA; senão BAIXA).")
    print("Métrica 13h+: cada snapshot real a partir de 13:00 BRT é uma previsão da direção final da sessão.")
    print("Engine: v1 (pesos estáticos vivos); persist_state=False; SQLite mode=ro.")
    print()

    for target in args.target:
        dates = candidate_sessions(args.db, target, args.sessions)
        arm_a = replay_arm(args.db, target, dates, ShiftArm.WITH_BUG)
        arm_b = replay_arm(args.db, target, dates, ShiftArm.FIXED)

        common = sorted(set(arm_a.sessions) & set(arm_b.sessions))
        only_a = set(arm_a.sessions) - set(arm_b.sessions)
        only_b = set(arm_b.sessions) - set(arm_a.sessions)
        if only_a or only_b:
            raise RuntimeError(
                f"{target}: braços produziram conjuntos válidos diferentes: "
                f"só A={sorted(only_a)}, só B={sorted(only_b)}"
            )

        a_final = _accuracy((arm_a.sessions[d].final_p_up, arm_a.sessions[d].actual_up) for d in common)
        b_final = _accuracy((arm_b.sessions[d].final_p_up, arm_b.sessions[d].actual_up) for d in common)
        a_late = _accuracy(
            (p, arm_a.sessions[d].actual_up) for d in common for p in arm_a.sessions[d].late_p_up
        )
        b_late = _accuracy(
            (p, arm_b.sessions[d].actual_up) for d in common for p in arm_b.sessions[d].late_p_up
        )
        table_rows.extend(
            [
                (target, "terminal/sessão", a_final, b_final),
                (target, "snapshots 13h+", a_late, b_late),
            ]
        )
        discarded = len(dates) - len(common)
        reasons = Counter(
            f"A={arm_a.discarded.get(date, 'válida')} / B={arm_b.discarded.get(date, 'válida')}"
            for date in dates if date not in common
        )
        reason_text = "; ".join(f"{reason}: {count}" for reason, count in sorted(reasons.items()))
        summaries.append(
            f"{target}: solicitadas={args.sessions}, encontradas={len(dates)}, "
            f"válidas={len(common)}, descartadas={discarded}"
            + (f" ({reason_text})" if reason_text else "")
            + f"; janela={dates[0] if dates else '-'}..{dates[-1] if dates else '-'}"
        )

    print(_format_table(table_rows))
    print()
    print("Sessões:")
    for summary in summaries:
        print(f"  {summary}")
    print(f"Tempo total: {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
