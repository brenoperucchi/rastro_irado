"""Regressões do alinhamento causal entre barras B3 e Tickmill."""

import json
import os
import sqlite3
import sys
import types
from datetime import datetime

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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


# Verão no relógio do servidor Tickmill (dentro do DST americano) -> offset +6h.
SESSION = "2026-07-10"
# Inverno (fora do DST americano) -> offset +5h. Sem uma sessão de inverno nos
# testes de engine, um `timedelta(hours=6)` literal reintroduzido no caminho do
# engine passaria despercebido: no verão os dois comportamentos são idênticos.
SESSION_INVERNO = "2026-01-15"


def _insert_bar(conn, symbol, source, timestamp, price):
    conn.execute(
        """INSERT INTO market_bars
           (symbol, source, timeframe, timestamp_utc, open, high, low, close,
            volume, real_volume, delta)
           VALUES (?, ?, 'M5', ?, ?, ?, ?, ?, 1, 1, 0)""",
        (symbol, source, timestamp, price, price, price, price),
    )


def _seed_engine(tmp_path, *, target, target_source, factor, factor_source,
                 session_start_h, session=SESSION):
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
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
        (
            target,
            "fixture",
            "Fixture",
            json.dumps([factor]),
            json.dumps({factor: "factor"}),
            session_start_h,
            18 if session_start_h else 24,
            json.dumps({"use_johansen": False}),
        ),
    )
    for name, value in (
        ("fixture_alpha", 1.0),
        ("fixture_intercept", 0.0),
        ("fixture_w_factor", 1.0),
        ("fixture_sigma_factor", 0.01),
    ):
        conn.execute(
            "INSERT INTO model_params (param_name, value, effective_from) VALUES (?, ?, ?)",
            (name, value, "2020-01-01"),
        )

    _insert_bar(conn, target, target_source, f"{session}T09:00:00Z", 1_000.0)
    _insert_bar(conn, factor, factor_source, f"{session}T09:00:00Z", 10.0)
    _insert_bar(conn, factor, factor_source, f"{session}T15:00:00Z", 20.0)
    conn.commit()
    conn.close()
    return IRAIEngine(db_path=str(db_path))


def test_target_b3_consumes_factor_b3_do_mesmo_instante_de_parede(tmp_path):
    engine = _seed_engine(
        tmp_path,
        target="WIN$N",
        target_source="br",
        factor="WDO$N",
        factor_source="br",
        session_start_h=9,
    )

    snapshots = engine.compute_from_db(
        SESSION, target="WIN$N", version="v1", persist_state=False
    )
    target_snapshot = next(
        s for s in snapshots if s.win_current == 1_000.0 and not s.is_ghost
    )

    assert target_snapshot.timestamp.startswith(f"{SESSION}T15:00:00")
    assert target_snapshot.factors["factor"]["current_price"] == 10.0


def test_sessao_de_inverno_desloca_5h_e_mantem_a_causalidade(tmp_path):
    """Fora do DST americano o offset é +5h — e o fator B3 continua alinhado.

    Trava as duas metades do fix de uma vez, no caminho do engine (não só no
    helper puro): um `timedelta(hours=6)` literal reintroduzido aqui colocaria o
    target em 15:00 e faria este teste falhar.
    """
    engine = _seed_engine(
        tmp_path,
        target="WIN$N",
        target_source="br",
        factor="WDO$N",
        factor_source="br",
        session_start_h=9,
        session=SESSION_INVERNO,
    )

    snapshots = engine.compute_from_db(
        SESSION_INVERNO, target="WIN$N", version="v1", persist_state=False
    )
    target_snapshot = next(
        s for s in snapshots if s.win_current == 1_000.0 and not s.is_ghost
    )

    # 09:00 BRT + 5h = 14:00 no eixo do servidor (e não 15:00, como no verão).
    assert target_snapshot.timestamp.startswith(f"{SESSION_INVERNO}T14:00:00")
    # O fator B3 desloca junto: consome a barra do MESMO instante de parede
    # (09:00 BRT, preço 10.0) — não a das 15:00 BRT (preço 20.0), que é futuro.
    assert target_snapshot.factors["factor"]["current_price"] == 10.0


@pytest.mark.parametrize("factor_source", ["tickmill", "axi"])
def test_target_global_preserva_eixo_original(tmp_path, factor_source):
    engine = _seed_engine(
        tmp_path,
        target="GLOBAL",
        target_source="tickmill",
        factor="MACRO",
        factor_source=factor_source,
        session_start_h=0,
    )

    snapshots = engine.compute_from_db(
        SESSION, target="GLOBAL", version="v1", persist_state=False
    )
    target_snapshot = next(s for s in snapshots if s.win_current == 1_000.0)

    assert target_snapshot.timestamp.startswith(f"{SESSION}T09:00:00")
    assert target_snapshot.factors["factor"]["current_price"] == 10.0


@pytest.mark.parametrize("factor_source", ["tickmill", "axi"])
def test_target_b3_nao_desloca_fator_de_outra_origem(tmp_path, factor_source):
    engine = _seed_engine(
        tmp_path,
        target="WIN$N",
        target_source="br",
        factor="MACRO",
        factor_source=factor_source,
        session_start_h=9,
    )

    snapshots = engine.compute_from_db(
        SESSION, target="WIN$N", version="v1", persist_state=False
    )
    target_snapshot = next(
        s for s in snapshots if s.win_current == 1_000.0 and not s.is_ghost
    )

    assert target_snapshot.timestamp.startswith(f"{SESSION}T15:00:00")
    assert target_snapshot.factors["factor"]["current_price"] == 20.0


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2026-07-10T09:00:00", 6),
        ("2026-01-15T09:00:00", 5),
        ("2025-11-01T23:59:59", 6),
        ("2025-11-02T00:00:00", 5),
        ("2026-03-07T23:59:59", 5),
        ("2026-03-08T00:00:00", 6),
        ("2026-10-31T23:59:59", 6),
        ("2026-11-01T00:00:00", 5),
    ],
)
def test_offset_brt_tickmill_segue_transicoes_medidas(timestamp, expected):
    from backend.irai.timezones import brt_to_tickmill_offset_hours

    assert brt_to_tickmill_offset_hours(datetime.fromisoformat(timestamp)) == expected
