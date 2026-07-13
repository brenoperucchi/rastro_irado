"""Regressão do skew D4 entre a calibração diária e o serving."""

import importlib.util
import json
import os
import sqlite3
import sys
import types
from datetime import date

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import pykalman  # noqa: F401
except ModuleNotFoundError:
    stub = types.ModuleType("pykalman")
    stub.KalmanFilter = object
    sys.modules["pykalman"] = stub

try:
    import statsmodels  # noqa: F401
except ModuleNotFoundError:
    statsmodels_stub = types.ModuleType("statsmodels")
    for submodule in (
        "statsmodels.tsa",
        "statsmodels.tsa.vector_ar",
        "statsmodels.tsa.vector_ar.vecm",
    ):
        sys.modules[submodule] = types.ModuleType(submodule)
    sys.modules["statsmodels"] = statsmodels_stub
    sys.modules["statsmodels.tsa.vector_ar.vecm"].coint_johansen = lambda *args, **kwargs: None

from backend.db import SCHEMA, migrate_divergence_config
from backend.irai.engine import IRAIEngine
from backend.irai.market_geometry import serving_daily_returns


spec = importlib.util.spec_from_file_location(
    "calibrate_universal", os.path.join(ROOT, "scripts", "calibrate_universal.py")
)
calibrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calibrator)


SESSION = "2026-07-10"


def _insert_bar(conn, symbol, source, clock, open_price, close_price):
    conn.execute(
        """INSERT INTO market_bars
           (symbol, source, timeframe, timestamp_utc, open, high, low, close,
            volume, real_volume, delta)
           VALUES (?, ?, 'M5', ?, ?, ?, ?, ?, 1, 1, 0)""",
        (
            symbol,
            source,
            f"{SESSION}T{clock}:00Z",
            open_price,
            max(open_price, close_price),
            min(open_price, close_price),
            close_price,
        ),
    )


def _seed_db(tmp_path):
    db_path = tmp_path / "irai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.close()
    migrate_divergence_config(str(db_path))

    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO asset_models
           (target, slug, display_name, factors, factor_labels,
            session_start_h, session_end_h, active, divergence_config)
           VALUES ('WIN$N', 'fixture', 'Fixture', ?, ?, 9, 18, 1, ?)""",
        (
            json.dumps(["US500"]),
            json.dumps({"US500": "us500"}),
            json.dumps({"use_johansen": False}),
        ),
    )
    for name, value in (
        ("fixture_alpha", 1.0),
        ("fixture_intercept", 0.0),
        ("fixture_w_us500", 1.0),
        ("fixture_sigma_us500", 1.0),
    ):
        conn.execute(
            "INSERT INTO model_params (param_name, value, effective_from) VALUES (?, ?, '2020-01-01')",
            (name, value),
        )

    # O fator abre às 00:00 no eixo servido. A antiga janela 09–18 do
    # calibrador descartava essa abertura e regredia uma variável diferente.
    for minute in range(0, 50, 5):
        _insert_bar(conn, "US500", "tickmill", f"00:{minute:02d}", 10.0, 10.0)
        _insert_bar(conn, "US500", "tickmill", f"09:{minute:02d}", 20.0, 20.0)
    _insert_bar(conn, "US500", "tickmill", "17:00", 30.0, 30.0)
    _insert_bar(conn, "US500", "tickmill", "23:00", 40.0, 40.0)

    # No verão, 09:00–17:00 BRT vira 15:00–23:00 no eixo do servidor.
    for minute in range(0, 50, 5):
        _insert_bar(conn, "WIN$N", "br", f"09:{minute:02d}", 100.0, 100.0)
    _insert_bar(conn, "WIN$N", "br", "17:00", 110.0, 110.0)
    conn.commit()
    return db_path, conn


def test_calibrador_regride_o_mesmo_retorno_de_fator_servido_pelo_engine(tmp_path):
    db_path, conn = _seed_db(tmp_path)
    conn.row_factory = sqlite3.Row

    daily = calibrator.load_daily_returns(conn, 9, 18, "WIN$N")
    calibrated_return = daily["US500"].iloc[0]

    engine = IRAIEngine(db_path=str(db_path))
    snapshots = engine.compute_from_db(
        SESSION, target="WIN$N", version="v1", persist_state=False
    )
    final_target = next(
        snap for snap in reversed(snapshots) if not snap.is_ghost
    )
    served_return = final_target.factors["us500"]["ret"] / 100.0

    assert calibrated_return == pytest.approx(served_return)
    assert calibrated_return == pytest.approx(3.0)


def test_calibrador_fecha_fator_no_cutoff_alinhado_do_target_no_inverno():
    session = "2026-01-15"

    def row(symbol, source, clock, price):
        return {
            "symbol": symbol,
            "source": source,
            "timestamp_utc": f"{session}T{clock}:00Z",
            "open": price,
            "close": price,
        }

    rows = []
    for minute in range(0, 50, 5):
        rows.append(row("US500", "tickmill", f"00:{minute:02d}", 10.0))
        rows.append(row("WIN$N", "br", f"09:{minute:02d}", 100.0))
    rows.extend([
        row("WIN$N", "br", "17:00", 110.0),  # alinhada para 22:00
        row("US500", "tickmill", "22:00", 30.0),
        row("US500", "tickmill", "23:00", 40.0),  # futuro para o target
    ])
    rows.sort(key=lambda item: item["timestamp_utc"])

    daily = serving_daily_returns(rows, "WIN$N")

    assert daily["US500"][date(2026, 1, 15)] == pytest.approx(2.0)
