"""Contrato de API dos campos NWE em /api/irai/series e /api/irai/overview.

A engine enriquece cada snapshot com os 15 campos NWE causais
(backend/irai/nwe.py, aplicados após a passagem principal em engine.py). Estes
specs travam o CONTRATO da camada de SERIALIZAÇÃO (backend/api/main.py), que é o
que a task adicionou:

  - GET /api/irai/series: cada barra da série carrega os 15 campos NWE, com os
    tipos certos (floats opcionais podem ser None; `nwe_direction` é str "up"/
    "down"; os `*_available` são bool; `nwe_slope_price` é number).
  - GET /api/irai/overview: cada target expõe nwe_direction / nwe_slope_price /
    nwe_center / nwe_upper / nwe_lower / nwe_available e NÃO expõe mais a chave
    ambígua `nwe_slope` sem sufixo (decisão deliberada — o nome já teve 3
    semânticas conflitantes; ver backend/irai/engine.py:109).

Falharia ANTES desta task: `_snap_to_dict` não serializava nenhum campo NWE, e o
overview publicava `nwe_slope` a partir de um kernel O(n²) NÃO causal inline.

Chama as funções de rota diretamente (asyncio.run), como test_api_brt_offset.py —
sem subir servidor/MT5. Requer fastapi; sem ele, faz skip (o runtime é Windows,
deps instaladas ad hoc — ver CLAUDE.md).
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


# Os 15 campos NWE do contrato (canônicos — espelham NWE_FIELDS de
# tests/test_nwe_causality.py e o dataclass de engine.py).
OPTIONAL_FLOAT_KEYS = [
    "nwe_center_price", "nwe_upper_price", "nwe_lower_price",
    "nwe_center", "nwe_upper", "nwe_lower",
    "atr_14", "session_vwap", "distance_to_nwe_atr", "distance_to_vwap_atr",
]
BOOL_KEYS = ["nwe_available", "atr_available", "vwap_available"]
ALL_NWE_KEYS = OPTIONAL_FLOAT_KEYS + BOOL_KEYS + ["nwe_slope_price", "nwe_direction"]

# Subconjunto que o overview (grid multi-asset) publica por target.
OVERVIEW_NWE_KEYS = [
    "nwe_direction", "nwe_slope_price", "nwe_center",
    "nwe_upper", "nwe_lower", "nwe_available",
]


def _skip_if_no_fastapi():
    if _HAS_FASTAPI:
        return False
    try:
        import pytest
        pytest.skip("fastapi não instalado neste ambiente")
    except ModuleNotFoundError:
        pass
    return True


def _is_number(v):
    # bool é subclasse de int em Python — não conta como number aqui.
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _assert_series_bar_typed(bar):
    for k in ALL_NWE_KEYS:
        assert k in bar, f"campo NWE ausente na série: {k}"
    for k in OPTIONAL_FLOAT_KEYS:
        assert bar[k] is None or _is_number(bar[k]), \
            f"{k} deveria ser float|None, veio {bar[k]!r}"
    for k in BOOL_KEYS:
        assert isinstance(bar[k], bool), f"{k} deveria ser bool, veio {bar[k]!r}"
    assert _is_number(bar["nwe_slope_price"]), \
        f"nwe_slope_price deveria ser number, veio {bar['nwe_slope_price']!r}"
    # "flat" (slope≈0) e None (nwe_available=False) são estados legítimos —
    # não um tie-break silencioso pra "up" (achado B1#3 da tri-review).
    assert bar["nwe_direction"] in ("up", "down", "flat", None), \
        f"nwe_direction inválido: {bar['nwe_direction']!r}"
    if bar["nwe_direction"] is None:
        assert bar["nwe_available"] is False, \
            "nwe_direction None mas nwe_available=True — inconsistente"


def _seed(db_path, *, target, source, session_start_h, session, n_bars):
    """Semeia 1 target + 1 fator global alinhado, com `n_bars` barras M5 a partir
    de 09:00. accuracy é setado (o overview pula targets não calibrados)."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.close()
    migrate_divergence_config(str(db_path))

    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO asset_models
           (target, slug, display_name, icon, factors, factor_labels,
            session_start_h, session_end_h, active, accuracy, divergence_config)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        (target, "fx", "Fixture", "F",
         json.dumps(["US500"]), json.dumps({"US500": "f"}),
         session_start_h, 18 if session_start_h else 24, 0.55,
         json.dumps({"use_johansen": False})),
    )
    for name, value in (("fx_alpha", 1.0), ("fx_intercept", 0.0),
                        ("fx_w_f", 1.0), ("fx_sigma_f", 0.01)):
        conn.execute(
            "INSERT INTO model_params (param_name, value, effective_from) VALUES (?, ?, ?)",
            (name, value, "2020-01-01"))

    def insert(symbol, source_, ts, price):
        conn.execute(
            """INSERT INTO market_bars
               (symbol, source, timeframe, timestamp_utc, open, high, low, close,
                volume, real_volume, delta)
               VALUES (?, ?, 'M5', ?, ?, ?, ?, ?, 10, 10, 0)""",
            (symbol, source_, ts, price, price + 3, price - 3, price))

    for k in range(n_bars):
        total_min = 9 * 60 + k * 5
        hh, mm = divmod(total_min, 60)
        ts = f"{session}T{hh:02d}:{mm:02d}:00Z"
        insert(target, source, ts, 30000.0 + k * 10.0)  # target: walk suave
        insert("US500", "tickmill", ts, 10.0)           # fator alinhado 1:1
    conn.commit()
    conn.close()


def _with_engine(db_path, fn):
    """Roda `fn()` com a engine/DB da fixture plugados no módulo da API."""
    orig_engine = api_main.engine
    orig_conn = api_main.get_connection
    api_main.engine = IRAIEngine(db_path=str(db_path))
    api_main.get_connection = lambda: db_mod.get_connection(str(db_path))
    api_main.series_cache.clear()
    api_main.overview_cache_data.clear()
    try:
        return fn()
    finally:
        api_main.engine = orig_engine
        api_main.get_connection = orig_conn


def _run_series(*, target, source, session_start_h, session, n_bars):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "irai.db")
        _seed(db_path, target=target, source=source,
              session_start_h=session_start_h, session=session, n_bars=n_bars)

        def call():
            resp = asyncio.run(api_main.irai_series(
                session_date=session, target=target, version="v2"))
            return json.loads(resp.body) if hasattr(resp, "body") else resp
        return _with_engine(db_path, call)


def _run_overview(*, target, source, session_start_h, session, n_bars):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "irai.db")
        _seed(db_path, target=target, source=source,
              session_start_h=session_start_h, session=session, n_bars=n_bars)

        def call():
            return asyncio.run(api_main.irai_overview(
                session_date=session, version="v2"))
        return _with_engine(db_path, call)


def test_series_expoe_15_campos_nwe_por_barra():
    if _skip_if_no_fastapi():
        return
    data = _run_series(target="US30", source="tickmill", session_start_h=0,
                       session="2026-01-15", n_bars=20)
    assert "series" in data and data["series"], "série vazia — fixture não respondeu"

    for bar in data["series"]:
        _assert_series_bar_typed(bar)

    # Não é só default: o wiring da engine produz valores reais em barras reais.
    assert any(b["nwe_available"] for b in data["series"]), \
        "nenhuma barra com nwe_available=True — engine não enriqueceu"
    assert any(_is_number(b["nwe_center_price"]) for b in data["series"]), \
        "nenhum nwe_center_price numérico"

    # Serialização estrita: o payload não pode conter NaN/Infinity.
    json.dumps(data["series"], allow_nan=False)


def test_series_expoe_ohlc_real_da_barra_sem_reusar_abertura_da_sessao():
    """O ledger tático precisa do OHLC M5 real para medir MFE/MAE e stops.

    ``win_open`` continua sendo a âncora de abertura da sessão. O contrato
    aditivo ``win_bar_open``/``win_high``/``win_low`` expõe a barra corrente
    sem alterar essa semântica histórica.
    """
    if _skip_if_no_fastapi():
        return
    data = _run_series(target="US30", source="tickmill", session_start_h=0,
                       session="2026-01-15", n_bars=3)

    first = next(bar for bar in data["series"] if not bar["is_ghost"])
    assert first["win_open"] == 30000.0
    assert first["win_bar_open"] == 30000.0
    assert first["win_high"] == 30003.0
    assert first["win_low"] == 29997.0
    assert first["win_current"] == 30000.0


def test_overview_expoe_nwe_causal_sem_slope_ambiguo():
    if _skip_if_no_fastapi():
        return
    data = _run_overview(target="US30", source="tickmill", session_start_h=0,
                         session="2026-01-15", n_bars=20)
    assert data.get("targets"), "overview sem targets — fixture não respondeu"

    for card in data["targets"]:
        for k in OVERVIEW_NWE_KEYS:
            assert k in card, f"campo NWE ausente no overview: {k}"
        # A chave ambígua legada NÃO pode voltar.
        assert "nwe_slope" not in card, \
            "overview reintroduziu a chave ambígua `nwe_slope` sem sufixo"
        assert card["nwe_direction"] in ("up", "down", "flat", None)
        if card["nwe_direction"] is None:
            assert card["nwe_available"] is False, \
                "nwe_direction None mas nwe_available=True — inconsistente"
        assert isinstance(card["nwe_available"], bool)
        assert _is_number(card["nwe_slope_price"])

    # Não é só default: o wiring da engine produz valor real no overview
    # também — sem isto, o teste passaria mesmo se o overview nunca recebesse
    # dado real (achado B2#4 da tri-review, o teste da série já tinha essa
    # guarda, o do overview não).
    assert any(c["nwe_available"] for c in data["targets"]), \
        "nenhum card com nwe_available=True — overview não enriqueceu"

    json.dumps(data, allow_nan=False)


if __name__ == "__main__":
    if not _HAS_FASTAPI:
        print("SKIP: fastapi não instalado")
        sys.exit(0)
    for fn in (test_series_expoe_15_campos_nwe_por_barra,
               test_overview_expoe_nwe_causal_sem_slope_ambiguo):
        fn()
        print(f"ok  {fn.__name__}")
