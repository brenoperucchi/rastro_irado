"""Spec do Slice 1 da extensão de GEX para DOL (Mini Dólar / WDO$N).

Ref: docs/plans/2026-07-10-frontend-migration-status-and-forward-plan.md — Pacote B #6.
Painel (codex + deep-reasoner + fable-reasoner) sobre a arquitetura completa
(fonte de OI/strike/CP/vencimento, spot/settle, GRID_STEP) — decisão registrada
na Task #15.

O worker original (backend/workers/gex_worker.py) só cobria IBOV -> WIN$N, e
usava o universo de símbolos do MT5 (terminal XP) para strike/call-put/
vencimento/prêmio das opções. Confirmado em produção (com autorização do
usuário, collector pausado) que NENHUM dos 2 terminais MT5 tem séries de opção
de DOL — só futuros/ETFs. Substituição: a tabela InstrumentsDerivatives do BDI
(cadastro oficial B3, "Derivativos de bolsa") supre strike/CP/vencimento sem
nenhuma dependência do MT5 para essa perna.

As invariantes que este spec trava:
  1. fetch_bdi_oi lê a coluna de OI correta por SEGMENTO, não por asset
     hardcoded: IBOV (SgmtNm='EQUITY CALL/PUT') preenche TtlPos e deixa
     OpnIntrst None; DOL (SgmtNm='FINANCIAL') é o INVERSO — TtlPos vem None,
     OpnIntrst é quem carrega o OI real. Um fetch_bdi_oi que só olhasse TtlPos
     devolveria ZERO séries de DOL com OI (bug confirmado nos dados reais
     coletados nesta sessão, antes do fix).
  2. fetch_bdi_instruments só devolve séries de OPÇÃO (OptnTp in {'Call','Put'});
     futuros do mesmo Asst (ex. DOLF27, sem OptnTp) ficam de fora do dict —
     não podem vazar pro join como se fossem opção.
  3. fetch_bdi_option_data faz o join oi_rows x instruments por ticker e não
     inventa prêmio (premium=None sempre, já que não existe fonte B3 pública
     de prêmio EOD de DOL) — compute_gex já sabe cair no fallback de IV nesse
     caso. Séries sem cadastro (miss) são descartadas, não quebram o join.

Roda sem pytest:  python3 tests/test_gex_worker.py
"""
import math
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.workers import gex_worker as gw

# ── fixtures: layout real das colunas observado nos dados coletados (BDI) ──

OPE_COLS = ["TckrSymb", "Asst", "SgmtNm", "OpnIntrst", "VartnOpnIntrst", "DstrbtnId", "TtlPos"]

OPE_ROWS_MIXED = [
    # IBOV: TtlPos preenchido, OpnIntrst None (segmento EQUITY CALL/PUT)
    ["IBOVG177W4", "IBOV", "EQUITY CALL", None, None, 18, 52162],
    ["IBOVS165W4", "IBOV", "EQUITY PUT", None, None, 18, 80000],
    ["IBOVX000ZZ", "IBOV", "EQUITY CALL", None, None, 18, 0],       # OI zero -> descartada
    # DOL: OpnIntrst preenchido, TtlPos None (segmento FINANCIAL)
    ["DOLF27", "DOL", "FINANCIAL", 1000, 0, None, None],            # futuro (sem strike) — ainda assim tem OI aqui
    ["DOLF27C007000", "DOL", "FINANCIAL", 830, 0, None, None],
    ["DOLF27P006500", "DOL", "FINANCIAL", 695, 0, None, None],
    ["DOLF27C006500", "DOL", "FINANCIAL", 2550, 0, None, None],
    ["DOLZ00X999999", "DOL", "FINANCIAL", 0, 0, None, None],        # OI zero -> descartada
]

INSTR_COLS = ["TckrSymb", "Asst", "OptnTp", "ExrcPric", "XprtnDt"]

INSTR_ROWS = [
    ["DOLF27", "DOL", None, None, "2027-01-04T00:00:00"],                 # futuro, sem OptnTp
    ["DOLF27C007000", "DOL", "Call", 7000, "2027-01-04T00:00:00"],
    ["DOLF27P006500", "DOL", "Put", 6500, "2027-01-04T00:00:00"],
    ["DOLF27C006500", "DOL", "Call", 6500, "2027-01-04T00:00:00"],
    # nota: DOLZ00X999999 (OI zero) nem chega a ser consultada no join real,
    # mas também não está no cadastro — simula uma série sem OI e sem cadastro.
    ["IBOVG177W4", "IBOV", "Call", 177000, "2026-08-21T00:00:00"],        # asset diferente -> não deve vazar no filtro por 'DOL'
]


def _patch_bdi_table(monkeypatch_table):
    gw.fetch_bdi_table = monkeypatch_table


def test_fetch_bdi_oi_ibov_usa_ttlpos():
    orig = gw.fetch_bdi_table
    gw.fetch_bdi_table = lambda table, session_date, sort: (OPE_COLS, OPE_ROWS_MIXED)
    try:
        out = gw.fetch_bdi_oi("2026-07-13", asset="IBOV")
    finally:
        gw.fetch_bdi_table = orig
    got = {r["ticker"]: r["oi"] for r in out}
    assert got == {"IBOVG177W4": 52162.0, "IBOVS165W4": 80000.0}, got


def test_fetch_bdi_oi_dol_usa_opnintrst_nao_ttlpos():
    orig = gw.fetch_bdi_table
    gw.fetch_bdi_table = lambda table, session_date, sort: (OPE_COLS, OPE_ROWS_MIXED)
    try:
        out = gw.fetch_bdi_oi("2026-07-13", asset="DOL")
    finally:
        gw.fetch_bdi_table = orig
    got = {r["ticker"]: r["oi"] for r in out}
    # as 4 séries com OI>0 (futuro + 3 opções); a de OI zero fica de fora
    assert got == {
        "DOLF27": 1000.0,
        "DOLF27C007000": 830.0,
        "DOLF27P006500": 695.0,
        "DOLF27C006500": 2550.0,
    }, got


def test_fetch_bdi_oi_ambas_colunas_none_vira_zero_e_descarta():
    cols = OPE_COLS
    rows = [["DOLW00X111111", "DOL", "FINANCIAL", None, None, None, None]]
    orig = gw.fetch_bdi_table
    gw.fetch_bdi_table = lambda table, session_date, sort: (cols, rows)
    try:
        out = gw.fetch_bdi_oi("2026-07-13", asset="DOL")
    finally:
        gw.fetch_bdi_table = orig
    assert out == [], out


def test_fetch_bdi_oi_ambas_colunas_preenchidas_ttlpos_prevalece():
    # Política explícita (revisão codex): se o feed um dia trouxer as duas
    # colunas preenchidas simultaneamente, TtlPos vence (é a coluna
    # autoritativa hoje pra IBOV; o fallback só existe pro caso DOL onde
    # TtlPos vem None). Documentado aqui pra não virar comportamento
    # acidental não coberto por teste.
    cols = OPE_COLS
    rows = [["XXXW00X222222", "DOL", "FINANCIAL", 999, 0, None, 111]]
    orig = gw.fetch_bdi_table
    gw.fetch_bdi_table = lambda table, session_date, sort: (cols, rows)
    try:
        out = gw.fetch_bdi_oi("2026-07-13", asset="DOL")
    finally:
        gw.fetch_bdi_table = orig
    assert out == [{"ticker": "XXXW00X222222", "oi": 111.0}], out


def test_fetch_bdi_instruments_ignora_optntp_nao_reconhecido():
    cols = INSTR_COLS
    rows = [["DOLW00X333333", "DOL", "Straddle", 5000, "2026-08-01T00:00:00"]]
    orig = gw.fetch_bdi_table
    gw.fetch_bdi_table = lambda table, session_date, sort: (cols, rows)
    try:
        out = gw.fetch_bdi_instruments("2026-07-13", asset="DOL")
    finally:
        gw.fetch_bdi_table = orig
    assert out == {}, "valor de OptnTp fora de {Call,Put} não pode virar Put por default"


def test_fetch_bdi_instruments_so_opcoes_do_asset():
    orig = gw.fetch_bdi_table
    gw.fetch_bdi_table = lambda table, session_date, sort: (INSTR_COLS, INSTR_ROWS)
    try:
        out = gw.fetch_bdi_instruments("2026-07-13", asset="DOL")
    finally:
        gw.fetch_bdi_table = orig
    # futuro (DOLF27, OptnTp None) fora; IBOVG177W4 (asset != DOL) fora
    assert set(out.keys()) == {"DOLF27C007000", "DOLF27P006500", "DOLF27C006500"}, out.keys()
    assert out["DOLF27C007000"] == {"strike": 7000.0, "is_call": True, "expiry": "2027-01-04"}
    assert out["DOLF27P006500"] == {"strike": 6500.0, "is_call": False, "expiry": "2027-01-04"}


def test_fetch_bdi_option_data_join_e_sem_premio():
    oi_rows = [
        {"ticker": "DOLF27", "oi": 1000.0},              # futuro -> sem cadastro de opção -> miss (descartado)
        {"ticker": "DOLF27C007000", "oi": 830.0},
        {"ticker": "DOLF27P006500", "oi": 695.0},
        {"ticker": "DOLF27C006500", "oi": 2550.0},
        {"ticker": "DOLZ00X999999", "oi": 5.0},           # sem cadastro -> miss
    ]
    orig = gw.fetch_bdi_table
    gw.fetch_bdi_table = lambda table, session_date, sort: (INSTR_COLS, INSTR_ROWS)
    try:
        options = gw.fetch_bdi_option_data(oi_rows, "2026-07-13", asset="DOL")
    finally:
        gw.fetch_bdi_table = orig
    assert len(options) == 3, options
    by_ticker = {o["ticker"]: o for o in options}
    assert by_ticker["DOLF27C007000"]["strike"] == 7000.0
    assert by_ticker["DOLF27C007000"]["is_call"] is True
    assert by_ticker["DOLF27C007000"]["expiry"] == "2027-01-04"
    assert by_ticker["DOLF27C007000"]["oi"] == 830.0
    assert all(o["premium"] is None for o in options), "DOL não tem fonte de prêmio B3 pública — nunca inventar"
    assert "DOLF27" not in by_ticker, "futuro não pode vazar como opção"
    assert "DOLZ00X999999" not in by_ticker, "série sem cadastro deve ser descartada, não quebrar o join"


# ── Slice 3: infer_grid_step (painel Task #15, Q3) ──────────────────────────

def test_infer_grid_step_calcula_mediana_do_gap_perto_do_spot():
    spot = 5000.0
    options = [
        {"strike": 4900.0}, {"strike": 4950.0}, {"strike": 5000.0},
        {"strike": 5050.0}, {"strike": 5100.0},
        {"strike": 3000.0},  # fora da moneyness (+-15%) -> ignorado
    ]
    step = gw.infer_grid_step(options, spot)
    assert step == 50.0, step


def test_infer_grid_step_poucos_strikes_perto_do_spot_usa_default():
    spot = 5000.0
    options = [{"strike": 5000.0}, {"strike": 5050.0}]  # só 2 distintos -> < 3
    step = gw.infer_grid_step(options, spot, default=1234.0)
    assert step == 1234.0, step


def test_infer_grid_step_ignora_strikes_fora_da_moneyness():
    spot = 5000.0
    options = [
        {"strike": 4950.0}, {"strike": 5000.0}, {"strike": 5050.0},
        {"strike": 100.0}, {"strike": 50000.0},  # bem fora de +-15% do spot
    ]
    step = gw.infer_grid_step(options, spot)
    assert step == 50.0, step


def test_infer_grid_step_default_none_significa_falha_explicita_nao_1000_do_ibov():
    """Review codex: a chamada de main() pra perna DOL passa default=None de
    propósito -- dado esparso demais pra inferir grid tem que virar 'sem
    dado' (main() falha o target), não um fallback silencioso pro GRID_STEP
    de 1000 do IBOV (escala errada pra DOL, abriria os gates liquid/valid
    demais e mascararia dado ruim como válido)."""
    spot = 5400.0
    options = [{"strike": 5400.0}, {"strike": 5450.0}]  # só 2 -> insuficiente
    step = gw.infer_grid_step(options, spot, default=None)
    assert step is None, step


# ── Slice 3: realized_vol / realized_iv_by_expiry (painel Task #15, Q1) ────

def _mk_conn():
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """CREATE TABLE market_bars (
             symbol TEXT NOT NULL, source TEXT NOT NULL, timeframe TEXT NOT NULL,
             timestamp_utc TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL,
             volume REAL, real_volume REAL, delta REAL,
             PRIMARY KEY (symbol, timeframe, timestamp_utc)
           );""")
    return conn


def _insert_bar(conn, symbol, ts, close):
    conn.execute(
        """INSERT INTO market_bars
           (symbol, source, timeframe, timestamp_utc, open, high, low, close, volume, real_volume, delta)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (symbol, "tickmill", "M5", ts, close, close, close, close, 10, 10, 0))


def test_realized_vol_historico_insuficiente_retorna_none():
    conn = _mk_conn()
    try:
        for i, close in enumerate([100, 101, 102]):  # só 3 dias -> < 5
            _insert_bar(conn, "WDO$N", f"2026-07-{i + 1:02d}T17:00:00Z", close)
        conn.commit()
        out = gw.realized_vol(conn, "WDO$N", "2026-07-10", window_days=5)
    finally:
        conn.close()
    assert out is None, out


def test_realized_vol_usa_ultimo_close_do_dia_nao_o_primeiro():
    """O collector grava M5 o dia todo; o close diário tem que ser o ÚLTIMO
    bar cronológico do dia, não o primeiro (que seria o open, não o close)."""
    conn = _mk_conn()
    try:
        wrongs = [90, 95, 90, 96, 91, 93]   # bar de abertura, deve ser IGNORADO
        trues = [100, 102, 101, 103, 102, 104]   # bar de fechamento, é o que conta
        for i, (wrong, close) in enumerate(zip(wrongs, trues)):
            d = f"2026-07-{i + 1:02d}"
            _insert_bar(conn, "WDO$N", f"{d}T09:00:00Z", wrong)
            _insert_bar(conn, "WDO$N", f"{d}T17:00:00Z", close)
        conn.commit()
        out = gw.realized_vol(conn, "WDO$N", "2026-07-10", window_days=5)
    finally:
        conn.close()
    assert out is not None
    # valor de referência pré-calculado independentemente com a mesma série de
    # closes diários (100,102,101,103,102,104) -> log-retornos -> vol anualizada
    assert abs(out - 0.25575897488575555) < 1e-9, out


def test_realized_vol_exclui_o_proprio_dia_da_sessao_lookahead():
    """Uma barra datada NO PRÓPRIO dia da sessão (ou depois) não pode vazar
    pro cálculo -- seria olhar o futuro. `WHERE ... < session_date` é a guarda."""
    conn = _mk_conn()
    try:
        for i, close in enumerate([100, 102, 101, 103, 102, 104]):
            _insert_bar(conn, "WDO$N", f"2026-07-{i + 1:02d}T17:00:00Z", close)
        # barra do PRÓPRIO dia da sessão, com valor absurdo -- não pode contaminar
        _insert_bar(conn, "WDO$N", "2026-07-07T09:00:00Z", 999999.0)
        conn.commit()
        out = gw.realized_vol(conn, "WDO$N", "2026-07-07", window_days=5)
    finally:
        conn.close()
    assert out is not None
    assert abs(out - 0.25575897488575555) < 1e-9, (
        "vazou a barra do próprio dia da sessão (lookahead) no cálculo de vol")


def test_realized_iv_by_expiry_clampa_janela_em_min_max():
    """Trava a aritmética do clamp horizon-matched sem depender de dados reais
    no banco: espia os `window_days` que realized_iv_by_expiry realmente pede
    pra realized_vol por vencimento."""
    captured = []
    orig = gw.realized_vol
    gw.realized_vol = lambda conn, symbol, session_date, window_days: captured.append(window_days) or 0.30
    try:
        gw.realized_iv_by_expiry(
            None, "WDO$N", "2026-07-01",
            expiries=["2026-07-03", "2026-08-15", "2028-01-01"],  # +2d, +45d, +~549d
            min_window=10, max_window=60)
    finally:
        gw.realized_vol = orig
    assert captured == [10, 45, 60], captured


def test_realized_iv_by_expiry_janela_horizon_matched_pega_regime_certo():
    """Integração fim-a-fim (sem mock): vencimento próximo usa janela curta
    (min_window) -> só enxerga o regime recente calmo; vencimento distante
    clampa em max_window -> puxa também o regime antigo volátil. A IV do
    vencimento distante tem que sair maior."""
    conn = _mk_conn()
    try:
        closes = [100.0]
        for i in range(1, 40):  # regime antigo: alterna +-5% (volátil)
            closes.append(closes[-1] * (1 + (0.05 if i % 2 else -0.05)))
        for i in range(1, 30):  # regime recente: alterna +-0.05% (calmo)
            closes.append(closes[-1] * (1 + (0.0005 if i % 2 else -0.0005)))
        session = datetime(2026, 9, 1)
        n = len(closes)
        for i, close in enumerate(closes):
            d = session - timedelta(days=n - i)
            _insert_bar(conn, "WDO$N", f"{d.date().isoformat()}T17:00:00Z", close)
        conn.commit()
        out = gw.realized_iv_by_expiry(
            conn, "WDO$N", "2026-09-01",
            expiries=["2026-09-06", "2027-03-01"],  # +5d (min_window) e +181d (max_window)
            min_window=10, max_window=60)
    finally:
        conn.close()
    assert out["2026-09-06"] < out["2027-03-01"], (
        "vencimento próximo (janela curta, regime calmo) devia sair com IV "
        f"menor que o distante (janela longa, pega regime volátil também): {out}")


def test_realized_iv_by_expiry_clampa_em_iv_max():
    orig = gw.realized_vol
    gw.realized_vol = lambda conn, symbol, session_date, window_days: 5.0  # vol absurda
    try:
        out = gw.realized_iv_by_expiry(None, "WDO$N", "2026-07-01", expiries=["2026-08-01"])
    finally:
        gw.realized_vol = orig
    assert out["2026-08-01"] == gw.IV_MAX, out


def test_realized_iv_by_expiry_clampa_em_iv_min():
    orig = gw.realized_vol
    gw.realized_vol = lambda conn, symbol, session_date, window_days: 0.001  # vol quase nula
    try:
        out = gw.realized_iv_by_expiry(None, "WDO$N", "2026-07-01", expiries=["2026-08-01"])
    finally:
        gw.realized_vol = orig
    assert out["2026-08-01"] == gw.IV_MIN, out


def test_realized_iv_by_expiry_sem_historico_omite_vencimento():
    orig = gw.realized_vol
    gw.realized_vol = lambda conn, symbol, session_date, window_days: None
    try:
        out = gw.realized_iv_by_expiry(None, "WDO$N", "2026-07-01", expiries=["2026-08-01"])
    finally:
        gw.realized_vol = orig
    assert out == {}, out


# ── Slice 2: pernas MT5 (spot/settle IBOV+WIN e DOL+WDO) — mt5 mockado ─────

def _ts(y, m, d, h=21):
    return int(datetime(y, m, d, h, 0, tzinfo=timezone.utc).timestamp())


class _FakeInfo:
    def __init__(self, name, strike, right, exp_ts, session_close):
        self.name = name
        self.option_strike = strike
        self.option_right = right
        self.expiration_time = exp_ts
        self.session_close = session_close


class _FakeMT5:
    def __init__(self, bars, symbols):
        self.TIMEFRAME_D1 = "D1"
        self._bars = bars
        self._symbols = symbols
        self.selected = []

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        return self._bars.get(symbol, [])

    def symbols_get(self, pattern):
        return list(self._symbols)

    def symbol_select(self, ticker, enable):
        self.selected.append((ticker, enable))


def test_fetch_ibov_mt5_leg_junta_oi_bdi_com_metadados_e_premio_mt5():
    ref_ts = _ts(2026, 7, 13)
    exp_ts = _ts(2026, 8, 21)
    bars = {
        "IBOV": [(ref_ts, 0, 0, 0, 130000.0, 0, 0, 0)],
        "WIN$N": [(ref_ts, 0, 0, 0, 131500.0, 0, 0, 0)],
    }
    symbols = [
        _FakeInfo("IBOVG130W4", 130000.0, 0, exp_ts, 1250.0),   # call, com prêmio
        _FakeInfo("IBOVS128W4", 128000.0, 1, exp_ts, 300.0),    # put, com prêmio
        _FakeInfo("IBOVX999ZZ", 0, 0, 0, 0),                    # sem option_strike -> não é opção válida
    ]
    mt5 = _FakeMT5(bars, symbols)
    oi_rows = [
        {"ticker": "IBOVG130W4", "oi": 5000.0},
        {"ticker": "IBOVS128W4", "oi": 3000.0},
        {"ticker": "IBOVNAOEXISTE", "oi": 10.0},  # sem metadado MT5 -> descartada
    ]
    out = gw.fetch_ibov_mt5_leg(mt5, oi_rows, "2026-07-13", trust_session_close=True)
    assert out["spot"] == 130000.0
    assert out["win_settle"] == 131500.0
    by_ticker = {o["ticker"]: o for o in out["options"]}
    assert set(by_ticker) == {"IBOVG130W4", "IBOVS128W4"}, by_ticker.keys()
    assert by_ticker["IBOVG130W4"]["is_call"] is True
    assert by_ticker["IBOVG130W4"]["premium"] == 1250.0
    assert by_ticker["IBOVS128W4"]["is_call"] is False
    assert by_ticker["IBOVG130W4"]["expiry"] == "2026-08-21"
    assert mt5.selected == [], "trust_session_close=True não pode cair no fallback via symbol_select"


def test_fetch_ibov_mt5_leg_sem_trust_session_close_cai_no_fallback_atm():
    ref_ts = _ts(2026, 7, 13)
    exp_ts = _ts(2026, 8, 21)
    bars = {
        "IBOV": [(ref_ts, 0, 0, 0, 130000.0, 0, 0, 0)],
        "WIN$N": [(ref_ts, 0, 0, 0, 131500.0, 0, 0, 0)],
        "IBOVG130W4": [(ref_ts, 0, 0, 0, 1300.0, 0, 0, 0)],   # fallback: D1 datado do próprio ticker
    }
    symbols = [_FakeInfo("IBOVG130W4", 130000.0, 0, exp_ts, 0.0)]  # session_close=0 -> vira None
    mt5 = _FakeMT5(bars, symbols)
    oi_rows = [{"ticker": "IBOVG130W4", "oi": 5000.0}]
    out = gw.fetch_ibov_mt5_leg(mt5, oi_rows, "2026-07-13", trust_session_close=False)
    opt = out["options"][0]
    assert opt["premium"] == 1300.0, opt
    assert mt5.selected == [("IBOVG130W4", True), ("IBOVG130W4", False)], mt5.selected


def test_fetch_dol_mt5_leg_dol_e_spot_wdo_e_settle():
    ref_ts = _ts(2026, 7, 13)
    bars = {
        "DOL$N": [(ref_ts, 0, 0, 0, 5432.5, 0, 0, 0)],
        "WDO$N": [(ref_ts, 0, 0, 0, 5430.0, 0, 0, 0)],
    }
    mt5 = _FakeMT5(bars, [])
    out = gw.fetch_dol_mt5_leg(mt5, "2026-07-13")
    assert out == {"spot": 5432.5, "future_settle": 5430.0}, out


# ── Slice 3: compute_gex — novos parâmetros (painel Task #15) ──────────────

def _mk_options(n_calls=4, n_puts=4, strike_step=1000, spot=130000.0,
                 expiry="2026-08-21", premium=None):
    options = []
    for i in range(n_calls):
        k = spot + (i - n_calls // 2) * strike_step
        options.append({"ticker": f"C{i}", "oi": 100.0, "strike": k,
                         "is_call": True, "expiry": expiry, "premium": premium})
    for i in range(n_puts):
        k = spot + (i - n_puts // 2) * strike_step
        options.append({"ticker": f"P{i}", "oi": 100.0, "strike": k,
                         "is_call": False, "expiry": expiry, "premium": premium})
    return options


def _mk_options_wide(spot, expiry="2026-08-21", step=100.0, n=6):
    """Strikes -n..+n de `step` em `step`; negativos viram put, não-negativos
    call -- gera um netGEX com sinal trocando perto do spot (flip real), pra
    exercitar o gate `valid` de compute_gex."""
    options = []
    for i in range(-n, n + 1):
        k = spot + i * step
        options.append({"ticker": f"O{i}", "oi": 100.0, "strike": k,
                         "is_call": i >= 0, "expiry": expiry, "premium": None})
    return options


def test_compute_gex_iv_fallback_by_expiry_substitui_020_fixo():
    options = _mk_options()
    session_date = "2026-07-13"
    sem_fallback = gw.compute_gex(130000.0, 131500.0, options, session_date)
    assert sem_fallback["meta"]["iv_fallback"] == 0.20

    com_fallback = gw.compute_gex(130000.0, 131500.0, options, session_date,
                                   iv_fallback_by_expiry={"2026-08-21": 0.12},
                                   iv_source="realized")
    assert com_fallback["meta"]["iv_fallback"] == 0.12
    assert com_fallback["meta"]["iv_by_exp"]["2026-08-21"] == 0.12
    assert com_fallback["meta"]["iv_source"] == "realized"


def test_compute_gex_iv_fallback_by_expiry_vazio_nao_vira_020_fixo():
    """Review codex: se realized_iv_by_expiry não achou histórico nenhum
    (retorna {} -- ex. DB local sem collector rodando), compute_gex NÃO pode
    reverter pro 0.20 fixo (nível de índice, proibido pra DOL) só porque {}
    é falsy em Python. `{}` explícito ainda é modo "realized" -- sem premio E
    sem vol realizada = sem IV confiável, os strikes ficam de fora do
    netGEX (não inventa um valor), e sem strikes suficientes o resultado
    inteiro vira None (mesmo gate de \"dado insuficiente\" que já existe)."""
    options = _mk_options()  # sem premium (default None) -> nada inverte
    result = gw.compute_gex(130000.0, 131500.0, options, "2026-07-13",
                             iv_fallback_by_expiry={}, iv_source="realized")
    assert result is None, result


def test_compute_gex_risk_free_e_realmente_usado_no_calculo():
    """Black-76 via risk_free=0.0 (decisão do painel p/ a perna DOL, Q1): tem
    que ALTERAR o resultado, não ser um parâmetro morto (ainda hardcoded
    internamente pro antigo R_FREE)."""
    spot = 5400.0
    options = _mk_options(spot=spot, strike_step=50, expiry="2026-08-21")
    for o in options:
        intrinsic = max(0.0, (spot - o["strike"]) if o["is_call"] else (o["strike"] - spot))
        o["premium"] = intrinsic + 40.0  # prêmio plausível -> inverte IV de verdade
    session_date = "2026-07-13"
    r0 = gw.compute_gex(spot, spot, options, session_date, risk_free=0.0)
    r1 = gw.compute_gex(spot, spot, options, session_date, risk_free=0.1425)
    assert r0["meta"]["risk_free"] == 0.0
    assert r1["meta"]["risk_free"] == 0.1425
    # a mesma tabela de prêmios inverte pra IVs BEM diferentes conforme o r
    # usado no BSM -- se isto desse igual, r estaria sendo ignorado por dentro
    # (ainda hardcoded no antigo R_FREE) em vez de vir do parâmetro.
    iv0 = r0["meta"]["iv_by_exp"]["2026-08-21"]
    iv1 = r1["meta"]["iv_by_exp"]["2026-08-21"]
    assert iv0 != iv1, (iv0, iv1)


def test_bsm_gamma_usa_risk_free_no_calculo_final():
    """Review codex: o teste de risk_free em compute_gex só provava o estágio
    de INVERSÃO de prêmio -- se _bsm_gamma (o cálculo final) regredisse pro
    R_FREE hardcoded, aquele teste continuaria passando. Isola o estágio
    final direto: mesmo S,K,T,sigma, r diferente TEM que dar gamma diferente."""
    g0 = gw._bsm_gamma(5400.0, 5450.0, 0.1, 0.0, 0.20)
    g1 = gw._bsm_gamma(5400.0, 5450.0, 0.1, 0.1425, 0.20)
    assert g0 != g1, (g0, g1)


def test_compute_gex_risk_free_influencia_o_estagio_final_da_gamma():
    """Complementa o teste de inversão de prêmio: aqui a IV é IDÊNTICA nos
    dois cálculos (fallback fixo, sem prêmio pra inverter) -- só o r do
    d1/gamma final muda. Se desse igual, `risk_free` não estaria chegando
    até o _bsm_gamma final dentro de compute_gex."""
    spot = 5400.0
    options = _mk_options_wide(spot, step=100.0, n=6)
    iv_fixa = {"2026-08-21": 0.30}
    session_date = "2026-07-13"
    r0 = gw.compute_gex(spot, spot, options, session_date, risk_free=0.0,
                         iv_fallback_by_expiry=iv_fixa, iv_source="realized")
    r1 = gw.compute_gex(spot, spot, options, session_date, risk_free=0.30,
                         iv_fallback_by_expiry=iv_fixa, iv_source="realized")
    assert r0["gamma_flip_ibov"] is not None and r1["gamma_flip_ibov"] is not None
    assert r0["gamma_flip_ibov"] != r1["gamma_flip_ibov"], (
        r0["gamma_flip_ibov"], r1["gamma_flip_ibov"])


def test_compute_gex_f_sanity_clamp_forca_f_1_quando_foge_da_faixa():
    spot = 5400.0
    win_settle = spot * 1.02   # 2% de diferença -> muito além do clamp de 0.5%
    options = _mk_options(spot=spot, strike_step=50, expiry="2026-08-21")
    result = gw.compute_gex(spot, win_settle, options, "2026-07-13", f_sanity_clamp=0.005)
    assert result["conv_factor"] == 1.0, result["conv_factor"]


def test_compute_gex_f_sanity_clamp_none_nao_interfere_no_basis_real_do_ibov():
    spot = 130000.0
    win_settle = spot * 1.012   # basis real do WIN (carry) -- IBOV/WIN não usa clamp
    options = _mk_options(spot=spot, strike_step=1000, expiry="2026-08-21")
    result = gw.compute_gex(spot, win_settle, options, "2026-07-13", f_sanity_clamp=None)
    assert abs(result["conv_factor"] - 1.012) < 1e-9, result["conv_factor"]


def test_compute_gex_grid_step_alimenta_o_gate_liquid_strikes():
    """GRID_STEP não é cosmético (decisão do painel, Task #15, Q3): alimenta
    o gate `liquid`/`valid` direto. Mesmo conjunto de opções, grid_step maior
    conta mais strikes como líquidos (janela 5*grid_step mais larga)."""
    spot = 5400.0
    options = _mk_options_wide(spot)
    session_date = "2026-07-13"
    largo = gw.compute_gex(spot, spot, options, session_date, grid_step=1000.0)
    apertado = gw.compute_gex(spot, spot, options, session_date, grid_step=50.0)
    assert largo["liquid_strikes"] > apertado["liquid_strikes"], (
        largo["liquid_strikes"], apertado["liquid_strikes"])


def test_flip_fora_dos_extremos_pontuais_e_alerta_mas_nao_invalida_gex():
    """Flip é zero do acumulado; max/min são extremos pontuais por strike.

    Não existe invariante matemática que obrigue a coordenada do cruzamento
    acumulado a ficar entre as coordenadas dos extremos pontuais.
    """
    options = []
    for strike in range(100, 112):
        options.append({
            "ticker": f"O{strike}",
            "oi": 100.0 if strike == 100 else 10.0,
            "strike": float(strike),
            "is_call": strike == 100,
            "expiry": "2026-08-21",
            "premium": None,
        })
    original_gamma = gw._bsm_gamma
    gw._bsm_gamma = lambda *_args: 1.0
    try:
        result = gw.compute_gex(
            105.0, 105.0, options, "2026-07-13", grid_step=1.0,
        )
    finally:
        gw._bsm_gamma = original_gamma

    assert result["gamma_flip_ibov"] > result["gamma_max_ibov"]
    assert result["liquid_strikes"] >= 8
    assert result["valid"] is True
    assert "gamma_flip_not_between_pointwise_extrema" in (
        result["meta"]["diagnostic_warnings"]
    )


def test_flip_escolhe_cruzamento_cumulativo_mais_proximo_do_spot():
    """Com múltiplos zero-crossings, preserva a regra formal por proximidade.

    Cenário: uma call gigante isolada no strike 103 cria um cruzamento
    neg->pos longe do spot (~102.6, |102.6-110|=7.4); um put grande no strike
    110 cria um segundo cruzamento pos->neg bem perto do spot (~109.83,
    |109.83-110|=0.17). A definição versionada de Gamma Flip escolhe o zero
    cumulativo mais próximo do spot, sem introduzir filtro direcional.
    """
    options = [
        {"ticker": "P100", "oi": 100.0, "strike": 100.0, "is_call": False,
         "expiry": "2026-08-21", "premium": None},
        {"ticker": "P101", "oi": 10.0, "strike": 101.0, "is_call": False,
         "expiry": "2026-08-21", "premium": None},
        {"ticker": "P102", "oi": 10.0, "strike": 102.0, "is_call": False,
         "expiry": "2026-08-21", "premium": None},
        {"ticker": "C103", "oi": 200.0, "strike": 103.0, "is_call": True,
         "expiry": "2026-08-21", "premium": None},
        {"ticker": "P104", "oi": 5.0, "strike": 104.0, "is_call": False,
         "expiry": "2026-08-21", "premium": None},
        {"ticker": "P105", "oi": 5.0, "strike": 105.0, "is_call": False,
         "expiry": "2026-08-21", "premium": None},
        {"ticker": "P106", "oi": 5.0, "strike": 106.0, "is_call": False,
         "expiry": "2026-08-21", "premium": None},
        {"ticker": "P107", "oi": 5.0, "strike": 107.0, "is_call": False,
         "expiry": "2026-08-21", "premium": None},
        {"ticker": "P108", "oi": 5.0, "strike": 108.0, "is_call": False,
         "expiry": "2026-08-21", "premium": None},
        {"ticker": "P109", "oi": 5.0, "strike": 109.0, "is_call": False,
         "expiry": "2026-08-21", "premium": None},
        {"ticker": "P110", "oi": 60.0, "strike": 110.0, "is_call": False,
         "expiry": "2026-08-21", "premium": None},
        {"ticker": "P111", "oi": 5.0, "strike": 111.0, "is_call": False,
         "expiry": "2026-08-21", "premium": None},
        {"ticker": "P112", "oi": 5.0, "strike": 112.0, "is_call": False,
         "expiry": "2026-08-21", "premium": None},
        {"ticker": "P113", "oi": 5.0, "strike": 113.0, "is_call": False,
         "expiry": "2026-08-21", "premium": None},
    ]
    original_gamma = gw._bsm_gamma
    gw._bsm_gamma = lambda *_args: 1.0
    try:
        result = gw.compute_gex(110.0, 110.0, options, "2026-07-13", grid_step=1.0)
    finally:
        gw._bsm_gamma = original_gamma

    flip = result["gamma_flip_ibov"]
    crossing_neg_to_pos = 102.0 + 1.0 * 120.0 / 200.0       # ~102.6
    crossing_pos_to_neg = 109.0 + 1.0 * 50.0 / 60.0         # ~109.83

    # O pos->neg é genuinamente o crossing mais próximo e precisa vencer a
    # seleção global por distância.
    assert abs(spot_distance := abs(110.0 - crossing_pos_to_neg)) < abs(
        110.0 - crossing_neg_to_pos
    ), spot_distance

    assert flip is not None
    assert math.isclose(flip, crossing_pos_to_neg, abs_tol=1e-9), flip
    assert not math.isclose(flip, crossing_neg_to_pos, abs_tol=1e-6), flip


def test_grid_de_walls_ancorado_no_spot_mesmo_com_flip_put_heavy_longe():
    """O grid de referência precisa cobrir o preço negociado.

    Num mercado put-heavy, o Flip pode ficar muito distante do spot. Gamma
    Max/Min/Flip continuam como níveis calculados próprios, mas o grid de
    walls precisa manter suportes e resistências visíveis dos dois lados do
    preço. Sob a fórmula revertida, centrada no Flip, o intervalo sai
    [121, 137] e deixa o spot 105 fora do gráfico útil.
    """
    options = []
    for strike in range(100, 130):  # 30 strikes de put, todos com OI -- cumulativo
        options.append({                                      # bem negativo até 130
            "ticker": f"P{strike}", "oi": 10.0, "strike": float(strike),
            "is_call": False, "expiry": "2026-08-21", "premium": None,
        })
    options.append({  # call isolada e gigante bem longe do spot -- só ali o
        "ticker": "C130", "oi": 5000.0, "strike": 130.0,   # cumulativo vira positivo
        "is_call": True, "expiry": "2026-08-21", "premium": None,
    })
    original_gamma = gw._bsm_gamma
    gw._bsm_gamma = lambda *_args: 1.0
    try:
        result = gw.compute_gex(105.0, 105.0, options, "2026-07-13", grid_step=1.0)
    finally:
        gw._bsm_gamma = original_gamma

    flip = result["gamma_flip_ibov"]
    assert flip is not None
    assert flip - 105.0 > 15, flip  # put-heavy empurra o flip bem longe do spot

    walls = [w for w in result["walls"] if w["type"] == "wall"]
    assert len(walls) == 17
    prices = sorted(w["price"] for w in walls)
    assert (prices[0], prices[-1]) == (97, 113)  # centro=round(105/1)*1=105, ±8
    assert prices[0] <= 105 <= prices[-1]
    assert not (prices[0] <= flip <= prices[-1])


# ── Slice 3: orquestração de main() (painel Task #15) ───────────────────────

def test_main_isola_falha_por_target_e_so_notifica_se_salvou_algo():
    """Cobertura de orquestração de main(), apontada como lacuna no review
    codex: (a) uma exceção processando UM target não pode impedir o outro de
    ser computado/salvo (try/except por target); (b) o aviso pra API
    (notify_update) só dispara se `saved_any` -- não `exit_code==0`, senão
    uma falha parcial nunca invalidaria o cache; (c) sem --date, o WIN$N
    reusa os oi_rows já buscados por last_session_with_oi() em vez de
    refazer o round-trip HTTP pro BDI; (d) uma única sessão MT5 e uma única
    conexão DB são abertas e compartilhadas entre os dois targets."""
    calls = {"fetch_bdi_oi": [], "save": [], "notify": 0, "load_mt5": 0, "get_conn": 0}

    class _FakeMT5Handle:
        def shutdown(self):
            pass

    class _FakeConn:
        def close(self):
            pass
        def rollback(self):
            pass

    def fake_load_mt5_terminal():
        calls["load_mt5"] += 1
        return _FakeMT5Handle()

    def fake_get_connection(db):
        calls["get_conn"] += 1
        return _FakeConn()

    def fake_last_session_with_oi(max_back=5):
        return "2026-07-13", [{"ticker": "IBOVFAKE", "oi": 100.0}]

    def fake_fetch_bdi_oi(session_date, asset="IBOV"):
        calls["fetch_bdi_oi"].append(asset)
        return [{"ticker": "DOLFAKE", "oi": 50.0}]

    def fake_fetch_bdi_option_data(oi_rows, session_date, asset):
        return [{"ticker": "DOLFAKEOPT", "oi": 50.0, "strike": 5400.0,
                  "is_call": True, "expiry": "2026-08-21", "premium": None}]

    def fake_fetch_ibov_mt5_leg(mt5, oi_rows, session_date, trust_session_close=True):
        raise RuntimeError("MT5 indisponível pro WIN$N nesse teste")

    def fake_fetch_dol_mt5_leg(mt5, session_date):
        return {"spot": 5400.0, "future_settle": 5401.0}

    def fake_infer_grid_step(options, spot, default=None):
        return 50.0

    def fake_realized_iv_by_expiry(conn, symbol, session_date, expiries,
                                    min_window=10, max_window=60):
        return {}

    def fake_compute_gex(spot, future_settle, options, session_date, **kw):
        return {"gamma_max_ibov": 1.0, "gamma_min_ibov": -1.0, "gamma_flip_ibov": 0.0,
                "gamma_max": spot, "gamma_min": spot, "gamma_flip": spot,
                "spot": spot, "future_settle": future_settle, "conv_factor": 1.0,
                "n_strikes": 5, "liquid_strikes": 5, "valid": True, "walls": [],
                "meta": {"iv_by_exp": {}, "iv_fallback": 0.2, "iv_source": "realized",
                         "grid_step": 50.0, "risk_free": 0.0}}

    def fake_save(conn, session_date, result, target="WIN$N"):
        calls["save"].append(target)

    def fake_urlopen(req, timeout=2):
        calls["notify"] += 1

    orig = dict(
        load_mt5_terminal=gw.load_mt5_terminal, get_connection=gw.get_connection,
        _observed_win_session_pair=gw._observed_win_session_pair,
        _validate_official_source=gw._validate_official_source,
        _official_rate=gw._official_rate,
        compute_official_win_snapshot=gw.compute_official_win_snapshot,
        last_session_with_oi=gw.last_session_with_oi, fetch_bdi_oi=gw.fetch_bdi_oi,
        fetch_bdi_option_data=gw.fetch_bdi_option_data,
        fetch_ibov_mt5_leg=gw.fetch_ibov_mt5_leg, fetch_dol_mt5_leg=gw.fetch_dol_mt5_leg,
        infer_grid_step=gw.infer_grid_step, realized_iv_by_expiry=gw.realized_iv_by_expiry,
        compute_gex=gw.compute_gex, save=gw.save, urlopen=gw.urllib.request.urlopen,
        argv=sys.argv,
    )
    gw.load_mt5_terminal = fake_load_mt5_terminal
    gw.get_connection = fake_get_connection
    gw._observed_win_session_pair = lambda conn, effective: ("2026-07-13", effective)
    gw._validate_official_source = lambda source, cache_dir: source
    gw._official_rate = lambda source, cache_dir: (source, 0.149)
    gw.compute_official_win_snapshot = _raise("bundle oficial indisponível pro WIN$N nesse teste")
    gw.last_session_with_oi = fake_last_session_with_oi
    gw.fetch_bdi_oi = fake_fetch_bdi_oi
    gw.fetch_bdi_option_data = fake_fetch_bdi_option_data
    gw.fetch_ibov_mt5_leg = fake_fetch_ibov_mt5_leg
    gw.fetch_dol_mt5_leg = fake_fetch_dol_mt5_leg
    gw.infer_grid_step = fake_infer_grid_step
    gw.realized_iv_by_expiry = fake_realized_iv_by_expiry
    gw.compute_gex = fake_compute_gex
    gw.save = fake_save
    gw.urllib.request.urlopen = fake_urlopen
    sys.argv = ["gex_worker.py"]
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

    assert exit_code == 1, "WIN$N falhou -> exit_code tem que refletir isso"
    assert calls["save"] == ["WDO$N"], (
        "WDO$N tinha que salvar mesmo com WIN$N tendo explodido", calls["save"])
    assert calls["notify"] == 1, "saved_any (não exit_code==0) tem que gatear o notify_update"
    assert calls["fetch_bdi_oi"] == ["DOL"], (
        "WIN$N não pode consultar BDI; só a perna DOL chama fetch_bdi_oi",
        calls["fetch_bdi_oi"])
    assert calls["load_mt5"] == 1 and calls["get_conn"] == 1, (
        "uma sessão MT5 e uma conexão DB compartilhadas entre os targets", calls)


def test_main_com_date_explicito_win_usa_oficial_sem_bdi_nem_mt5():
    calls = {"official": [], "bdi": 0, "mt5": 0}

    class _FakeConn:
        def execute(self, sql, *_args):
            class _Cursor:
                def __init__(self, row):
                    self.row = row
                def fetchone(self):
                    return self.row
            if "MIN(date" in sql:
                return _Cursor(("2026-06-02",))
            if "MAX(date" in sql:
                return _Cursor(("2026-06-01",))
            return _Cursor((1,))
        def close(self):
            pass
        def rollback(self):
            pass

    result = {
        "gamma_max_ibov": 1.0, "gamma_min_ibov": -1.0, "gamma_flip_ibov": 0.0,
        "gamma_max": 1.0, "gamma_min": -1.0, "gamma_flip": 0.0,
        "spot": 1.0, "future_settle": 1.0, "conv_factor": 1.0,
        "n_strikes": 10, "liquid_strikes": 10, "valid": True, "walls": [],
        "meta": {},
    }
    orig = dict(
        get_connection=gw.get_connection,
        load_mt5_terminal=gw.load_mt5_terminal,
        fetch_bdi_oi=gw.fetch_bdi_oi,
        _official_rate=gw._official_rate,
        _validate_official_source=gw._validate_official_source,
        compute_official_win_snapshot=gw.compute_official_win_snapshot,
        argv=sys.argv,
    )
    gw.get_connection = lambda db: _FakeConn()
    gw.load_mt5_terminal = lambda: calls.__setitem__("mt5", calls["mt5"] + 1)
    gw.fetch_bdi_oi = lambda *a, **k: calls.__setitem__("bdi", calls["bdi"] + 1)
    gw._official_rate = lambda source, cache: (source, 0.149)
    gw._validate_official_source = lambda source, cache: source
    gw.compute_official_win_snapshot = lambda *a, **k: (calls["official"].append(a) or result)
    sys.argv = ["gex_worker.py", "--date", "2026-06-01", "--target", "WIN$N", "--dry-run"]
    try:
        assert gw.main() == 0
    finally:
        _restore_main_env(orig)

    assert calls["official"] == [("2026-06-01", "2026-06-02", 0.149, "2026-06-01")]
    assert calls["bdi"] == 0
    assert calls["mt5"] == 0


def test_main_win_bundle_oficial_inconsistente_falha_fechado_sem_publicar():
    calls = {"save": 0, "notify": 0, "mt5": 0}

    class _FakeConn:
        def execute(self, sql, *_args):
            class _Cursor:
                def __init__(self, row):
                    self.row = row
                def fetchone(self):
                    return self.row
            if "MIN(date" in sql:
                return _Cursor(("2026-07-16",))
            if "MAX(date" in sql:
                return _Cursor(("2026-07-15",))
            return _Cursor((1,))
        def close(self):
            pass
        def rollback(self):
            pass

    orig = dict(
        get_connection=gw.get_connection,
        load_mt5_terminal=gw.load_mt5_terminal,
        _official_rate=gw._official_rate,
        _validate_official_source=gw._validate_official_source,
        compute_official_win_snapshot=gw.compute_official_win_snapshot,
        save=gw.save,
        urlopen=gw.urllib.request.urlopen,
        argv=sys.argv,
    )
    gw.get_connection = lambda db: _FakeConn()
    gw.load_mt5_terminal = lambda: calls.__setitem__("mt5", calls["mt5"] + 1)
    gw._official_rate = lambda source, cache: (source, 0.149)
    gw._validate_official_source = lambda source, cache: source
    gw.compute_official_win_snapshot = _raise("bundle oficial incompleto")
    gw.save = lambda *a, **k: calls.__setitem__("save", calls["save"] + 1)
    gw.urllib.request.urlopen = lambda *a, **k: calls.__setitem__("notify", calls["notify"] + 1)
    sys.argv = ["gex_worker.py", "--date", "2026-07-15", "--target", "WIN$N"]
    try:
        assert gw.main() == 1
    finally:
        _restore_main_env(orig)

    assert calls == {"save": 0, "notify": 0, "mt5": 0}


def test_fonte_live_nao_recua_para_bundle_antigo_quando_d1_falha():
    calls = []
    original_download = gw.gex_official.download_b3_bundle
    original_parse = gw.gex_official.parse_official_bundle

    def fake_download(source, cache):
        calls.append(source)
        if source == "2026-07-13":
            raise ValueError("bundle esperado ausente")
        return {"unexpected": source}

    gw.gex_official.download_b3_bundle = fake_download
    gw.gex_official.parse_official_bundle = lambda paths, source: {}
    try:
        try:
            gw._validate_official_source("2026-07-13", "/tmp/cache")
            raise AssertionError("deveria falhar fechado no D-1 esperado")
        except ValueError as exc:
            assert "2026-07-13" in str(exc)
    finally:
        gw.gex_official.download_b3_bundle = original_download
        gw.gex_official.parse_official_bundle = original_parse

    assert calls == ["2026-07-13"], calls


def test_fonte_causal_de_segunda_feira_e_sexta_anterior():
    conn = _win_ledger("2026-07-10", "2026-07-13")
    assert gw._observed_win_session_pair(conn, "2026-07-13") == (
        "2026-07-10", "2026-07-13",
    )


def _win_ledger(*session_dates):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE market_bars(symbol TEXT, timeframe TEXT, timestamp_utc TEXT)"
    )
    conn.executemany(
        "INSERT INTO market_bars VALUES ('WIN$N','M5',?)",
        [(f"{session}T09:00:00",) for session in session_dates],
    )
    return conn


def test_par_causal_pos_feriado_vem_exatamente_do_ledger_win():
    conn = _win_ledger("2026-09-04", "2026-09-08")
    assert gw._observed_win_session_pair(conn, "2026-09-08") == (
        "2026-09-04", "2026-09-08",
    )


def test_sessao_efetiva_sem_barra_win_falha_fechado_como_feriado():
    conn = _win_ledger("2026-09-04")
    try:
        gw._observed_win_session_pair(conn, "2026-09-07")
        raise AssertionError("feriado sem sessão WIN não pode publicar GEX")
    except ValueError as exc:
        assert "sessão WIN efetiva não observada" in str(exc)


def test_date_sem_proxima_sessao_observada_nao_cai_em_today():
    conn = _win_ledger("2026-07-15")
    assert gw._next_observed_win_session(conn, "2026-07-15") is None


def test_main_date_sem_proxima_sessao_nao_calcula_nem_publica():
    calls = {"compute": 0, "save": 0}

    class _FakeConn:
        def execute(self, *_args):
            class _Cursor:
                @staticmethod
                def fetchone():
                    return (None,)
            return _Cursor()
        def close(self):
            pass
        def rollback(self):
            pass

    orig = dict(
        get_connection=gw.get_connection,
        compute_official_win_snapshot=gw.compute_official_win_snapshot,
        save=gw.save,
        argv=sys.argv,
    )
    gw.get_connection = lambda db: _FakeConn()
    gw.compute_official_win_snapshot = lambda *a, **k: calls.__setitem__(
        "compute", calls["compute"] + 1,
    )
    gw.save = lambda *a, **k: calls.__setitem__("save", calls["save"] + 1)
    sys.argv = ["gex_worker.py", "--date", "2026-07-15", "--target", "WIN$N"]
    try:
        assert gw.main() == 1
    finally:
        _restore_main_env(orig)

    assert calls == {"compute": 0, "save": 0}


def test_main_automatico_em_feriado_sem_win_nao_publica():
    calls = {"validate": 0, "compute": 0, "save": 0}

    class _FakeConn:
        def execute(self, *_args):
            class _Cursor:
                @staticmethod
                def fetchone():
                    return None
            return _Cursor()
        def close(self):
            pass
        def rollback(self):
            pass

    orig = dict(
        get_connection=gw.get_connection,
        _validate_official_source=gw._validate_official_source,
        compute_official_win_snapshot=gw.compute_official_win_snapshot,
        save=gw.save,
        argv=sys.argv,
    )
    gw.get_connection = lambda db: _FakeConn()
    gw._validate_official_source = lambda *a: calls.__setitem__(
        "validate", calls["validate"] + 1,
    )
    gw.compute_official_win_snapshot = lambda *a, **k: calls.__setitem__(
        "compute", calls["compute"] + 1,
    )
    gw.save = lambda *a, **k: calls.__setitem__("save", calls["save"] + 1)
    sys.argv = ["gex_worker.py", "--target", "WIN$N"]
    try:
        assert gw.main() == 1
    finally:
        _restore_main_env(orig)

    assert calls == {"validate": 0, "compute": 0, "save": 0}


def test_timer_gex_roda_apos_primeira_barra_win():
    timer = Path("scripts/systemd/rastro-irado-gex.timer").read_text(encoding="utf-8")
    assert "OnCalendar=Mon..Fri *-*-* 09:10:00 America/Sao_Paulo" in timer
    assert "07:30" not in timer


def _raise(msg):
    """Fábrica de callable que estoura RuntimeError(msg) -- lambda não
    aceita `raise` no corpo, e os testes de main() precisam simular uma
    perna MT5 indisponível via monkeypatch."""
    def _fn(*a, **k):
        raise RuntimeError(msg)
    return _fn


def _patch_main_success_env(calls, extra_argv=None):
    """Monta um ambiente onde os dois targets teriam sucesso (spot/settle e
    compute_gex válidos) e devolve o dict `orig` pra restaurar depois --
    reusado pelos testes de exit_code==0 e de dry-run (Review codex: gap #2
    remanescente, ambos os cenários exigem a MESMA cadeia de sucesso, só
    variando --dry-run)."""
    class _FakeMT5Handle:
        def shutdown(self):
            pass

    class _FakeConn:
        def close(self):
            pass
        def rollback(self):
            pass

    def fake_result(spot, future_settle):
        return {"gamma_max_ibov": 1.0, "gamma_min_ibov": -1.0, "gamma_flip_ibov": 0.0,
                "gamma_max": spot, "gamma_min": spot, "gamma_flip": spot,
                "spot": spot, "future_settle": future_settle, "conv_factor": 1.0,
                "n_strikes": 5, "liquid_strikes": 5, "valid": True, "walls": [],
                "meta": {"iv_by_exp": {}, "iv_fallback": 0.2, "iv_source": "realized",
                         "grid_step": 50.0, "risk_free": 0.0}}

    orig = dict(
        load_mt5_terminal=gw.load_mt5_terminal, get_connection=gw.get_connection,
        _observed_win_session_pair=gw._observed_win_session_pair,
        _validate_official_source=gw._validate_official_source,
        _official_rate=gw._official_rate,
        compute_official_win_snapshot=gw.compute_official_win_snapshot,
        last_session_with_oi=gw.last_session_with_oi, fetch_bdi_oi=gw.fetch_bdi_oi,
        fetch_bdi_option_data=gw.fetch_bdi_option_data,
        fetch_ibov_mt5_leg=gw.fetch_ibov_mt5_leg, fetch_dol_mt5_leg=gw.fetch_dol_mt5_leg,
        infer_grid_step=gw.infer_grid_step, realized_iv_by_expiry=gw.realized_iv_by_expiry,
        compute_gex=gw.compute_gex, save=gw.save, save_history_result=gw.save_history_result,
        existing_validity=gw.existing_validity, decide_persistence=gw.decide_persistence,
        urlopen=gw.urllib.request.urlopen,
        argv=sys.argv,
    )
    gw.load_mt5_terminal = lambda: _FakeMT5Handle()
    gw.get_connection = lambda db: _FakeConn()
    gw._observed_win_session_pair = lambda conn, effective: ("2026-07-13", effective)
    gw._validate_official_source = lambda source, cache_dir: source
    gw._official_rate = lambda source, cache_dir: (source, 0.149)
    gw.compute_official_win_snapshot = (
        lambda *a, **k: fake_result(130000.0, 131500.0))
    gw.last_session_with_oi = lambda max_back=5: (
        "2026-07-13", [{"ticker": "IBOVFAKE", "oi": 100.0}])
    gw.fetch_bdi_oi = lambda session_date, asset="IBOV": [{"ticker": "DOLFAKE", "oi": 50.0}]
    gw.fetch_bdi_option_data = lambda oi_rows, session_date, asset: [
        {"ticker": "DOLFAKEOPT", "oi": 50.0, "strike": 5400.0,
         "is_call": True, "expiry": "2026-08-21", "premium": None}]
    gw.fetch_ibov_mt5_leg = lambda mt5, oi_rows, session_date, trust_session_close=True: {
        "spot": 130000.0, "win_settle": 131500.0, "options": []}
    gw.fetch_dol_mt5_leg = lambda mt5, session_date: {"spot": 5400.0, "future_settle": 5401.0}
    gw.infer_grid_step = lambda options, spot, default=None: 50.0
    gw.realized_iv_by_expiry = (
        lambda conn, symbol, session_date, expiries, min_window=10, max_window=60: {})
    gw.compute_gex = (
        lambda spot, future_settle, options, session_date, **kw: fake_result(spot, future_settle))
    gw.save = (
        lambda conn, session_date, result, target="WIN$N", commit=True:
            calls["save"].append(target))
    gw.save_history_result = (
        lambda conn, source, effective, result, target="WIN$N", commit=True:
            calls.setdefault("save_history", []).append((source, effective, target)))
    # sem histórico prévio no ambiente fake -- decide_persistence real (não
    # mockado) resolve pra insert_valid/insert_invalid, exercendo a mesma
    # política de produção sem exigir um _FakeConn com execute() de verdade.
    gw.existing_validity = lambda conn, source_session_date, target="WIN$N": None
    gw.urllib.request.urlopen = (
        lambda req, timeout=2: calls.__setitem__("notify", calls["notify"] + 1))
    sys.argv = ["gex_worker.py"] + (extra_argv or [])
    return orig


def _restore_main_env(orig):
    for k, v in orig.items():
        if k == "urlopen":
            gw.urllib.request.urlopen = v
        elif k == "argv":
            sys.argv = v
        else:
            setattr(gw, k, v)


def test_main_sucesso_completo_retorna_exit_code_0_e_notifica_uma_vez():
    """Review codex: os testes de orquestração anteriores só cobriam falha
    parcial (exit_code==1). Sem este teste, inicializar exit_code=1 por
    engano no topo de main() passaria despercebido nos outros dois testes
    -- o scheduler veria todo run 100% bem-sucedido como falho."""
    calls = {"save": [], "notify": 0}
    orig = _patch_main_success_env(calls)
    try:
        exit_code = gw.main()
    finally:
        _restore_main_env(orig)

    assert exit_code == 0, f"os dois targets tiveram sucesso -- exit_code tinha que ser 0, veio {exit_code}"
    assert calls["save"] == ["WIN$N", "WDO$N"], calls["save"]
    assert calls["notify"] == 1, calls["notify"]
    assert calls.get("save_history") == [("2026-07-13", date.today().isoformat(), "WIN$N")], (
        "F4: WIN$N tem que gravar em gex_history_levels com source/effective corretos, "
        "e WDO$N NAO tem esse conceito -- não pode aparecer aqui: " + repr(calls.get("save_history")))


def test_main_dry_run_nao_grava_nem_notifica_mesmo_com_sucesso():
    """Review codex: --dry-run com sucesso total é o outro lado do gate
    `saved_any` -- save() nunca é chamado (o `continue` do dry-run vem antes
    dele), então saved_any fica False e notify_update não pode disparar,
    mesmo o run inteiro sendo bem-sucedido (exit_code==0)."""
    calls = {"save": [], "notify": 0}
    orig = _patch_main_success_env(calls, extra_argv=["--dry-run"])
    try:
        exit_code = gw.main()
    finally:
        _restore_main_env(orig)

    assert exit_code == 0, exit_code
    assert calls["save"] == [], "--dry-run não pode gravar nada" + repr(calls["save"])
    assert calls["notify"] == 0, "--dry-run não pode notificar a API (nada foi salvo)"
    assert not calls.get("save_history"), (
        "--dry-run não pode gravar em gex_history_levels: " + repr(calls.get("save_history")))


def test_main_ambos_targets_falhando_nao_salva_nem_notifica():
    """Review codex: complementa o teste de falha parcial -- com os DOIS
    targets explodindo, saved_any nunca vira True em nenhum momento do
    loop, então notify_update não pode disparar (e exit_code tem que
    refletir a falha total, não só a de um target)."""
    calls = {"save": [], "notify": 0}

    class _FakeMT5Handle:
        def shutdown(self):
            pass

    class _FakeConn:
        def close(self):
            pass
        def rollback(self):
            pass

    orig = dict(
        load_mt5_terminal=gw.load_mt5_terminal, get_connection=gw.get_connection,
        _observed_win_session_pair=gw._observed_win_session_pair,
        _validate_official_source=gw._validate_official_source,
        _official_rate=gw._official_rate,
        compute_official_win_snapshot=gw.compute_official_win_snapshot,
        last_session_with_oi=gw.last_session_with_oi, fetch_bdi_oi=gw.fetch_bdi_oi,
        fetch_bdi_option_data=gw.fetch_bdi_option_data,
        fetch_ibov_mt5_leg=gw.fetch_ibov_mt5_leg, fetch_dol_mt5_leg=gw.fetch_dol_mt5_leg,
        save=gw.save, urlopen=gw.urllib.request.urlopen, argv=sys.argv,
    )
    gw.load_mt5_terminal = lambda: _FakeMT5Handle()
    gw.get_connection = lambda db: _FakeConn()
    gw._observed_win_session_pair = lambda conn, effective: ("2026-07-13", effective)
    gw._validate_official_source = lambda source, cache_dir: source
    gw._official_rate = lambda source, cache_dir: (source, 0.149)
    gw.compute_official_win_snapshot = _raise("bundle oficial indisponível")
    gw.last_session_with_oi = lambda max_back=5: (
        "2026-07-13", [{"ticker": "IBOVFAKE", "oi": 100.0}])
    gw.fetch_bdi_oi = lambda session_date, asset="IBOV": [{"ticker": "DOLFAKE", "oi": 50.0}]
    # sem isto, a fetch_bdi_option_data REAL roda (com HTTP de verdade, indisponível
    # no sandbox) antes de fetch_dol_mt5_leg estourar -- e como o monkeypatch de
    # urlopen abaixo é o módulo urllib.request global (não um objeto isolado por
    # teste), aquela chamada de rede real incrementaria "notify" por engano.
    gw.fetch_bdi_option_data = lambda oi_rows, session_date, asset: []
    gw.fetch_ibov_mt5_leg = _raise("MT5 indisponível pro WIN$N nesse teste")
    gw.fetch_dol_mt5_leg = _raise("MT5 indisponível pro WDO$N nesse teste")
    gw.save = (
        lambda conn, session_date, result, target="WIN$N", commit=True:
            calls["save"].append(target))
    gw.urllib.request.urlopen = (
        lambda req, timeout=2: calls.__setitem__("notify", calls["notify"] + 1))
    sys.argv = ["gex_worker.py"]
    try:
        exit_code = gw.main()
    finally:
        _restore_main_env(orig)

    assert exit_code == 1, exit_code
    assert calls["save"] == [], calls["save"]
    assert calls["notify"] == 0, "nada foi salvo -- notify_update não podia disparar"


def test_main_win_grava_mesmo_snapshot_causal_em_gex_levels_e_history_e_e_idempotente():
    """F4 (tri-r): antes desta mudança só o backfill manual gravava
    gex_history_levels -- o worker EOD agendado nunca escrevia lá, então
    sessões recém-fechadas ficavam sem histórico até alguém rodar o backfill
    à mão. Roda main() de ponta a ponta contra um sqlite REAL (save() e
    save_history_result() não mockados) duas vezes seguidas e confere: (1) na
    PRIMEIRA rodada, WIN$N grava o MESMO result em gex_levels e
    gex_history_levels, com source/effective corretos; (2) WDO$N (sem PIT)
    não grava em gex_history_levels; (3) na SEGUNDA rodada (revisão
    tri-r/fable-reasoner: decide_persistence), gex_levels (LIVE) continua
    refletindo sempre o resultado mais fresco, mas gex_history_levels (PIT)
    NÃO é reescrito -- o snapshot válido já arquivado na primeira rodada é
    preservado, não substituído por um rerun automático sem --replace. Isso é
    ainda mais forte que "sobrescreve sem duplicar": o histórico, uma vez
    válido, fica imutável a reruns automáticos."""
    db_path = tempfile.mktemp(suffix=".db")

    class _FakeMT5Handle:
        def shutdown(self):
            pass

    def fake_win_result(spot, future_settle):
        return {"gamma_max_ibov": 1.0, "gamma_min_ibov": -1.0, "gamma_flip_ibov": 0.0,
                "gamma_max": spot, "gamma_min": spot, "gamma_flip": spot,
                "spot": spot, "future_settle": future_settle, "conv_factor": 1.0,
                "n_strikes": 5, "liquid_strikes": 5, "valid": True, "walls": [],
                "meta": {"iv_by_exp": {}, "iv_fallback": 0.2, "iv_source": "realized",
                         "grid_step": 50.0, "risk_free": 0.0}}

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
    gw._observed_win_session_pair = lambda conn, effective: ("2026-07-13", effective)
    gw._validate_official_source = lambda source, cache_dir: source
    gw._official_rate = lambda source, cache_dir: (source, 0.149)
    gw.compute_official_win_snapshot = lambda *a, **k: fake_win_result(130000.0, 131500.0)
    gw.last_session_with_oi = lambda max_back=5: (
        "2026-07-13", [{"ticker": "IBOVFAKE", "oi": 100.0}])
    gw.fetch_bdi_oi = lambda session_date, asset="IBOV": [{"ticker": "DOLFAKE", "oi": 50.0}]
    gw.fetch_bdi_option_data = lambda oi_rows, session_date, asset: [
        {"ticker": "DOLFAKEOPT", "oi": 50.0, "strike": 5400.0,
         "is_call": True, "expiry": "2026-08-21", "premium": None}]
    gw.fetch_ibov_mt5_leg = lambda mt5, oi_rows, session_date, trust_session_close=True: {
        "spot": 130000.0, "win_settle": 131500.0, "options": []}
    gw.fetch_dol_mt5_leg = lambda mt5, session_date: {"spot": 5400.0, "future_settle": 5401.0}
    gw.infer_grid_step = lambda options, spot, default=None: 50.0
    gw.realized_iv_by_expiry = (
        lambda conn, symbol, session_date, expiries, min_window=10, max_window=60: {})
    gw.compute_gex = (
        lambda spot, future_settle, options, session_date, **kw: fake_win_result(spot, future_settle))
    gw.urllib.request.urlopen = lambda req, timeout=2: None
    sys.argv = ["gex_worker.py", "--db", db_path]
    try:
        exit_code_1 = gw.main()
        # confere ANTES do segundo run que a primeira rodada gravou o MESMO
        # snapshot causal nas duas tabelas -- é essa igualdade que decide_
        # persistence passa a proteger de reruns automáticos a partir daqui.
        conn_1 = sqlite3.connect(db_path)
        conn_1.row_factory = sqlite3.Row
        live_1 = conn_1.execute("SELECT * FROM gex_levels WHERE target='WIN$N'").fetchone()
        hist_1 = conn_1.execute("SELECT * FROM gex_history_levels WHERE target='WIN$N'").fetchone()
        conn_1.close()
        for col in ("gamma_max", "gamma_min", "gamma_flip", "gamma_max_ibov",
                    "gamma_min_ibov", "gamma_flip_ibov", "spot", "future_settle",
                    "conv_factor", "n_strikes", "valid"):
            assert live_1[col] == hist_1[col], (
                "primeira rodada: mesmo snapshot causal exige valores iguais em "
                "gex_levels e gex_history_levels: " + repr((col, live_1[col], hist_1[col])))

        # segunda rodada com spot diferente, mas SEM --replace -- prova que
        # gex_levels (LIVE) continua sempre fresco, enquanto o histórico já
        # válido da primeira rodada fica intacto (decide_persistence).
        gw.compute_official_win_snapshot = lambda *a, **k: fake_win_result(131000.0, 132500.0)
        exit_code_2 = gw.main()
    finally:
        for k, v in orig.items():
            if k == "urlopen":
                gw.urllib.request.urlopen = v
            elif k == "argv":
                sys.argv = v
            else:
                setattr(gw, k, v)

    assert exit_code_1 == 0 and exit_code_2 == 0, (exit_code_1, exit_code_2)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    live_rows = conn.execute("SELECT * FROM gex_levels WHERE target='WIN$N'").fetchall()
    hist_rows = conn.execute("SELECT * FROM gex_history_levels WHERE target='WIN$N'").fetchall()
    hist_dol = conn.execute("SELECT * FROM gex_history_levels WHERE target='WDO$N'").fetchone()
    conn.close()

    assert len(live_rows) == 1, "rerun tinha que SOBRESCREVER gex_levels, não duplicar: " + repr(live_rows)
    assert len(hist_rows) == 1, (
        "rerun não pode duplicar a linha em gex_history_levels: " + repr(hist_rows))
    assert hist_dol is None, "WDO$N não tem PIT source/effective -- não pode gravar histórico"

    live, hist = live_rows[0], hist_rows[0]
    assert hist["source_session_date"] == "2026-07-13", dict(hist)
    assert hist["effective_session_date"] == date.today().isoformat(), dict(hist)
    assert live["spot"] == 131000.0, (
        "gex_levels (LIVE) tem que refletir a SEGUNDA rodada, mesmo sem --replace")
    assert hist["spot"] == 130000.0, (
        "gex_history_levels tem que preservar o snapshot VÁLIDO da PRIMEIRA rodada -- "
        "decide_persistence não deixa um rerun automático (sem --replace) degradar/trocar "
        "um histórico já válido: " + repr(dict(hist)))


def _run_main_win_twice_para_politica_de_sobrescrita(second_result, *, extra_argv_2=None):
    """Monta o ambiente comum aos dois testes de decide_persistence abaixo:
    primeira rodada sempre grava um snapshot WIN$N VÁLIDO; a segunda usa o
    `second_result` (e os argv extras) passados pelo chamador. Devolve
    (db_path, exit_code_1, exit_code_2)."""
    db_path = tempfile.mktemp(suffix=".db")

    class _FakeMT5Handle:
        def shutdown(self):
            pass

    def fake_win_result(spot, future_settle, valid=True):
        return {"gamma_max_ibov": 1.0, "gamma_min_ibov": -1.0, "gamma_flip_ibov": 0.0,
                "gamma_max": spot, "gamma_min": spot, "gamma_flip": spot,
                "spot": spot, "future_settle": future_settle, "conv_factor": 1.0,
                "n_strikes": 5, "liquid_strikes": 5, "valid": valid, "walls": [],
                "meta": {"iv_by_exp": {}, "iv_fallback": 0.2, "iv_source": "realized",
                         "grid_step": 50.0, "risk_free": 0.0}}

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
    gw._observed_win_session_pair = lambda conn, effective: ("2026-07-13", effective)
    gw._validate_official_source = lambda source, cache_dir: source
    gw._official_rate = lambda source, cache_dir: (source, 0.149)
    gw.compute_official_win_snapshot = lambda *a, **k: fake_win_result(130000.0, 131500.0)
    gw.last_session_with_oi = lambda max_back=5: (
        "2026-07-13", [{"ticker": "IBOVFAKE", "oi": 100.0}])
    gw.fetch_bdi_oi = lambda session_date, asset="IBOV": [{"ticker": "DOLFAKE", "oi": 50.0}]
    gw.fetch_bdi_option_data = lambda oi_rows, session_date, asset: [
        {"ticker": "DOLFAKEOPT", "oi": 50.0, "strike": 5400.0,
         "is_call": True, "expiry": "2026-08-21", "premium": None}]
    gw.fetch_ibov_mt5_leg = lambda mt5, oi_rows, session_date, trust_session_close=True: {
        "spot": 130000.0, "win_settle": 131500.0, "options": []}
    gw.fetch_dol_mt5_leg = lambda mt5, session_date: {"spot": 5400.0, "future_settle": 5401.0}
    gw.infer_grid_step = lambda options, spot, default=None: 50.0
    gw.realized_iv_by_expiry = (
        lambda conn, symbol, session_date, expiries, min_window=10, max_window=60: {})
    gw.compute_gex = (
        lambda spot, future_settle, options, session_date, **kw: fake_win_result(spot, future_settle))
    gw.urllib.request.urlopen = lambda req, timeout=2: None
    sys.argv = ["gex_worker.py", "--db", db_path]
    try:
        exit_code_1 = gw.main()
        gw.compute_official_win_snapshot = lambda *a, **k: second_result
        sys.argv = ["gex_worker.py", "--db", db_path] + (extra_argv_2 or [])
        exit_code_2 = gw.main()
    finally:
        for k, v in orig.items():
            if k == "urlopen":
                gw.urllib.request.urlopen = v
            elif k == "argv":
                sys.argv = v
            else:
                setattr(gw, k, v)
    return db_path, exit_code_1, exit_code_2, fake_win_result


def test_main_win_historico_valido_nao_e_degradado_por_rerun_invalido_sem_replace():
    """F4 (decisão do usuário sobre o achado ALTO do fable-reasoner): um
    snapshot histórico VÁLIDO não pode ser degradado silenciosamente por uma
    rerrodada automática com fonte incompleta (ex.: menos strikes líquidos
    no dia seguinte, valid=False). decide_persistence bloqueia mesmo sem
    nenhuma flag no CLI -- não é preciso pedir --replace pra recusar, o
    bloqueio é o comportamento padrão."""
    invalid_second = {
        "gamma_max_ibov": 1.0, "gamma_min_ibov": -1.0, "gamma_flip_ibov": 0.0,
        "gamma_max": 131000.0, "gamma_min": 131000.0, "gamma_flip": 131000.0,
        "spot": 131000.0, "future_settle": 132500.0, "conv_factor": 1.0,
        "n_strikes": 2, "liquid_strikes": 2, "valid": False, "walls": [],
        "meta": {"iv_by_exp": {}, "iv_fallback": 0.2, "iv_source": "realized",
                 "grid_step": 50.0, "risk_free": 0.0},
    }
    db_path, exit_code_1, exit_code_2, _ = _run_main_win_twice_para_politica_de_sobrescrita(
        invalid_second)

    assert exit_code_1 == 0 and exit_code_2 == 0, (exit_code_1, exit_code_2)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    live = conn.execute("SELECT * FROM gex_levels WHERE target='WIN$N'").fetchone()
    hist = conn.execute("SELECT * FROM gex_history_levels WHERE target='WIN$N'").fetchone()
    conn.close()

    assert live["spot"] == 131000.0 and live["valid"] == 0, (
        "gex_levels (LIVE) reflete a rodada mais fresca mesmo sendo inválida -- "
        "essa tabela não é gated por decide_persistence: " + repr(dict(live)))
    assert hist["spot"] == 130000.0 and hist["valid"] == 1, (
        "gex_history_levels tem que preservar o snapshot VÁLIDO da primeira rodada -- "
        "um candidato inválido NUNCA pode substituir um histórico já válido: "
        + repr(dict(hist)))


def test_main_win_replace_forca_sobrescrita_de_historico_valido():
    """F4 (decisão do usuário): --replace é a válvula de escape explícita
    pra quando o operador SABE que quer forçar a sobrescrita de um snapshot
    histórico já válido (ex.: reprocessamento manual após correção de dado
    na fonte B3) -- sem a flag, decide_persistence bloqueia; com ela, grava."""
    second_valid = {
        "gamma_max_ibov": 1.0, "gamma_min_ibov": -1.0, "gamma_flip_ibov": 0.0,
        "gamma_max": 131000.0, "gamma_min": 131000.0, "gamma_flip": 131000.0,
        "spot": 131000.0, "future_settle": 132500.0, "conv_factor": 1.0,
        "n_strikes": 5, "liquid_strikes": 5, "valid": True, "walls": [],
        "meta": {"iv_by_exp": {}, "iv_fallback": 0.2, "iv_source": "realized",
                 "grid_step": 50.0, "risk_free": 0.0},
    }
    db_path, exit_code_1, exit_code_2, _ = _run_main_win_twice_para_politica_de_sobrescrita(
        second_valid, extra_argv_2=["--replace"])

    assert exit_code_1 == 0 and exit_code_2 == 0, (exit_code_1, exit_code_2)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    hist = conn.execute("SELECT * FROM gex_history_levels WHERE target='WIN$N'").fetchone()
    conn.close()

    assert hist["spot"] == 131000.0, (
        "--replace tem que forçar a sobrescrita do histórico já válido "
        "pelo candidato da segunda rodada: " + repr(dict(hist)))


def test_main_win_falha_entre_save_e_save_history_nao_deixa_linha_orfa_em_gex_levels():
    """F4 (review deep-reasoner/fable-reasoner): save() e save_history_result()
    tinham dois commits independentes -- se save_history_result() explodisse
    DEPOIS de save() já ter persistido gex_levels, a sessão ficava com um
    snapshot em gex_levels sem o par correspondente em gex_history_levels,
    quebrando a garantia de "mesmo snapshot causal nas duas tabelas" do F4.
    Roda main() contra um sqlite REAL (get_connection e save() não mockados,
    só save_history_result força uma exceção) e confere que a linha WIN$N em
    gex_levels foi revertida junto -- tudo ou nada, não meio-escrito."""
    db_path = tempfile.mktemp(suffix=".db")

    class _FakeMT5Handle:
        def shutdown(self):
            pass

    def fake_win_result(spot, future_settle):
        return {"gamma_max_ibov": 1.0, "gamma_min_ibov": -1.0, "gamma_flip_ibov": 0.0,
                "gamma_max": spot, "gamma_min": spot, "gamma_flip": spot,
                "spot": spot, "future_settle": future_settle, "conv_factor": 1.0,
                "n_strikes": 5, "liquid_strikes": 5, "valid": True, "walls": [],
                "meta": {"iv_by_exp": {}, "iv_fallback": 0.2, "iv_source": "realized",
                         "grid_step": 50.0, "risk_free": 0.0}}

    def _raising_save_history_result(conn, source, effective, result, target="WIN$N", commit=True):
        gw.ensure_history_schema(conn)
        raise RuntimeError("falha simulada em save_history_result (teste de atomicidade)")

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
        compute_gex=gw.compute_gex, save_history_result=gw.save_history_result,
        urlopen=gw.urllib.request.urlopen, argv=sys.argv,
    )
    gw.load_mt5_terminal = lambda: _FakeMT5Handle()
    gw._observed_win_session_pair = lambda conn, effective: ("2026-07-13", effective)
    gw._validate_official_source = lambda source, cache_dir: source
    gw._official_rate = lambda source, cache_dir: (source, 0.149)
    gw.compute_official_win_snapshot = lambda *a, **k: fake_win_result(130000.0, 131500.0)
    gw.last_session_with_oi = lambda max_back=5: (
        "2026-07-13", [{"ticker": "IBOVFAKE", "oi": 100.0}])
    gw.fetch_bdi_oi = lambda session_date, asset="IBOV": [{"ticker": "DOLFAKE", "oi": 50.0}]
    gw.fetch_bdi_option_data = lambda oi_rows, session_date, asset: [
        {"ticker": "DOLFAKEOPT", "oi": 50.0, "strike": 5400.0,
         "is_call": True, "expiry": "2026-08-21", "premium": None}]
    gw.fetch_ibov_mt5_leg = lambda mt5, oi_rows, session_date, trust_session_close=True: {
        "spot": 130000.0, "win_settle": 131500.0, "options": []}
    gw.fetch_dol_mt5_leg = lambda mt5, session_date: {"spot": 5400.0, "future_settle": 5401.0}
    gw.infer_grid_step = lambda options, spot, default=None: 50.0
    gw.realized_iv_by_expiry = (
        lambda conn, symbol, session_date, expiries, min_window=10, max_window=60: {})
    gw.compute_gex = (
        lambda spot, future_settle, options, session_date, **kw: fake_win_result(spot, future_settle))
    gw.save_history_result = _raising_save_history_result
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

    assert exit_code == 1, "WIN$N falhou no meio da dupla gravação -- exit_code tem que refletir isso"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    win_live = conn.execute("SELECT * FROM gex_levels WHERE target='WIN$N'").fetchall()
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    # rollback() desfaz a MESMA transação do CREATE TABLE IF NOT EXISTS de
    # ensure_history_schema (DDL é transacional no sqlite) -- é esperado que
    # gex_history_levels nem exista ainda nesse cenário, não só que esteja vazia.
    win_hist = (
        conn.execute("SELECT * FROM gex_history_levels WHERE target='WIN$N'").fetchall()
        if "gex_history_levels" in tables else [])
    dol_live = conn.execute("SELECT * FROM gex_levels WHERE target='WDO$N'").fetchall()
    conn.close()

    assert win_live == [], (
        "save_history_result() falhou DEPOIS de save() -- gex_levels não pode reter "
        "a linha WIN$N sem o par em gex_history_levels (linha órfã): " + repr([dict(r) for r in win_live]))
    assert win_hist == [], "save_history_result() falhou -- não podia ter gravado nada: " + repr(win_hist)
    assert len(dol_live) == 1, (
        "a falha isolada de WIN$N não pode impedir WDO$N (target independente) de gravar: "
        + repr(dol_live))


TESTS = [
    test_fetch_bdi_oi_ibov_usa_ttlpos,
    test_fetch_bdi_oi_dol_usa_opnintrst_nao_ttlpos,
    test_fetch_bdi_oi_ambas_colunas_none_vira_zero_e_descarta,
    test_fetch_bdi_oi_ambas_colunas_preenchidas_ttlpos_prevalece,
    test_fetch_bdi_instruments_ignora_optntp_nao_reconhecido,
    test_fetch_bdi_instruments_so_opcoes_do_asset,
    test_fetch_bdi_option_data_join_e_sem_premio,
    test_infer_grid_step_calcula_mediana_do_gap_perto_do_spot,
    test_infer_grid_step_poucos_strikes_perto_do_spot_usa_default,
    test_infer_grid_step_ignora_strikes_fora_da_moneyness,
    test_infer_grid_step_default_none_significa_falha_explicita_nao_1000_do_ibov,
    test_realized_vol_historico_insuficiente_retorna_none,
    test_realized_vol_usa_ultimo_close_do_dia_nao_o_primeiro,
    test_realized_vol_exclui_o_proprio_dia_da_sessao_lookahead,
    test_realized_iv_by_expiry_clampa_janela_em_min_max,
    test_realized_iv_by_expiry_janela_horizon_matched_pega_regime_certo,
    test_realized_iv_by_expiry_clampa_em_iv_max,
    test_realized_iv_by_expiry_clampa_em_iv_min,
    test_realized_iv_by_expiry_sem_historico_omite_vencimento,
    test_fetch_ibov_mt5_leg_junta_oi_bdi_com_metadados_e_premio_mt5,
    test_fetch_ibov_mt5_leg_sem_trust_session_close_cai_no_fallback_atm,
    test_fetch_dol_mt5_leg_dol_e_spot_wdo_e_settle,
    test_compute_gex_iv_fallback_by_expiry_substitui_020_fixo,
    test_compute_gex_iv_fallback_by_expiry_vazio_nao_vira_020_fixo,
    test_compute_gex_risk_free_e_realmente_usado_no_calculo,
    test_bsm_gamma_usa_risk_free_no_calculo_final,
    test_compute_gex_risk_free_influencia_o_estagio_final_da_gamma,
    test_compute_gex_f_sanity_clamp_forca_f_1_quando_foge_da_faixa,
    test_compute_gex_f_sanity_clamp_none_nao_interfere_no_basis_real_do_ibov,
    test_compute_gex_grid_step_alimenta_o_gate_liquid_strikes,
    test_flip_fora_dos_extremos_pontuais_e_alerta_mas_nao_invalida_gex,
    test_flip_escolhe_cruzamento_cumulativo_mais_proximo_do_spot,
    test_grid_de_walls_ancorado_no_spot_mesmo_com_flip_put_heavy_longe,
    test_main_isola_falha_por_target_e_so_notifica_se_salvou_algo,
    test_main_com_date_explicito_win_usa_oficial_sem_bdi_nem_mt5,
    test_main_win_bundle_oficial_inconsistente_falha_fechado_sem_publicar,
    test_fonte_live_nao_recua_para_bundle_antigo_quando_d1_falha,
    test_fonte_causal_de_segunda_feira_e_sexta_anterior,
    test_par_causal_pos_feriado_vem_exatamente_do_ledger_win,
    test_sessao_efetiva_sem_barra_win_falha_fechado_como_feriado,
    test_date_sem_proxima_sessao_observada_nao_cai_em_today,
    test_main_date_sem_proxima_sessao_nao_calcula_nem_publica,
    test_main_automatico_em_feriado_sem_win_nao_publica,
    test_timer_gex_roda_apos_primeira_barra_win,
    test_main_sucesso_completo_retorna_exit_code_0_e_notifica_uma_vez,
    test_main_dry_run_nao_grava_nem_notifica_mesmo_com_sucesso,
    test_main_ambos_targets_falhando_nao_salva_nem_notifica,
    test_main_win_grava_mesmo_snapshot_causal_em_gex_levels_e_history_e_e_idempotente,
    test_main_win_historico_valido_nao_e_degradado_por_rerun_invalido_sem_replace,
    test_main_win_replace_forca_sobrescrita_de_historico_valido,
    test_main_win_falha_entre_save_e_save_history_nao_deixa_linha_orfa_em_gex_levels,
]

if __name__ == "__main__":
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
