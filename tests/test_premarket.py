"""Spec do gate de pré-mercado do engine (backend/irai/engine.py).

Ref: docs/plans/2026-07-10-frontend-migration-status-and-forward-plan.md — Fase 3.
Revisado por deep-reasoner + fable-reasoner + codex (3 lentes independentes).

REGRESSÃO. Os fatores globais negociam 24h; o target B3 só abre 09:00 BRT
(= 15:00 EEST no eixo normalizado). O loop itera a UNIÃO dos timestamps, então
há ~180 barras M5 antes da primeira barra do target — o "pré-mercado".

O BUG: `target_cursor` nascia em 0, então `is_pre_market = (target_cursor < 0)`
NUNCA disparava. Nessas barras o engine montava uma barra sintética com o
fechamento de ONTEM, mas ancorava em `opens[data_target]` = abertura de HOJE
(que nem existe ainda → lookahead). Resultado: win_ret = (ontem − hoje)/hoje,
um retorno FALSO e constante, que (a) ia pro payload, (b) era a OBSERVAÇÃO do
Kalman por ~180 barras, (c) entrava no price_history do Johansen.

Gravidade do (b): memória efetiva do filtro = √(R/Q) = √(1e-3/1e-5) = 10 barras.
180 barras envenenadas = 18 memórias ⇒ o prior é APAGADO. Os betas colapsam e o
intercepto é arrastado pra constante falsa — e como no v2 os betas VIRAM os
pesos do score, o P(↑) da abertura sai corrompido.

As invariantes que estes testes travam:
  1. No pré-mercado o payload não vaza a abertura de hoje (win_open == win_current
     == fechamento de ontem) e win_return == 0. Sem isso o frontend recomputa o
     retorno falso por conta própria (App.jsx:397 faz (win_current/win_open − 1)).
  2. O Kalman NUNCA recebe correção por observação antes da primeira barra real
     (predict-only: a média fica no prior, a covariância difunde).
  3. O Johansen não acumula o preço sintético do target.
  4. Ativos 24h não entram em pré-mercado por engano.

Roda sem pytest:  python3 tests/test_premarket.py
Ou com pytest:    pytest tests/test_premarket.py
Não exige pykalman/statsmodels: os testes de Kalman/Johansen usam spies.
"""
import os
import sys
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# pykalman só existe no runtime Windows; aqui os testes de Kalman usam spy no
# wrapper, então um stub basta para permitir o import do engine. No host (com
# pykalman de verdade) este bloco não faz nada.
import types

try:
    import pykalman  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("pykalman")
    _stub.KalmanFilter = object
    sys.modules["pykalman"] = _stub

try:
    import statsmodels  # noqa: F401
except ModuleNotFoundError:
    _sm = types.ModuleType("statsmodels")
    for _sub in ("statsmodels.tsa", "statsmodels.tsa.vector_ar",
                 "statsmodels.tsa.vector_ar.vecm"):
        sys.modules[_sub] = types.ModuleType(_sub)
    sys.modules["statsmodels"] = _sm
    sys.modules["statsmodels.tsa.vector_ar.vecm"].coint_johansen = lambda *a, **k: None

from backend.db import SCHEMA, migrate_divergence_config

TARGET = "WIN$N"
SLUG = "win"
FACTOR = "US500"
SESSION = "2026-07-10"

PREV_CLOSE = 100_000.0      # fechamento de ONTEM
TODAY_OPEN = 105_000.0      # abertura de HOJE (5% acima -> o retorno falso é gritante)
FACTOR_OPEN = 5_000.0


def _seed(db_path, target_is_b3=True):
    """Semeia um DB com 1 fator 24h e um target que abre tarde (ou 24h)."""
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    # O SCHEMA não cria `divergence_config`, mas _load_params a seleciona e o
    # except genérico engole o erro -> "0 models loaded". É a Fase 1 do plano;
    # aqui aplicamos a migração para o fixture refletir uma instalação correta.
    migrate_divergence_config(db_path)

    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute(
        """INSERT INTO asset_models
           (target, slug, display_name, factors, factor_labels,
            session_start_h, session_end_h, active)
           VALUES (?,?,?,?,?,?,?,1)""",
        (TARGET, SLUG, "Mini Índice", json.dumps([FACTOR]),
         json.dumps({FACTOR: "us500"}),
         9 if target_is_b3 else 0, 18 if target_is_b3 else 24),
    )
    for name, val in [
        (f"{SLUG}_alpha", 1.0), (f"{SLUG}_intercept", 0.0),
        (f"{SLUG}_w_us500", 2.0), (f"{SLUG}_sigma_us500", 0.01),
        (f"{SLUG}_kalman_trans_cov", 1e-5), (f"{SLUG}_kalman_obs_cov", 1e-3),
    ]:
        c.execute("INSERT INTO model_params (param_name, value, effective_from) VALUES (?,?,?)",
                  (name, val, "2020-01-01"))

    # ── barras ──
    # Fator (24h, EEST): 00:00 -> 20:00, a cada 5min. Preço SOBE (retorno real != 0).
    base = datetime.fromisoformat(f"{SESSION}T00:00:00")
    n_factor = 240   # 20h
    for i in range(n_factor):
        ts = (base + timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        px = FACTOR_OPEN * (1 + 0.0001 * i)   # sobe ~2.4% ao longo do dia
        c.execute("""INSERT INTO market_bars
                     (symbol, source, timeframe, timestamp_utc, open, high, low, close, volume, real_volume, delta)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  (FACTOR, "tickmill", "M5", ts, px, px, px, px, 10, 10, 0))

    # Fechamento de ONTEM do target (o engine busca `< session_date`)
    prev_ts = f"2026-07-09T21:00:00Z"
    c.execute("""INSERT INTO market_bars
                 (symbol, source, timeframe, timestamp_utc, open, high, low, close, volume, real_volume, delta)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
              (TARGET, "br", "M5", prev_ts, PREV_CLOSE, PREV_CLOSE, PREV_CLOSE, PREV_CLOSE, 10, 10, 0))

    # Target: se B3, abre 09:00 BRT (o engine soma +6h -> 15:00 no eixo EEST).
    # Se 24h, começa junto com o fator (00:00) -> não deve haver pré-mercado.
    start_h = 9 if target_is_b3 else 0
    t_base = datetime.fromisoformat(f"{SESSION}T{start_h:02d}:00:00")
    for i in range(40):   # ~3h20 de sessão
        ts = (t_base + timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        px = TODAY_OPEN * (1 + 0.0002 * i)
        o = TODAY_OPEN if i == 0 else px
        c.execute("""INSERT INTO market_bars
                     (symbol, source, timeframe, timestamp_utc, open, high, low, close, volume, real_volume, delta)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  (TARGET, "br", "M5", ts, o, px, px, px, 10, 10, 0))
    c.commit()
    c.close()


def _engine(db_path):
    from backend.irai.engine import IRAIEngine
    return IRAIEngine(db_path=db_path)


def _run(version="v1", target_is_b3=True):
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    _seed(db, target_is_b3=target_is_b3)
    eng = _engine(db)
    snaps = eng.compute_from_db(SESSION, target=TARGET, version=version, persist_state=False)
    return snaps


# ── 1. O BUG PRINCIPAL: retorno falso no pré-mercado ────────────
def test_win_return_zero_no_pre_mercado():
    snaps = _run("v1")
    pre = [s for s in snaps if s.is_ghost and s.win_current == PREV_CLOSE]
    assert pre, "fixture inválida: não gerou barras de pré-mercado"
    for s in pre:
        assert s.win_return == 0.0, (
            f"retorno falso no pré-mercado: {s.win_return}% "
            f"(barra sintética ancorada na abertura de hoje)")


# ── 2. O payload não pode vazar a abertura de hoje ──────────────
def test_win_open_nao_vaza_abertura_de_hoje():
    """O frontend recomputa (win_current/win_open − 1) por barra (App.jsx:397).
    Se win_open for a abertura de HOJE, a UI plota o retorno falso mesmo com
    win_return zerado no backend — o fix seria falso."""
    snaps = _run("v1")
    pre = [s for s in snaps if s.is_ghost and s.win_current == PREV_CLOSE]
    for s in pre:
        assert s.win_open == s.win_current, (
            f"win_open={s.win_open} vaza a abertura de hoje (win_current={s.win_current}) "
            f"-> o frontend recomputaria {((s.win_current/s.win_open)-1)*100:.2f}%")


# ── 3. A primeira barra real NÃO é pré-mercado (trava o off-by-one) ──
def test_primeira_barra_real_nao_e_ghost():
    snaps = _run("v1")
    reais = [s for s in snaps if s.win_current != PREV_CLOSE]
    assert reais, "fixture inválida: não gerou barras reais"
    assert not reais[0].is_ghost, "primeira barra real marcada como ghost (cursor não avançou de -1 p/ 0)"
    assert reais[0].win_open == TODAY_OPEN, "âncora da sessão real deve ser a abertura de hoje"


# ── 4. Ativo 24h não entra em pré-mercado por engano ────────────
def test_ativo_24h_nao_tem_pre_mercado():
    snaps = _run("v1", target_is_b3=False)
    assert snaps, "fixture inválida"
    assert not snaps[0].is_ghost, "ativo 24h não deve ter pré-mercado na primeira barra"
    assert snaps[0].win_open == TODAY_OPEN


# ── 5/6. Kalman: nenhuma CORREÇÃO por observação antes da 1ª barra real ──
def test_kalman_nao_recebe_observacao_no_pre_mercado():
    """Invariante forte: o posterior é função APENAS das observações intra-sessão.
    Spy no wrapper -> não exige pykalman instalado."""
    import backend.irai.engine as eng_mod

    calls = []

    class SpyKalman:
        def __init__(self, n_dim_state, n_dim_obs, transition_covariance,
                     observation_covariance, initial_state_mean):
            self.mean = list(initial_state_mean)
            self.prior = list(initial_state_mean)
            self.n = n_dim_state

        def update(self, observation, observation_matrix):
            calls.append(("update", float(observation)))
            return self.mean, None

        def predict(self, observation_matrix=None):
            calls.append(("predict", None))
            return self.mean, None

        def get_state(self):
            return list(self.mean), [[0.0] * self.n] * self.n

        def set_state(self, m, c):
            self.mean = list(m)

    orig = eng_mod.KalmanFilterWrapper
    eng_mod.KalmanFilterWrapper = SpyKalman
    try:
        db = os.path.join(tempfile.mkdtemp(), "t.db")
        _seed(db)
        snaps = eng_mod.IRAIEngine(db_path=db).compute_from_db(
            SESSION, target=TARGET, version="v2", persist_state=False)
    finally:
        eng_mod.KalmanFilterWrapper = orig

    assert snaps, "v2 não produziu snapshots"
    n_pre = sum(1 for s in snaps if s.is_ghost and s.win_current == PREV_CLOSE)
    assert n_pre > 0, "fixture inválida: sem pré-mercado"

    # nenhuma das primeiras n_pre chamadas pode ser 'update' (correção)
    pre_calls = calls[:n_pre]
    updates_no_pre = [c for c in pre_calls if c[0] == "update"]
    assert not updates_no_pre, (
        f"{len(updates_no_pre)} correções do Kalman no pré-mercado "
        f"(observações: {[round(c[1], 5) for c in updates_no_pre[:3]]}...) — "
        f"o prior é apagado: memória efetiva do filtro é ~10 barras")

    # e as barras reais precisam correr o update normalmente
    assert any(c[0] == "update" for c in calls[n_pre:]), "nenhuma correção nas barras reais"


# ── 7. Johansen não acumula o preço sintético ──────────────────
def test_johansen_nao_acumula_pre_mercado():
    """No pré-mercado a coluna do target é constante -> check_cointegration a
    descarta (variância ~0) e o 'teste' roda fatores-contra-fatores, sem o
    target. Pior: a janela rolante (50) carrega um degrau fabricado nas
    primeiras ~4h de sessão — justo onde o gate do WDO importa (ADR-001)."""
    import backend.irai.engine as eng_mod

    seen = []

    def spy_coint(df, *a, **k):
        seen.append(df)
        return (0.05, True)

    class SpyKalman:
        def __init__(self, n_dim_state, **kw):
            self.n = n_dim_state
            self.mean = [0.0] * n_dim_state
        def update(self, observation, observation_matrix): return self.mean, None
        def predict(self, observation_matrix=None): return self.mean, None
        def get_state(self): return list(self.mean), [[0.0] * self.n] * self.n
        def set_state(self, m, c): self.mean = list(m)

    orig_k, orig_c = eng_mod.KalmanFilterWrapper, eng_mod.check_cointegration
    eng_mod.KalmanFilterWrapper = SpyKalman
    eng_mod.check_cointegration = spy_coint
    try:
        db = os.path.join(tempfile.mkdtemp(), "t.db")
        _seed(db)
        eng_mod.IRAIEngine(db_path=db).compute_from_db(
            SESSION, target=TARGET, version="v2", persist_state=False)
    finally:
        eng_mod.KalmanFilterWrapper = orig_k
        eng_mod.check_cointegration = orig_c

    for df in seen:
        vals = df["target"].tolist() if hasattr(df, "columns") else []
        assert PREV_CLOSE not in vals, (
            "preço sintético do pré-mercado entrou na cesta do Johansen "
            "(coluna constante -> descartada por variância ~0 -> teste sem o target)")


# ── 8. Gap INTRA-SESSÃO = mesma classe do pré-mercado (review Codex) ──
def test_ghost_intra_sessao_nao_alimenta_o_kalman():
    """Leilão/halt/falha de feed: o target não imprime num slot, mas os fatores
    sim. O preço é forward-filled (stale) — não é observação nova. Alimentar o
    Kalman com ele super-pondera um dado que não existe."""
    import backend.irai.engine as eng_mod

    calls = []

    class SpyKalman:
        def __init__(self, n_dim_state, **kw):
            self.n = n_dim_state
            self.mean = [0.0] * n_dim_state
        def update(self, observation, observation_matrix):
            calls.append("update"); return self.mean, None
        def predict(self, observation_matrix=None):
            calls.append("predict"); return self.mean, None
        def get_state(self): return list(self.mean), [[0.0] * self.n] * self.n
        def set_state(self, m, c): self.mean = list(m)

    db = os.path.join(tempfile.mkdtemp(), "t.db")
    _seed(db)
    # abre um GAP no meio da sessão do target (remove 3 barras), mantendo o fator
    c = sqlite3.connect(db)
    gap = [f"{SESSION}T09:5{i}:00Z" for i in (0, 5)] + [f"{SESSION}T10:00:00Z"]
    for ts in gap:
        c.execute("DELETE FROM market_bars WHERE symbol=? AND timestamp_utc=?", (TARGET, ts))
    n_gap = c.total_changes
    c.commit(); c.close()
    assert n_gap > 0, "fixture inválida: nenhuma barra removida"

    orig = eng_mod.KalmanFilterWrapper
    eng_mod.KalmanFilterWrapper = SpyKalman
    try:
        snaps = eng_mod.IRAIEngine(db_path=db).compute_from_db(
            SESSION, target=TARGET, version="v2", persist_state=False)
    finally:
        eng_mod.KalmanFilterWrapper = orig

    # A invariante: UMA correção do Kalman por barra realmente IMPRESSA pelo
    # target. Tudo que é forward-fill (pré-mercado, gap intra-sessão, e também
    # os slots após o fechamento, em que o fator segue imprimindo) é observação
    # ausente -> predict.
    c = sqlite3.connect(db)
    n_impressas = c.execute(
        "SELECT COUNT(*) FROM market_bars WHERE symbol=? AND timeframe='M5' "
        "AND substr(timestamp_utc,1,10)=?", (TARGET, SESSION)).fetchone()[0]
    c.close()
    ghosts = [s for s in snaps if s.is_ghost]
    n_updates = calls.count("update")
    assert n_updates == n_impressas, (
        f"{n_updates} correções do Kalman para {n_impressas} barras impressas "
        f"({len(ghosts)} ghosts) — forward-fill virou observação")
    assert calls.count("predict") == len(ghosts), "todo ghost deve ser predict-only"


# ── 8b. Gap intra-sessão NÃO contamina o pair z-score (review deep+fable) ──
def test_ghost_intra_sessao_nao_contamina_pair_zscore():
    """Mesma classe de bug do teste 8, mas no bloco do pair z-score (linha
    ~754 de engine.py): o guard só checava `is_pre_market`, não `sem_print`.
    Num gap intra-sessão o target não imprime, mas `win_ret` (linha ~715) só é
    zerado no pré-mercado — durante o gap ele fica CONGELADO no forward-fill,
    enquanto o retorno do fator continua vivo. O resíduo `win_ret − β·ret_fator`
    dessa barra é fabricado e era apendado em `pair_residual_history`,
    contaminando o z_pair das barras reais seguintes (janela de até
    PAIR_SIGMA_WINDOW=20 observações)."""
    import backend.irai.engine as eng_mod

    calls = []
    orig_residual = eng_mod.pairwise_residual
    def spy_residual(*a, **k):
        calls.append(a)
        return orig_residual(*a, **k)

    class SpyKalman:
        """Beta fixo > PAIR_MIN_BETA, p/ o par ativo sempre existir."""
        def __init__(self, n_dim_state, **kw):
            self.n = n_dim_state
            self.mean = [0.0] + [1.5] * (n_dim_state - 1)
        def update(self, observation, observation_matrix): return self.mean, None
        def predict(self, observation_matrix=None): return self.mean, None
        def get_state(self): return list(self.mean), [[0.0] * self.n] * self.n
        def set_state(self, m, c): self.mean = list(m)

    db = os.path.join(tempfile.mkdtemp(), "t.db")
    _seed(db)
    # mesmo gap intra-sessão do teste 8 (leilão/halt: 3 barras do target somem,
    # o fator continua imprimindo)
    c = sqlite3.connect(db)
    gap = [f"{SESSION}T09:5{i}:00Z" for i in (0, 5)] + [f"{SESSION}T10:00:00Z"]
    for ts in gap:
        c.execute("DELETE FROM market_bars WHERE symbol=? AND timestamp_utc=?", (TARGET, ts))
    n_gap = c.total_changes
    c.commit(); c.close()
    assert n_gap > 0, "fixture inválida: nenhuma barra removida"

    orig_k = eng_mod.KalmanFilterWrapper
    eng_mod.KalmanFilterWrapper = SpyKalman
    eng_mod.pairwise_residual = spy_residual
    try:
        snaps = eng_mod.IRAIEngine(db_path=db).compute_from_db(
            SESSION, target=TARGET, version="v2", persist_state=False)
    finally:
        eng_mod.KalmanFilterWrapper = orig_k
        eng_mod.pairwise_residual = orig_residual

    n_reais = sum(1 for s in snaps if not s.is_ghost)
    assert len(calls) == n_reais, (
        f"pairwise_residual chamado {len(calls)}x para {n_reais} barras REAIS "
        f"({len(snaps) - n_reais} ghosts) — gap intra-sessão alimentou resíduo "
        f"fabricado na janela rolante do z-score")

    # e nenhum snapshot ghost carrega um pair_z/pair_signal calculado sobre o
    # forward-fill (deve ficar no default: sem observação nova, sem sinal)
    for s in snaps:
        if s.is_ghost:
            assert s.pair_signal == "neutral" and s.pair_z == 0.0, (
                f"ghost em {s.timestamp} carrega pair_signal={s.pair_signal!r} "
                f"pair_z={s.pair_z} fabricado a partir do forward-fill")


# ── 9. Replay histórico NÃO persiste o estado do Kalman ────────
def test_replay_historico_nao_persiste_estado():
    """O guard monotônico do save_kalman_state só protege quando JÁ existe estado
    mais novo. Num banco recém-resetado, um replay antigo semearia o prior vivo.
    Só a SESSÃO VIVA (a última do target no banco) pode gravar."""
    import backend.irai.engine as eng_mod

    class SpyKalman:
        def __init__(self, n_dim_state, **kw):
            self.n = n_dim_state
            self.mean = [0.0] * n_dim_state
        def update(self, observation, observation_matrix): return self.mean, None
        def predict(self, observation_matrix=None): return self.mean, None
        def get_state(self): return list(self.mean), [[0.0] * self.n] * self.n
        def set_state(self, m, c): self.mean = list(m)

    db = os.path.join(tempfile.mkdtemp(), "t.db")
    _seed(db)   # sessão 2026-07-10 (a última do target)

    # cria uma sessão ANTERIOR do target (o "replay histórico")
    antiga = "2026-07-08"
    c = sqlite3.connect(db)
    base = datetime.fromisoformat(f"{antiga}T09:00:00")
    for i in range(30):
        ts = (base + timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        px = 99_000.0 + i
        c.execute("""INSERT INTO market_bars
                     (symbol, source, timeframe, timestamp_utc, open, high, low, close, volume, real_volume, delta)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  (TARGET, "br", "M5", ts, px, px, px, px, 10, 10, 0))
        c.execute("""INSERT INTO market_bars
                     (symbol, source, timeframe, timestamp_utc, open, high, low, close, volume, real_volume, delta)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  (FACTOR, "tickmill", "M5", ts, 5000.0, 5000.0, 5000.0, 5000.0, 10, 10, 0))
    c.commit(); c.close()

    orig = eng_mod.KalmanFilterWrapper
    eng_mod.KalmanFilterWrapper = SpyKalman
    try:
        eng = eng_mod.IRAIEngine(db_path=db)
        # replay da sessão ANTIGA, com persist_state=True (o default dos endpoints)
        eng.compute_from_db(antiga, target=TARGET, version="v2", persist_state=True)
        c = sqlite3.connect(db)
        n = c.execute("SELECT COUNT(*) FROM kalman_state").fetchone()[0]
        c.close()
        assert n == 0, "replay histórico semeou o estado do Kalman (prior vivo contaminado)"

        # a sessão VIVA (a última) deve persistir normalmente
        eng.compute_from_db(SESSION, target=TARGET, version="v2", persist_state=True)
        c = sqlite3.connect(db)
        n = c.execute("SELECT COUNT(*) FROM kalman_state").fetchone()[0]
        c.close()
        assert n == 1, "sessão viva não persistiu o estado (v2 congelaria)"
    finally:
        eng_mod.KalmanFilterWrapper = orig


# ── 10. INTEGRAÇÃO: markers nunca caem em barra sintética (engine real) ──
def test_engine_nao_emite_marker_em_barra_sintetica():
    """test_markers.py trava a SEMÂNTICA da máquina de transição isoladamente;
    este aqui dirige o ENGINE DE VERDADE e garante que nenhum evento (pair/z)
    é emitido sobre barra sem print do target — o que seria um sinal fantasma."""
    import backend.irai.engine as eng_mod

    class SpyKalman:
        """Betas fixos e não-triviais, p/ o par ativo existir."""
        def __init__(self, n_dim_state, **kw):
            self.n = n_dim_state
            self.mean = [0.0] + [1.5] * (n_dim_state - 1)
        def update(self, observation, observation_matrix): return self.mean, None
        def predict(self, observation_matrix=None): return self.mean, None
        def get_state(self): return list(self.mean), [[0.0] * self.n] * self.n
        def set_state(self, m, c): self.mean = list(m)

    db = os.path.join(tempfile.mkdtemp(), "t.db")
    _seed(db)
    orig = eng_mod.KalmanFilterWrapper
    eng_mod.KalmanFilterWrapper = SpyKalman
    try:
        snaps = eng_mod.IRAIEngine(db_path=db).compute_from_db(
            SESSION, target=TARGET, version="v2", persist_state=False)
    finally:
        eng_mod.KalmanFilterWrapper = orig

    ghosts = [s for s in snaps if s.is_ghost]
    assert ghosts, "fixture inválida: sem barras sintéticas"
    for s in ghosts:
        for campo in ("pair_compra", "pair_venda", "z_compra_val", "z_venda_val"):
            assert getattr(s, campo, None) is None, (
                f"marker fantasma ({campo}) em barra sintética {s.timestamp}")

    # e compra/venda nunca coexistem na mesma barra
    for s in snaps:
        assert not (s.pair_compra is not None and s.pair_venda is not None)
        assert not (s.z_compra_val is not None and s.z_venda_val is not None)


# ── 10b. INTEGRAÇÃO: marker de par DIFERIDO através de um gap (review Codex) ──
def test_marker_pair_diferido_atravessa_gap_intra_sessao():
    """test_markers.py trava essa semântica na lógica REIMPLEMENTADA
    isoladamente (test_nunca_emite_marker_em_barra_sintetica); este aqui
    dirige o ENGINE DE VERDADE com um gap intra-sessão real e um sinal
    SCRIPTADO (via monkeypatch de pair_signal) que transiciona exatamente
    durante o gap — o marker não pode ser perdido nem duplicado: deve
    aparecer só na 1ª barra REAL depois do gap."""
    import backend.irai.engine as eng_mod

    class SpyKalman:
        def __init__(self, n_dim_state, **kw):
            self.n = n_dim_state
            self.mean = [0.0] + [1.5] * (n_dim_state - 1)
        def update(self, observation, observation_matrix): return self.mean, None
        def predict(self, observation_matrix=None): return self.mean, None
        def get_state(self): return list(self.mean), [[0.0] * self.n] * self.n
        def set_state(self, m, c): self.mean = list(m)

    db = os.path.join(tempfile.mkdtemp(), "t.db")
    _seed(db)
    # mesmo gap intra-sessão dos testes 8/8b: barras i=10,11,12 do target somem
    c = sqlite3.connect(db)
    gap = [f"{SESSION}T09:5{i}:00Z" for i in (0, 5)] + [f"{SESSION}T10:00:00Z"]
    for ts in gap:
        c.execute("DELETE FROM market_bars WHERE symbol=? AND timestamp_utc=?", (TARGET, ts))
    n_gap = c.total_changes
    c.commit(); c.close()
    assert n_gap > 0, "fixture inválida: nenhuma barra removida"

    # sinal scriptado: as 10 primeiras barras reais (i=0..9, antes do gap)
    # ficam "neutral"; a partir da 11ª (i=13, 1ª real depois do gap) vira
    # "sell" — a transição em si acontece só "dentro" do gap (i=10,11,12
    # nem existem mais), então só pode ser observada na 1ª barra real seguinte.
    call_idx = [0]
    def scripted_signal(z_pair, beta, threshold):
        call_idx[0] += 1
        return "neutral" if call_idx[0] <= 10 else "sell"

    orig_k = eng_mod.KalmanFilterWrapper
    orig_sig = eng_mod.pair_signal
    eng_mod.KalmanFilterWrapper = SpyKalman
    eng_mod.pair_signal = scripted_signal
    try:
        snaps = eng_mod.IRAIEngine(db_path=db).compute_from_db(
            SESSION, target=TARGET, version="v2", persist_state=False)
    finally:
        eng_mod.KalmanFilterWrapper = orig_k
        eng_mod.pair_signal = orig_sig

    assert call_idx[0] == 37, (
        f"esperava 37 barras reais (40 - 3 do gap) chamando o sinal, "
        f"veio {call_idx[0]}")

    reais = [s for s in snaps if not s.is_ghost]
    vendas = [i for i, s in enumerate(reais) if s.pair_venda is not None]
    assert vendas == [10], (
        f"esperava o marker só na 11ª barra real (índice 10, 1ª depois do "
        f"gap), veio em {vendas} — transição perdida ou duplicada através do gap")

    ghosts = [s for s in snaps if s.is_ghost]
    assert all(s.pair_venda is None for s in ghosts), "marker fantasma em barra sintética"


# ── 10c. INTEGRAÇÃO: marker de par e de divergência coexistem na mesma barra ──
def test_marker_pair_e_divergencia_coexistem_na_mesma_barra():
    """Os dois eventos são branches independentes no engine (linhas ~825 e
    ~842) e podem disparar juntos na mesma barra real — nenhum deve suprimir
    o outro (review Codex: 'mesma barra, duas transições simultâneas?')."""
    import backend.irai.engine as eng_mod

    class SpyKalman:
        def __init__(self, n_dim_state, **kw):
            self.n = n_dim_state
            self.mean = [0.0] + [1.5] * (n_dim_state - 1)
        def update(self, observation, observation_matrix): return self.mean, None
        def predict(self, observation_matrix=None): return self.mean, None
        def get_state(self): return list(self.mean), [[0.0] * self.n] * self.n
        def set_state(self, m, c): self.mean = list(m)

    db = os.path.join(tempfile.mkdtemp(), "t.db")
    _seed(db)

    # sinal de par: "neutral" até a 20ª barra real, "sell" a partir da 21ª.
    # `real_bar_flag` é aceso pelo pair_signal (só roda em barra real, linha
    # ~769 do engine) e consumido pelo compute() logo em seguida (linha ~775,
    # mesma iteração do loop) — assim sei, sem inspecionar is_ghost (que só é
    # setado pelo CALLER depois do compute retornar), que este compute() é o
    # da mesma barra real que acabou de gerar o sinal.
    call_idx = [0]
    real_bar_flag = [False]
    def scripted_signal(z_pair, beta, threshold):
        call_idx[0] += 1
        real_bar_flag[0] = True
        return "neutral" if call_idx[0] <= 20 else "sell"

    eng = _engine(db)
    orig_compute = eng.compute
    def wrapped_compute(*a, **k):
        snap = orig_compute(*a, **k)
        if real_bar_flag[0]:
            real_bar_flag[0] = False
            # na 21ª barra real (mesma em que o sinal de par vira "sell"),
            # força p_up<45 p/ a divergência bater junto — price_diverge_z já
            # é organicamente > threshold nessa altura da sessão (a cesta é
            # monotonicamente altista na fixture; ver diagnóstico da task).
            if call_idx[0] == 21:
                snap.p_up = 20.0
        return snap
    eng.compute = wrapped_compute

    orig_k = eng_mod.KalmanFilterWrapper
    orig_sig = eng_mod.pair_signal
    eng_mod.KalmanFilterWrapper = SpyKalman
    eng_mod.pair_signal = scripted_signal
    try:
        snaps = eng.compute_from_db(SESSION, target=TARGET, version="v2", persist_state=False)
    finally:
        eng_mod.KalmanFilterWrapper = orig_k
        eng_mod.pair_signal = orig_sig

    reais = [s for s in snaps if not s.is_ghost]
    assert len(reais) >= 21, f"fixture inválida: só {len(reais)} barras reais"
    alvo = reais[20]   # 21ª barra real (0-indexed 20)
    assert alvo.pair_venda is not None, "marker de par não disparou na barra alvo"
    assert alvo.z_venda_val is not None, "marker de divergência não disparou na barra alvo"
    assert alvo.pair_venda == alvo.z_venda_val, (
        "os dois markers devem carregar o mesmo preço (close) da barra")
    assert not (alvo.pair_compra or alvo.z_compra_val), "lado de compra não deveria disparar aqui"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL {name}: {e}")
            except Exception as e:
                fails += 1
                print(f"  ERRO {name}: {type(e).__name__}: {e}")
    print("todos passaram" if not fails else f"{fails} falha(s)")
    sys.exit(1 if fails else 0)
