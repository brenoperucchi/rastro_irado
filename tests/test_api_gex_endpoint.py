"""Spec do endpoint GET /api/irai/gex — Pacote B/GEX-DOL, Slice 4.

O worker (gex_worker.py) já persiste WIN$N e WDO$N na mesma tabela
`gex_levels`, chaveada por (session_date, target) -- Slice 3. O endpoint em
backend/api/main.py já era genérico por `target` desde a feature original
(commits 4dd1273/d455105, anteriores a este projeto de extensão pro dólar)
-- não precisou de mudança de código pra Slice 4. Este spec trava as
invariantes que o endpoint PRECISA manter agora que existe mais de um
target real na tabela:

  1. Isolamento por target: pedir WDO$N nunca pode devolver a linha de
     WIN$N (ou vice-versa) -- são pregões/gammas totalmente diferentes.
  2. Sem dado nenhum daquele target -> `active=False` explícito, nunca 500.
  3. Tabela gex_levels nem existe ainda (worker nunca rodou) -> mesmo
     `active=False` gracioso, não uma OperationalError vazando pro cliente.
  4. Freshness: `valid=True` no banco não basta -- dado com mais de 4 dias
     corridos tem que virar active=False (o frontend nunca pode plotar GEX
     velho como se fosse do pregão corrente).
  5. `valid=False` no banco (poucos strikes líquidos etc.) nunca vira
     active=True, mesmo fresco.

Chama a função da rota diretamente (await api_main.get_gex(target=...)),
não via TestClient/ASGI -- `target: str = Query("WIN$N")` só resolve pro
valor default de fato através do parsing de request do FastAPI; chamada
direta em Python puro bindaria `target` ao objeto Query(...) em vez da
string "WIN$N". Por isso todo teste aqui passa `target=` explícito; o
comportamento de default de query param é wiring do framework, não lógica
deste endpoint, e não muda com a extensão pro WDO$N.

Roda sem pytest: python3 tests/test_api_gex_endpoint.py -- requer fastapi
instalado (a API não sobe nesta máquina Linux de dev sem ele; ver
CLAUDE.md "This is a Windows-only runtime", deps "instaladas ad hoc"). Se
fastapi não estiver disponível, avisa e sai 0 (skip) -- ausência de
dependência opcional neste ambiente de dev, não uma regressão do endpoint.
"""
import os
import sys
import types
import asyncio
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _mod, _attr, _val in (("pykalman", "KalmanFilter", object),):
    try:
        __import__(_mod)
    except ModuleNotFoundError:
        _st = types.ModuleType(_mod)
        setattr(_st, _attr, _val)
        sys.modules[_mod] = _st
try:
    import statsmodels  # noqa: F401
except ModuleNotFoundError:
    for _sub in ("statsmodels", "statsmodels.tsa", "statsmodels.tsa.vector_ar",
                 "statsmodels.tsa.vector_ar.vecm"):
        sys.modules[_sub] = types.ModuleType(_sub)
    sys.modules["statsmodels.tsa.vector_ar.vecm"].coint_johansen = lambda *a, **k: None

try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ModuleNotFoundError:
    _HAS_FASTAPI = False

if _HAS_FASTAPI:
    import backend.db as db_mod
    import backend.api.main as api_main
    import backend.workers.gex_worker as gex_worker
    from scripts.backfill_gex_history import save_history_result


def _skip_without_fastapi():
    """Review codex: sem isto, um `pytest tests/` coletando este arquivo
    diretamente (ignorando a lista TESTS/_HAS_FASTAPI do runner manual)
    chamaria cada test_* mesmo sem fastapi instalado -- NameError em
    db_mod/api_main/gex_worker, lido como falha real em vez de ausência de
    dependência opcional neste ambiente de dev. Retorna True se o teste
    deve ser abortado (pytest.skip quando disponível, no-op silencioso no
    runner manual, que já filtra `_HAS_FASTAPI` antes de popular TESTS)."""
    if _HAS_FASTAPI:
        return False
    try:
        import pytest
        pytest.skip("fastapi não instalado neste ambiente")
    except ModuleNotFoundError:
        pass
    return True


def _mk_gex_result(spot=5400.0, valid=True, walls=None, gamma_flip=5400.0):
    return {
        "gamma_max_ibov": 1.0, "gamma_min_ibov": -1.0, "gamma_flip_ibov": 0.0,
        "gamma_max": spot + 100, "gamma_min": spot - 100, "gamma_flip": gamma_flip,
        "spot": spot, "future_settle": spot + 1.0, "conv_factor": 1.0,
        "n_strikes": 5, "valid": valid,
        "walls": walls if walls is not None else [{"strike": spot, "kind": "flip"}],
        "meta": {"iv_by_exp": {}, "iv_fallback": 0.2, "iv_source": "realized",
                 "grid_step": 50.0, "risk_free": 0.0},
    }


def _mk_result_com_grade(spot, gamma_flip_ibov, conv_factor=1.0, grid_step=50.0, valid=True):
    """F1: helper que deixa a grade de 17 walls ser regenerada de verdade por
    `_current_gex_walls`/`build_walls` a partir de `meta.grid_step` (mesmo
    caminho de um snapshot real), em vez de fabricar `walls` manualmente --
    assim os testes de flip_grid_signal exercitam a MESMA geometria que o
    endpoint calcula em produção."""
    gamma_flip = gamma_flip_ibov * conv_factor
    return {
        "gamma_max_ibov": spot + 1000, "gamma_min_ibov": spot - 1000,
        "gamma_flip_ibov": gamma_flip_ibov,
        "gamma_max": (spot + 1000) * conv_factor, "gamma_min": (spot - 1000) * conv_factor,
        "gamma_flip": gamma_flip,
        "spot": spot, "future_settle": spot * conv_factor, "conv_factor": conv_factor,
        "n_strikes": 20, "valid": valid,
        "walls": [],
        "meta": {"grid_step": grid_step},
    }


def _patch_get_connection(db_path):
    orig = api_main.get_connection
    api_main.get_connection = lambda: db_mod.get_connection(db_path)
    return orig


def _restore_get_connection(orig):
    api_main.get_connection = orig


def test_get_gex_isola_por_target_win_e_dol_nao_se_misturam():
    """WIN$N e WDO$N convivem na MESMA tabela (PK composta session_date+target)
    -- pedir um não pode nunca devolver o dado do outro."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    today = date.today().isoformat()
    gex_worker.save(conn, today, _mk_gex_result(spot=130000.0, gamma_flip=130500.0), target="WIN$N")
    gex_worker.save(conn, today, _mk_gex_result(spot=5400.0, gamma_flip=5410.0), target="WDO$N")
    conn.close()

    orig = _patch_get_connection(path)
    try:
        win = asyncio.run(api_main.get_gex(target="WIN$N"))
        dol = asyncio.run(api_main.get_gex(target="WDO$N"))
    finally:
        _restore_get_connection(orig)

    assert win["target"] == "WIN$N" and win["gamma_flip"] == 130500.0, win
    assert dol["target"] == "WDO$N" and dol["gamma_flip"] == 5410.0, dol
    assert win["gamma_flip"] != dol["gamma_flip"]


def test_get_gex_sem_dado_do_target_pedido_retorna_active_false():
    """A tabela TEM dado -- só não do target pedido (ex.: WDO$N ainda não
    rodou, só WIN$N). Não pode vazar o dado do outro target por engano nem
    quebrar."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    gex_worker.save(conn, date.today().isoformat(), _mk_gex_result(), target="WIN$N")
    conn.close()

    orig = _patch_get_connection(path)
    try:
        result = asyncio.run(api_main.get_gex(target="WDO$N"))
    finally:
        _restore_get_connection(orig)

    assert result == {"active": False, "reason": "sem dados de GEX"}, result


def test_get_gex_tabela_inexistente_nao_vira_erro_500():
    """gex_worker nunca rodou -- gex_levels nem existe. OperationalError
    'no such table' tem que virar 'sem dados', não uma exceção pro cliente."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    db_mod.get_connection(path).close()  # cria o arquivo, sem nenhuma tabela

    orig = _patch_get_connection(path)
    try:
        result = asyncio.run(api_main.get_gex(target="WDO$N"))
    finally:
        _restore_get_connection(orig)

    assert result == {"active": False, "reason": "sem dados de GEX"}, result


def test_get_gex_dado_velho_fica_active_false_mesmo_com_valid_true():
    """valid=True no banco não basta -- dado de >4 dias corridos não pode
    ser plotado como se fosse do pregão corrente (o frontend confia
    cegamente em `active`, não recalcula freshness)."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    stale = (date.today() - timedelta(days=10)).isoformat()
    gex_worker.save(conn, stale, _mk_gex_result(valid=True), target="WDO$N")
    conn.close()

    orig = _patch_get_connection(path)
    try:
        result = asyncio.run(api_main.get_gex(target="WDO$N"))
    finally:
        _restore_get_connection(orig)

    assert result["valid"] is True, "o dado em si é válido"
    assert result["active"] is False, (
        "dado de 10 dias atrás não pode ficar active -- é gamma wall velho", result)


def test_get_gex_fresco_e_valido_fica_active_true():
    """Caminho feliz: dado de hoje, válido, com walls -- active tem que
    ser True (é o gate que o toggle do frontend usa pra habilitar o botão)."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    gex_worker.save(conn, date.today().isoformat(), _mk_gex_result(valid=True), target="WDO$N")
    conn.close()

    orig = _patch_get_connection(path)
    try:
        result = asyncio.run(api_main.get_gex(target="WDO$N"))
    finally:
        _restore_get_connection(orig)

    assert result["active"] is True, result
    assert result["age_days"] == 0, result


def test_get_gex_invalido_no_banco_fica_active_false_mesmo_fresco():
    """compute_gex marcou valid=False (ex.: poucos strikes líquidos) -- dado
    de hoje não é suficiente pra active=True se o worker já sinalizou que
    não confia nesse GEX."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    gex_worker.save(conn, date.today().isoformat(), _mk_gex_result(valid=False), target="WDO$N")
    conn.close()

    orig = _patch_get_connection(path)
    try:
        result = asyncio.run(api_main.get_gex(target="WDO$N"))
    finally:
        _restore_get_connection(orig)

    assert result["valid"] is False, result
    assert result["active"] is False, (
        "compute_gex marcou como inválido -- não pode virar active mesmo fresco", result)


def test_get_gex_pega_o_pregao_mais_recente_do_target():
    """Review codex: o mesmo target pode ter mais de uma linha (WIN$N e
    WDO$N rodam todo dia útil). `ORDER BY session_date DESC LIMIT 1` tem
    que trazer a mais recente, não a primeira inserida nem a mais antiga."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    anteontem = (date.today() - timedelta(days=2)).isoformat()
    ontem = (date.today() - timedelta(days=1)).isoformat()
    hoje = date.today().isoformat()
    # Review codex: "hoje" (a resposta certa) tem que ser inserido no MEIO,
    # nem primeiro nem último -- se fosse o primeiro inserido, um SELECT sem
    # ORDER BY (SQLite tende a devolver ordem de inserção) acertaria por
    # coincidência mesmo com uma regressão que removesse o ORDER BY do
    # endpoint. Só `ORDER BY session_date DESC LIMIT 1` de verdade acerta
    # aqui, não importa a ordem de inserção.
    gex_worker.save(conn, anteontem, _mk_gex_result(gamma_flip=4000.0), target="WDO$N")
    gex_worker.save(conn, hoje, _mk_gex_result(gamma_flip=5500.0), target="WDO$N")
    gex_worker.save(conn, ontem, _mk_gex_result(gamma_flip=5000.0), target="WDO$N")
    conn.close()

    orig = _patch_get_connection(path)
    try:
        result = asyncio.run(api_main.get_gex(target="WDO$N"))
    finally:
        _restore_get_connection(orig)

    assert result["as_of"] == hoje, result
    assert result["gamma_flip"] == 5500.0, result


def test_get_gex_regera_grid_legado_no_spot_sem_alterar_niveis_gamma():
    """Walls armazenadas sob a antiga âncora no Flip não podem sobreviver.

    O snapshot mantém GammaMax/Flip/Min, spot e basis originais. Só o grid
    derivado é refeito ao redor do spot, para que um JSON salvo antes da
    correção não volte a desenhar todas as linhas de um lado do preço.
    """
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    legacy = {
        "gamma_max_ibov": 110.0, "gamma_min_ibov": 100.0,
        "gamma_flip_ibov": 129.0,
        "gamma_max": 110.0, "gamma_min": 100.0, "gamma_flip": 129.0,
        "spot": 105.0, "future_settle": 105.0, "conv_factor": 1.0,
        "n_strikes": 30, "valid": True,
        "walls": [
            {"type": "wall", "price": price, "color": "#EF4444",
             "style": "solid", "width": 1}
            for price in range(121, 138)
        ],
        "meta": {"grid_step": 1.0},
    }
    gex_worker.save(conn, date.today().isoformat(), legacy, target="WIN$N")
    conn.close()

    orig = _patch_get_connection(path)
    try:
        result = asyncio.run(api_main.get_gex(target="WIN$N"))
    finally:
        _restore_get_connection(orig)

    grid = [wall["price"] for wall in result["walls"] if wall["type"] == "wall"]
    assert (min(grid), max(grid)) == (97, 113), result
    assert result["gamma_flip"] == 129.0, result


def test_get_gex_historico_regera_grid_legado_no_spot():
    """O mesmo reparo visual vale para JSON histórico já serializado."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    legacy = {
        "gamma_max_ibov": 110.0, "gamma_min_ibov": 100.0,
        "gamma_flip_ibov": 129.0,
        "gamma_max": 110.0, "gamma_min": 100.0, "gamma_flip": 129.0,
        "spot": 105.0, "future_settle": 105.0, "conv_factor": 1.0,
        "n_strikes": 30, "valid": True,
        "walls": [
            {"type": "wall", "price": price, "color": "#EF4444",
             "style": "solid", "width": 1}
            for price in range(121, 138)
        ],
        "meta": {"grid_step": 1.0},
    }
    save_history_result(
        conn, "2026-07-15", "2026-07-16", legacy, target="WIN$N",
    )
    conn.close()

    orig = _patch_get_connection(path)
    try:
        result = asyncio.run(
            api_main.get_gex(target="WIN$N", session_date="2026-07-16")
        )
    finally:
        _restore_get_connection(orig)

    grid = [wall["price"] for wall in result["walls"] if wall["type"] == "wall"]
    assert (min(grid), max(grid)) == (97, 113), result


def test_get_gex_historico_usa_data_efetiva_e_nunca_recua_para_o_live():
    """Uma sessão histórica usa o snapshot PIT disponível naquele pregão.

    O EOD de 2026-07-15 só ficou disponível para operar em 2026-07-16. Mesmo
    com um GEX live diferente no banco, pedir 2026-07-16 precisa devolver o
    registro de ``gex_history_levels``; misturar o último live torna as walls
    de um gráfico histórico metodologicamente falsas.
    """
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    gex_worker.save(
        conn, date.today().isoformat(),
        _mk_gex_result(spot=190000.0, gamma_flip=191000.0), target="WIN$N",
    )
    historical = _mk_gex_result(spot=175000.0, gamma_flip=176000.0)
    save_history_result(
        conn, "2026-07-15", "2026-07-16", historical, target="WIN$N",
    )
    conn.close()

    orig = _patch_get_connection(path)
    try:
        result = asyncio.run(
            api_main.get_gex(target="WIN$N", session_date="2026-07-16")
        )
    finally:
        _restore_get_connection(orig)

    assert result["active"] is True, result
    assert result["historical"] is True, result
    assert result["as_of"] == "2026-07-16", result
    assert result["source_as_of"] == "2026-07-15", result
    assert result["gamma_flip"] == 176000.0, result
    assert result["gamma_flip"] != 191000.0, result


def test_get_gex_historico_ausente_nao_vaza_ultimo_snapshot_live():
    """Ausência de snapshot PIT é ausência de GEX, nunca fallback live."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    gex_worker.save(
        conn, date.today().isoformat(),
        _mk_gex_result(spot=190000.0, gamma_flip=191000.0), target="WIN$N",
    )
    save_history_result(
        conn, "2026-07-15", "2026-07-16", _mk_gex_result(gamma_flip=176000.0),
        target="WIN$N",
    )
    conn.close()

    orig = _patch_get_connection(path)
    try:
        result = asyncio.run(
            api_main.get_gex(target="WIN$N", session_date="2026-07-17")
        )
    finally:
        _restore_get_connection(orig)

    assert result == {
        "active": False,
        "historical": True,
        "reason": "sem dados de GEX para a data selecionada",
    }, result


def test_get_gex_historico_valido_antigo_permanece_ativo():
    """O limite de quatro dias pertence somente ao endpoint live."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    save_history_result(
        conn, "2026-06-15", "2026-06-16", _mk_gex_result(gamma_flip=176000.0),
        target="WIN$N",
    )
    conn.close()

    orig = _patch_get_connection(path)
    try:
        result = asyncio.run(
            api_main.get_gex(target="WIN$N", session_date="2026-06-16")
        )
    finally:
        _restore_get_connection(orig)

    assert result["historical"] is True, result
    assert result["age_days"] is None, result
    assert result["active"] is True, result


def test_get_gex_limite_exato_de_4_dias_fresco_5_dias_nao():
    """Review codex: trava o limite exato do gate de freshness (0<=age<=4).
    age=4 ainda é `active` (cobre fim de semana + feriado, doc no endpoint);
    age=5 já não é."""
    if _skip_without_fastapi():
        return
    path4 = tempfile.mktemp(suffix=".db")
    conn4 = db_mod.get_connection(path4)
    d4 = (date.today() - timedelta(days=4)).isoformat()
    gex_worker.save(conn4, d4, _mk_gex_result(valid=True), target="WDO$N")
    conn4.close()

    path5 = tempfile.mktemp(suffix=".db")
    conn5 = db_mod.get_connection(path5)
    d5 = (date.today() - timedelta(days=5)).isoformat()
    gex_worker.save(conn5, d5, _mk_gex_result(valid=True), target="WDO$N")
    conn5.close()

    orig = _patch_get_connection(path4)
    try:
        r4 = asyncio.run(api_main.get_gex(target="WDO$N"))
    finally:
        _restore_get_connection(orig)

    orig = _patch_get_connection(path5)
    try:
        r5 = asyncio.run(api_main.get_gex(target="WDO$N"))
    finally:
        _restore_get_connection(orig)

    assert r4["age_days"] == 4 and r4["active"] is True, r4
    assert r5["age_days"] == 5 and r5["active"] is False, r5


def test_get_gex_historico_reflete_dupla_gravacao_do_worker_main():
    """F4 (tri-r): fecha o loop worker -> endpoint. Antes desta mudança só o
    backfill manual gravava gex_history_levels -- o worker EOD agendado
    (gex_worker.main()) nunca escrevia lá, então uma sessão recém-fechada
    ficava sem histórico até alguém rodar o backfill à mão. Roda main() de
    ponta a ponta (save()/save_history_result() reais, só as fontes
    externas mockadas) e confere que o endpoint histórico enxerga
    exatamente o snapshot que o worker gravou -- e que WDO$N (sem PIT
    source/effective) nunca aparece no histórico, mesmo tendo live salvo."""
    if _skip_without_fastapi():
        return
    db_path = tempfile.mktemp(suffix=".db")

    class _FakeMT5Handle:
        def shutdown(self):
            pass

    def fake_result(spot, future_settle, gamma_flip=None):
        return {"gamma_max_ibov": 1.0, "gamma_min_ibov": -1.0, "gamma_flip_ibov": 0.0,
                "gamma_max": spot + 100, "gamma_min": spot - 100,
                "gamma_flip": gamma_flip if gamma_flip is not None else spot,
                "spot": spot, "future_settle": future_settle, "conv_factor": 1.0,
                "n_strikes": 5, "liquid_strikes": 5, "valid": True, "walls": [],
                "meta": {"iv_by_exp": {}, "iv_fallback": 0.2, "iv_source": "realized",
                         "grid_step": 50.0, "risk_free": 0.0}}

    gw = gex_worker
    orig = dict(
        load_mt5_terminal=gw.load_mt5_terminal,
        _observed_win_session_pair=gw._observed_win_session_pair,
        _validate_official_source=gw._validate_official_source,
        _official_rate=gw._official_rate,
        compute_official_win_snapshot=gw.compute_official_win_snapshot,
        last_session_with_oi=gw.last_session_with_oi, fetch_bdi_oi=gw.fetch_bdi_oi,
        fetch_bdi_option_data=gw.fetch_bdi_option_data,
        fetch_ibov_mt5_leg=gw.fetch_ibov_mt5_leg, fetch_dol_mt5_leg=gw.fetch_dol_mt5_leg,
        infer_grid_step=gw.infer_grid_step, realized_iv_by_expiry=gw.realized_iv_by_expiry,
        compute_gex=gw.compute_gex, urlopen=gw.urllib.request.urlopen, argv=sys.argv,
    )
    gw.load_mt5_terminal = lambda: _FakeMT5Handle()
    gw._observed_win_session_pair = lambda conn, effective: ("2026-07-15", effective)
    gw._validate_official_source = lambda source, cache_dir: source
    gw._official_rate = lambda source, cache_dir: (source, 0.149)
    gw.compute_official_win_snapshot = (
        lambda *a, **k: fake_result(175000.0, 176500.0, gamma_flip=176000.0))
    gw.last_session_with_oi = lambda max_back=5: (
        "2026-07-15", [{"ticker": "IBOVFAKE", "oi": 100.0}])
    gw.fetch_bdi_oi = lambda session_date, asset="IBOV": [{"ticker": "DOLFAKE", "oi": 50.0}]
    gw.fetch_bdi_option_data = lambda oi_rows, session_date, asset: [
        {"ticker": "DOLFAKEOPT", "oi": 50.0, "strike": 5400.0,
         "is_call": True, "expiry": "2026-08-21", "premium": None}]
    gw.fetch_ibov_mt5_leg = lambda mt5, oi_rows, session_date, trust_session_close=True: {
        "spot": 175000.0, "win_settle": 176500.0, "options": []}
    gw.fetch_dol_mt5_leg = lambda mt5, session_date: {"spot": 5400.0, "future_settle": 5401.0}
    gw.infer_grid_step = lambda options, spot, default=None: 50.0
    gw.realized_iv_by_expiry = (
        lambda conn, symbol, session_date, expiries, min_window=10, max_window=60: {})
    gw.compute_gex = (
        lambda spot, future_settle, options, session_date, **kw: fake_result(spot, future_settle))
    gw.urllib.request.urlopen = lambda req, timeout=2: None
    sys.argv = ["gex_worker.py", "--db", db_path]
    try:
        exit_code = gw.main()
    finally:
        for k, v in orig.items():
            if k == "urlopen":
                gw.urllib.request.urlopen = v
            elif k == "argv":
                sys.argv = v
            else:
                setattr(gw, k, v)

    assert exit_code == 0, exit_code
    effective = date.today().isoformat()

    orig_conn = _patch_get_connection(db_path)
    try:
        win_hist = asyncio.run(api_main.get_gex(target="WIN$N", session_date=effective))
        dol_hist = asyncio.run(api_main.get_gex(target="WDO$N", session_date=effective))
    finally:
        _restore_get_connection(orig_conn)

    assert win_hist["active"] is True, win_hist
    assert win_hist["historical"] is True, win_hist
    assert win_hist["as_of"] == effective, win_hist
    assert win_hist["source_as_of"] == "2026-07-15", win_hist
    assert win_hist["gamma_flip"] == 176000.0, win_hist
    assert dol_hist == {
        "active": False, "historical": True,
        "reason": "sem dados de GEX para a data selecionada",
    }, "WDO$N não tem PIT source/effective -- worker não pode gravar histórico pra ele: " + repr(dol_hist)


def test_get_gex_flip_dentro_da_grade_nao_sinaliza():
    """F1: Flip dentro da faixa das 17 walls (centrada no spot) não precisa
    de sinal -- o trader já vê a linha do Flip no chart normalmente."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    # grade: spot=130000, grid_step=50 -> [129600, 130400]; flip=130100 cai dentro.
    result = _mk_result_com_grade(spot=130000.0, gamma_flip_ibov=130100.0, grid_step=50.0)
    gex_worker.save(conn, date.today().isoformat(), result, target="WIN$N")
    conn.close()

    orig = _patch_get_connection(path)
    try:
        r = asyncio.run(api_main.get_gex(target="WIN$N"))
    finally:
        _restore_get_connection(orig)

    assert r["flip_grid_signal"] == {
        "outside_grid": False, "direction": None, "distance_to_spot": None,
    }, r


def test_get_gex_flip_acima_da_grade_sinaliza_direcao_e_distancia():
    """F1: Flip 1000pts acima do spot, fora da faixa de +-400 (grid_step=50,
    8 níveis pra cada lado) -- sinal tem que apontar 'above' e a distância
    exata ao spot, sem alterar o próprio gamma_flip nem a cor das walls
    (F1 é só visualização, não pode mexer no cálculo)."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    result = _mk_result_com_grade(spot=130000.0, gamma_flip_ibov=131000.0, grid_step=50.0)
    gex_worker.save(conn, date.today().isoformat(), result, target="WIN$N")
    conn.close()

    orig = _patch_get_connection(path)
    try:
        r = asyncio.run(api_main.get_gex(target="WIN$N"))
    finally:
        _restore_get_connection(orig)

    grid = [w["price"] for w in r["walls"] if w["type"] == "wall"]
    assert r["gamma_flip"] > max(grid), (
        "pré-condição do teste: Flip precisa estar de fato fora da grade", r)
    assert r["flip_grid_signal"] == {
        "outside_grid": True, "direction": "above", "distance_to_spot": 1000.0,
    }, r
    assert r["gamma_flip"] == 131000.0, (
        "F1 não pode alterar o próprio Gamma/Flip -- só sinalizar", r)


def test_get_gex_flip_abaixo_da_grade_sinaliza_direcao_e_distancia():
    """Mesma verificação para o lado 'below' -- direção não pode ficar
    hardcoded/assumida de um único caso."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    result = _mk_result_com_grade(spot=130000.0, gamma_flip_ibov=128500.0, grid_step=50.0)
    gex_worker.save(conn, date.today().isoformat(), result, target="WIN$N")
    conn.close()

    orig = _patch_get_connection(path)
    try:
        r = asyncio.run(api_main.get_gex(target="WIN$N"))
    finally:
        _restore_get_connection(orig)

    grid = [w["price"] for w in r["walls"] if w["type"] == "wall"]
    assert r["gamma_flip"] < min(grid), (
        "pré-condição do teste: Flip precisa estar de fato fora da grade", r)
    assert r["flip_grid_signal"] == {
        "outside_grid": True, "direction": "below", "distance_to_spot": 1500.0,
    }, r


def test_get_gex_flip_grid_signal_com_conv_factor_nao_trivial_usa_espaco_correto():
    """F1-CONV (revisão tri-r, fable-reasoner): os demais testes de F1 usam
    conv_factor=1.0, que mascara qualquer troca acidental de espaço de preço
    (IBOV vs futuro) -- com conv_factor=1 o bug e o valor correto coincidem
    numericamente. Fixa conv_factor=1.05 e confere o valor exato de
    distance_to_spot no espaço do futuro (o mesmo das walls), não no IBOV
    bruto."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    result = _mk_result_com_grade(
        spot=130000.0, gamma_flip_ibov=131000.0, conv_factor=1.05, grid_step=50.0)
    gex_worker.save(conn, date.today().isoformat(), result, target="WIN$N")
    conn.close()

    orig = _patch_get_connection(path)
    try:
        r = asyncio.run(api_main.get_gex(target="WIN$N"))
    finally:
        _restore_get_connection(orig)

    grid = [w["price"] for w in r["walls"] if w["type"] == "wall"]
    assert r["gamma_flip"] > max(grid), (
        "pré-condição: Flip precisa estar fora da grade em espaço futuro", r)
    # spot em espaço futuro: 130000*1.05 = 136500.0; flip: 131000*1.05 = 137550.0
    assert r["flip_grid_signal"] == {
        "outside_grid": True, "direction": "above", "distance_to_spot": 1050.0,
    }, r


def test_get_gex_flip_na_borda_exata_da_grade_conta_como_dentro():
    """F1-BORDA (revisão tri-r, fable-reasoner): a comparação é
    `grid_min <= flip <= grid_max` (inclusiva) -- um Flip exatamente sobre o
    limite da grade não pode ser sinalizado como fora, senão o badge aparece
    coincidindo com a própria última wall visível no chart."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    result = _mk_result_com_grade(spot=130000.0, gamma_flip_ibov=130400.0, grid_step=50.0)
    gex_worker.save(conn, date.today().isoformat(), result, target="WIN$N")
    conn.close()

    orig = _patch_get_connection(path)
    try:
        r = asyncio.run(api_main.get_gex(target="WIN$N"))
    finally:
        _restore_get_connection(orig)

    grid = [w["price"] for w in r["walls"] if w["type"] == "wall"]
    assert r["gamma_flip"] == max(grid), (
        "pré-condição: Flip precisa cair exatamente na borda superior da grade", r)
    assert r["flip_grid_signal"] == {
        "outside_grid": False, "direction": None, "distance_to_spot": None,
    }, r


def test_get_gex_flip_grid_signal_none_sem_geometria_de_grade():
    """Snapshot legado sem `meta.grid_step` válido (<=0 ou ausente) cai no
    fallback de `_current_gex_walls` -- devolve o `walls` armazenado tal
    qual, que pode não ter nenhum item `type: 'wall'` (ex.: formato antigo).
    Sem geometria de grade pra comparar, o sinal precisa degradar
    graciosamente pra None, nunca quebrar o endpoint."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    legacy_sem_grid_step = _mk_gex_result(
        walls=[{"strike": 5400.0, "kind": "flip"}])
    legacy_sem_grid_step["meta"] = {"grid_step": 0}
    gex_worker.save(conn, date.today().isoformat(), legacy_sem_grid_step, target="WIN$N")
    conn.close()

    orig = _patch_get_connection(path)
    try:
        r = asyncio.run(api_main.get_gex(target="WIN$N"))
    finally:
        _restore_get_connection(orig)

    assert r["flip_grid_signal"] is None, r


def test_get_gex_historico_tambem_expoe_flip_grid_signal():
    """O sinal precisa valer tanto pro live quanto pro snapshot PIT histórico
    -- os dois passam pela mesma `_current_gex_walls`/`_flip_grid_signal`,
    sem caminho separado que possa divergir."""
    if _skip_without_fastapi():
        return
    path = tempfile.mktemp(suffix=".db")
    conn = db_mod.get_connection(path)
    result = _mk_result_com_grade(spot=130000.0, gamma_flip_ibov=131000.0, grid_step=50.0)
    save_history_result(conn, "2026-07-15", "2026-07-16", result, target="WIN$N")
    conn.close()

    orig = _patch_get_connection(path)
    try:
        r = asyncio.run(api_main.get_gex(target="WIN$N", session_date="2026-07-16"))
    finally:
        _restore_get_connection(orig)

    assert r["flip_grid_signal"] == {
        "outside_grid": True, "direction": "above", "distance_to_spot": 1000.0,
    }, r


TESTS = [
    test_get_gex_isola_por_target_win_e_dol_nao_se_misturam,
    test_get_gex_sem_dado_do_target_pedido_retorna_active_false,
    test_get_gex_tabela_inexistente_nao_vira_erro_500,
    test_get_gex_dado_velho_fica_active_false_mesmo_com_valid_true,
    test_get_gex_fresco_e_valido_fica_active_true,
    test_get_gex_invalido_no_banco_fica_active_false_mesmo_fresco,
    test_get_gex_pega_o_pregao_mais_recente_do_target,
    test_get_gex_regera_grid_legado_no_spot_sem_alterar_niveis_gamma,
    test_get_gex_historico_regera_grid_legado_no_spot,
    test_get_gex_historico_usa_data_efetiva_e_nunca_recua_para_o_live,
    test_get_gex_historico_ausente_nao_vaza_ultimo_snapshot_live,
    test_get_gex_historico_valido_antigo_permanece_ativo,
    test_get_gex_limite_exato_de_4_dias_fresco_5_dias_nao,
    test_get_gex_historico_reflete_dupla_gravacao_do_worker_main,
    test_get_gex_flip_dentro_da_grade_nao_sinaliza,
    test_get_gex_flip_acima_da_grade_sinaliza_direcao_e_distancia,
    test_get_gex_flip_abaixo_da_grade_sinaliza_direcao_e_distancia,
    test_get_gex_flip_grid_signal_com_conv_factor_nao_trivial_usa_espaco_correto,
    test_get_gex_flip_na_borda_exata_da_grade_conta_como_dentro,
    test_get_gex_flip_grid_signal_none_sem_geometria_de_grade,
    test_get_gex_historico_tambem_expoe_flip_grid_signal,
] if _HAS_FASTAPI else []


if __name__ == "__main__":
    if not _HAS_FASTAPI:
        print("SKIP: fastapi não instalado neste ambiente -- o endpoint da API "
              "não roda nesta máquina Linux de dev (ver CLAUDE.md). Não é uma "
              "regressão do endpoint; instale fastapi (ex.: venv local) pra "
              "rodar este spec.")
        sys.exit(0)
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(TESTS) - failures}/{len(TESTS)} passed")
    sys.exit(1 if failures else 0)
