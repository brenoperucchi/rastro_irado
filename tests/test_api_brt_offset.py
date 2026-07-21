"""Spec do campo `brt_offset_h` na resposta de GET /api/irai/series.

O engine desloca as barras da B3 para o eixo do servidor somando um offset que
VARIA com o horário de verão americano (6h dentro do DST, 5h fora — ver
backend/irai/timezones.py e o commit 16d4661). O frontend reconstrói o eixo BRT
secundário subtraindo esse mesmo offset. Se ele não vier no payload, o cliente
volta a assumir -6h fixo e passa a rotular a abertura do pregão como "08:00"
fora do DST — a partir de 2026-11-01.

Este spec trava o CONTRATO, não o helper: ele exercita a rota de verdade e
falharia se `brt_offset_h` sumisse da resposta. (A primeira versão deste arquivo
só testava o helper puro e uma subtração de inteiros — teria passado verde com
o campo removido do payload. Achado da revisão do Codex sobre 895688e.)

Invariantes:
  1. Alvo B3, sessão de VERÃO  -> brt_offset_h == 6
  2. Alvo B3, sessão de INVERNO -> brt_offset_h == 5   (o bug que motivou tudo)
  3. Alvo GLOBAL (24h)          -> brt_offset_h == 0   (não há eixo BRT)
  4. O campo EXISTE na resposta — remover a chave derruba o teste.

Chama a função da rota diretamente (await api_main.irai_series(...)), como o
spec do GEX faz, passando todo query param explicitamente. Requer fastapi; sem
ele, faz skip (a API não sobe nesta máquina Linux de dev — ver CLAUDE.md,
"Windows-only runtime", deps instaladas ad hoc).
"""
import os
import sys
import json
import types
import asyncio
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mesmo stub dos testes vizinhos: pykalman/statsmodels não existem nesta máquina
# de dev (o runtime é Windows), e sem eles a engine nem importa.
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
    for submodule in ("statsmodels.tsa", "statsmodels.tsa.vector_ar",
                      "statsmodels.tsa.vector_ar.vecm"):
        sys.modules[submodule] = types.ModuleType(submodule)
    sys.modules["statsmodels"] = statsmodels_stub
    sys.modules["statsmodels.tsa.vector_ar.vecm"].coint_johansen = (
        lambda *args, **kwargs: None
    )

try:
    import backend.api.main as api_main
    _HAS_FASTAPI = True
except ModuleNotFoundError:
    _HAS_FASTAPI = False

import backend.db as db_mod
from backend.db import SCHEMA, migrate_divergence_config
from backend.irai.engine import IRAIEngine


def _skip_if_no_fastapi():
    if _HAS_FASTAPI:
        return False
    try:
        import pytest
        pytest.skip("fastapi não instalado neste ambiente")
    except ModuleNotFoundError:
        pass
    return True


def test_lifespan_migra_banco_legado_antes_de_criar_engine(tmp_path, monkeypatch):
    if _skip_if_no_fastapi():
        return

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE market_bars (
            symbol TEXT, source TEXT, timeframe TEXT, timestamp_utc TEXT
        );
        CREATE TABLE asset_models (
            target TEXT PRIMARY KEY, accuracy REAL, r_squared REAL
        );
        CREATE TABLE kalman_state (
            slug TEXT PRIMARY KEY,
            state_mean TEXT NOT NULL,
            state_covariance TEXT NOT NULL,
            johansen_p_value REAL,
            is_cointegrated INTEGER DEFAULT 1,
            timestamp_utc TEXT NOT NULL
        );
        """
    )
    conn.close()

    class EngineFake:
        def __init__(self):
            check = sqlite3.connect(db_path)
            kalman_cols = {
                row[1] for row in check.execute("PRAGMA table_info(kalman_state)")
            }
            asset_cols = {
                row[1] for row in check.execute("PRAGMA table_info(asset_models)")
            }
            check.close()
            assert "factor_signature" in kalman_cols
            assert {"oos_accuracy", "oos_r2"} <= asset_cols
            self.models = {}
            self.registered_targets = []

    monkeypatch.setattr(api_main, "DB_PATH", str(db_path))
    monkeypatch.setattr(api_main, "IRAIEngine", EngineFake)
    revision = {
        "git_commit": "a" * 40,
        "engine_sha256": "b" * 64,
        "kalman_sha256": "c" * 64,
        "runtime_code_sha256": "d" * 64,
        "model_config_sha256": "e" * 64,
    }
    monkeypatch.setattr(api_main, "build_engine_revision", lambda **_kwargs: revision)

    async def subir_api():
        async with api_main.lifespan(api_main.app):
            pass

    asyncio.run(subir_api())
    assert api_main.p_dynamic_runtime_revision == revision


def test_lifespan_nao_anuncia_revisao_se_calibracao_muda_no_startup(
    tmp_path, monkeypatch
):
    """O hash precisa representar os parâmetros que o engine realmente carregou."""
    if _skip_if_no_fastapi():
        return

    db_path = tmp_path / "runtime.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(SCHEMA)
    connection.close()

    class EngineFake:
        models = {}
        registered_targets = []

    before = {
        "git_commit": "a" * 40,
        "engine_sha256": "b" * 64,
        "kalman_sha256": "c" * 64,
        "runtime_code_sha256": "d" * 64,
        "model_config_sha256": "e" * 64,
    }
    after = {**before, "model_config_sha256": "f" * 64}
    revisions = iter((before, after))
    monkeypatch.setattr(api_main, "DB_PATH", str(db_path))
    monkeypatch.setattr(api_main, "IRAIEngine", EngineFake)
    monkeypatch.setattr(
        api_main, "build_engine_revision", lambda **_kwargs: next(revisions)
    )

    async def subir_api():
        async with api_main.lifespan(api_main.app):
            pass

    asyncio.run(subir_api())
    assert api_main.p_dynamic_runtime_revision is None


def _seed(db_path, *, target, source, session, session_start_h):
    """Uma sessão mínima: 1 target + 1 fator global, o bastante pra rota responder."""
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
        (target, "fx", "Fixture", json.dumps(["US500"]), json.dumps({"US500": "f"}),
         session_start_h, 18 if session_start_h else 24,
         json.dumps({"use_johansen": False})),
    )
    for name, value in (("fx_alpha", 1.0), ("fx_intercept", 0.0),
                        ("fx_w_f", 1.0), ("fx_sigma_f", 0.01)):
        conn.execute(
            "INSERT INTO model_params (param_name, value, effective_from) VALUES (?, ?, ?)",
            (name, value, "2020-01-01"))

    for sym, src, price in ((target, source, 1_000.0), ("US500", "tickmill", 10.0)):
        for hh in (9, 10):
            conn.execute(
                """INSERT INTO market_bars
                   (symbol, source, timeframe, timestamp_utc, open, high, low, close,
                    volume, real_volume, delta)
                   VALUES (?, ?, 'M5', ?, ?, ?, ?, ?, 1, 1, 0)""",
                (sym, src, f"{session}T{hh:02d}:00:00Z", price, price, price, price))
    conn.commit()
    conn.close()


def _call_series(db_path, *, target, session):
    """Sobe um engine sobre o DB da fixture e chama a rota de verdade."""
    orig_engine = api_main.engine
    orig_conn = api_main.get_connection
    api_main.engine = IRAIEngine(db_path=str(db_path))
    api_main.get_connection = lambda: db_mod.get_connection(str(db_path))
    api_main.series_cache.clear()
    try:
        resp = asyncio.run(api_main.irai_series(
            session_date=session, target=target, version="v2"))
    finally:
        api_main.engine = orig_engine
        api_main.get_connection = orig_conn
    return json.loads(resp.body) if hasattr(resp, "body") else resp


def _run(target, source, session, session_start_h):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "irai.db")
        _seed(db_path, target=target, source=source, session=session,
              session_start_h=session_start_h)
        return _call_series(db_path, target=target, session=session)


def test_b3_no_verao_expoe_offset_de_6h():
    if _skip_if_no_fastapi():
        return
    data = _run("WIN$N", "br", "2026-07-10", 9)
    assert "brt_offset_h" in data, "o campo sumiu do contrato — o eixo BRT quebra"
    assert data["brt_offset_h"] == 6
    assert data["is_b3"] is True


def test_b3_no_inverno_expoe_offset_de_5h():
    """O caso que motivou a mudança: fora do DST americano o offset é 5h.

    Um `brt_offset_h` hardcoded em 6 (ou o campo ausente, caindo no fallback do
    cliente) faria o eixo BRT rotular a abertura da B3 como 08:00.
    """
    if _skip_if_no_fastapi():
        return
    data = _run("WIN$N", "br", "2026-01-15", 9)
    assert "brt_offset_h" in data
    assert data["brt_offset_h"] == 5

    # A conta que o frontend faz: hora_do_eixo - offset == abertura real (09:00).
    # A série começa com ghost bars (a timeline é a união dos timestamps, e o
    # fator global imprime antes da B3 abrir) — a primeira barra REAL do target
    # é a que interessa.
    primeira_real = next(s for s in data["series"] if not s["is_ghost"])
    hora_no_eixo = int(primeira_real["timestamp"][11:13])
    assert hora_no_eixo == 14, "09:00 BRT + 5h (inverno) = 14:00 no eixo do servidor"
    assert hora_no_eixo - data["brt_offset_h"] == 9


def test_ativo_global_nao_tem_offset_brt():
    if _skip_if_no_fastapi():
        return
    data = _run("US30", "tickmill", "2026-01-15", 0)
    assert data["brt_offset_h"] == 0
    assert data["is_b3"] is False


if __name__ == "__main__":
    if not _HAS_FASTAPI:
        print("SKIP: fastapi não instalado")
        sys.exit(0)
    for fn in (test_b3_no_verao_expoe_offset_de_6h,
               test_b3_no_inverno_expoe_offset_de_5h,
               test_ativo_global_nao_tem_offset_brt):
        fn()
        print(f"ok  {fn.__name__}")
