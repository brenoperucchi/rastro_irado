"""Regressões do replay contrafactual usado para medir o bug D1."""

import json
import sqlite3

import pytest

from backend.db import SCHEMA, migrate_divergence_config
from backend.irai.engine import IRAIEngine
from scripts.measure_d1_inflation import (
    ShiftArm,
    bootstrap_accuracy_delta,
    compute_method_for_arm,
    forward_observations,
)


SESSION = "2026-07-10"


def _bar(conn, symbol, timestamp, price):
    conn.execute(
        """INSERT INTO market_bars
           (symbol, source, timeframe, timestamp_utc, open, high, low, close,
            volume, real_volume, delta)
           VALUES (?, 'br', 'M5', ?, ?, ?, ?, ?, 1, 1, 0)""",
        (symbol, timestamp, price, price, price, price),
    )


def _engine(tmp_path):
    db = tmp_path / "irai.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.close()
    migrate_divergence_config(str(db))

    conn = sqlite3.connect(db)
    conn.execute(
        """INSERT INTO asset_models
           (target, slug, display_name, factors, factor_labels,
            session_start_h, session_end_h, active, divergence_config)
           VALUES ('WIN$N', 'fixture', 'Fixture', ?, ?, 12, 21, 1, ?)""",
        (json.dumps(["WDO$N"]), json.dumps({"WDO$N": "wdo"}),
         json.dumps({"use_johansen": False})),
    )
    for name, value in (
        ("fixture_alpha", 1.0),
        ("fixture_intercept", 0.0),
        ("fixture_w_wdo", 1.0),
        ("fixture_sigma_wdo", 0.01),
    ):
        conn.execute(
            "INSERT INTO model_params (param_name, value, effective_from) VALUES (?, ?, '2020-01-01')",
            (name, value),
        )
    _bar(conn, "WIN$N", f"{SESSION}T09:00:00Z", 1_000.0)
    _bar(conn, "WDO$N", f"{SESSION}T09:00:00Z", 10.0)
    _bar(conn, "WDO$N", f"{SESSION}T12:00:00Z", 20.0)
    conn.commit()
    conn.close()
    return IRAIEngine(str(db))


def _factor_price(engine, arm):
    compute = compute_method_for_arm(arm)
    snapshots = compute(
        engine, SESSION, target="WIN$N", version="v1", persist_state=False
    )
    real = next(s for s in snapshots if not s.is_ghost)
    return real.factors["wdo"]["current_price"]


def test_braco_com_bug_reproduz_lookahead_sem_editar_engine(tmp_path):
    assert _factor_price(_engine(tmp_path), ShiftArm.WITH_BUG) == 20.0


def test_braco_corrigido_preserva_alinhamento_causal_do_head(tmp_path):
    assert _factor_price(_engine(tmp_path), ShiftArm.FIXED) == 10.0


class _Snapshot:
    def __init__(self, timestamp, close, p_up, *, ghost=False):
        self.timestamp = timestamp
        self.win_current = close
        self.p_up = p_up
        self.is_ghost = ghost


def test_horizonte_forward_conta_barras_reais_e_nao_cruza_sessao():
    snapshots = [
        _Snapshot("2026-07-10T12:00:00+00:00", 100, 60),
        _Snapshot("2026-07-10T12:01:00+00:00", 999, 1, ghost=True),
        _Snapshot("2026-07-10T12:05:00+00:00", 101, 40),
        _Snapshot("2026-07-10T12:10:00+00:00", 99, 55),
        _Snapshot("2026-07-10T12:15:00+00:00", 102, 45),
    ]

    observations = forward_observations(snapshots, horizon=2)

    assert [(item.p_up, item.actual_up) for item in observations] == [
        (60.0, False),
        (40.0, True),
    ]


def test_bootstrap_pareado_reamostra_sessoes_e_e_reprodutivel():
    # A vence todas as observações em uma sessão e perde todas na outra. O IC
    # largo confirma que o N do bootstrap é 2 sessões, não 200 snapshots.
    by_session = {
        "s1": [(True, False)] * 100,
        "s2": [(False, True)] * 100,
    }

    first = bootstrap_accuracy_delta(by_session, iterations=2_000, seed=7)
    second = bootstrap_accuracy_delta(by_session, iterations=2_000, seed=7)

    assert first == second
    assert first.delta_pp == pytest.approx(0.0)
    assert first.ci_low_pp == pytest.approx(-100.0)
    assert first.ci_high_pp == pytest.approx(100.0)
    assert not first.significant
