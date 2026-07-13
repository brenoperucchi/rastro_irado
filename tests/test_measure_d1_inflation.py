"""Regressões do replay contrafactual usado para medir o bug D1."""

import json
import sqlite3

from backend.db import SCHEMA, migrate_divergence_config
from backend.irai.engine import IRAIEngine
from scripts.measure_d1_inflation import ShiftArm, compute_method_for_arm


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

