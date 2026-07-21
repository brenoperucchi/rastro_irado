"""Spec do backtester NF-01 (escopo mínimo — Pair Signal isolado).

Ref: scripts/measure_pair_signal_value.py — ver docstring do módulo para o
contexto completo (achado C1-b, achado de risco #1 do plano consolidado).

Duas frentes de teste:
  1. Lógica pura de extração/medição (extract_trade_outcomes, bootstrap) —
     sobre snapshots sintéticos, sem banco nem Kalman.
  2. Encadeamento cronológico do Kalman (chronological_replay) — com um
     Kalman FAKE injetável (evita depender do pykalman real, ausente neste
     ambiente Linux de dev), sobre um DB seedado real via engine.

Roda sem pytest:  python3 tests/test_measure_pair_signal_value.py
Ou com pytest:    pytest tests/test_measure_pair_signal_value.py
"""
import os
import sys
import json
import sqlite3
import tempfile
import types
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
    for _sub in ("statsmodels", "statsmodels.tsa", "statsmodels.tsa.vector_ar",
                 "statsmodels.tsa.vector_ar.vecm"):
        sys.modules[_sub] = types.ModuleType(_sub)
    sys.modules["statsmodels.tsa.vector_ar.vecm"].coint_johansen = lambda *a, **k: None

from backend.irai.engine import IRAISnapshot
from backend.db import SCHEMA, migrate_divergence_config
import test_premarket as tp  # reaproveita _seed/_engine/SESSION/TARGET/SLUG/FACTOR

import scripts.measure_pair_signal_value as psv
from scripts.measure_pair_signal_value import (
    COOLDOWN_BARS,
    TARGET_COST_POINTS,
    Estimate,
    TradeOutcome,
    _bootstrap_sessions,
    chronological_replay,
    estimate_mean,
    extract_trade_outcomes,
    win_rate,
)


def _mk_outcome(session_date, target, direction, hour_brt, pair_factor, entry_price,
                fwd, mfe, mae, *, obs="2026-07-10T10:05:00", conf="2026-07-10T10:05:00",
                avail="2026-07-10T10:05:00", entry_at="2026-07-10T10:10:00"):
    """Constrói TradeOutcome nos testes de bootstrap/win_rate, que não se
    importam com os 4 timestamps causais (só medem fwd) — preenche-os com
    valores plausíveis pra satisfazer o dataclass sem poluir cada caso."""
    return TradeOutcome(session_date, target, direction, hour_brt, pair_factor,
                        entry_price, fwd, mfe, mae, obs, conf, avail, entry_at)


def _snap(i, close, *, pair_compra=None, pair_venda=None, pair_factor="us500",
          ts_hour=10, bar_open=None, high=None, low=None, timestamp=None):
    s = IRAISnapshot(
        timestamp=timestamp or f"2026-07-10T{ts_hour:02d}:{(i * 5) % 60:02d}:00+00:00",
        session_date="2026-07-10", bar_idx=i, t_frac=1.0, p_up=50.0,
        score=0.0, verdict="", verdict_color="",
    )
    s.win_current = close
    s.win_bar_open = close if bar_open is None else bar_open
    s.win_high = close if high is None else high
    s.win_low = close if low is None else low
    s.pair_compra = pair_compra
    s.pair_venda = pair_venda
    s.pair_factor = pair_factor
    return s


# ── 1. extract_trade_outcomes ───────────────────────────────────────────────

def test_extrai_evento_de_compra_com_retorno_liquido_de_custo():
    """Sinal de compra na barra 0; entrada usa o open da barra seguinte.
    Preço sobe 5pts/barra; h=3 encerra no close da barra 3 (115): retorno
    bruto +10, líquido = 10 - custo (WIN$N=10) = 0."""
    snaps = [_snap(0, 100.0, pair_compra=100.0)] + [
        _snap(i, 100.0 + i * 5.0) for i in range(1, 25)
    ]
    outcomes = extract_trade_outcomes("2026-07-10", "WIN$N", snaps, is_b3=True)
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.direction == "buy"
    assert o.entry_price == 105.0
    assert o.fwd[3] == (115.0 - 105.0) - TARGET_COST_POINTS["WIN$N"]


def test_entry_usa_open_da_barra_seguinte_no_instante_em_que_sinal_fica_disponivel():
    """Ao fechar a barra do sinal às 10:05, o primeiro preço M5 observável é
    o OPEN da barra seguinte, também às 10:05 — não o close dessa barra às
    10:10. O close seguinte era o fill provisório do NF-01A e este teste deve
    impedir que ele volte depois do VAL-04."""
    snaps = [_snap(0, 100.0, pair_compra=100.0)]
    snaps += [
        _snap(1, 105.0, bar_open=107.0, high=110.0, low=103.0),
        *[_snap(i, 105.0 + i) for i in range(2, 25)],
    ]

    outcome = extract_trade_outcomes(
        "2026-07-10", "WIN$N", snaps, is_b3=True)[0]

    assert outcome.entry_price == 107.0
    assert outcome.signal_available_at == "2026-07-10T10:05:00"
    assert outcome.entry_at == outcome.signal_available_at


def test_h3_fecha_apos_tres_barras_completas_a_partir_do_open_de_entrada():
    """Entrada no open da barra 1. Três barras completas são 1, 2 e 3;
    portanto h=3 usa o close da barra 3, e não o da barra 4."""
    snaps = [
        _snap(0, 100.0, pair_compra=100.0),
        _snap(1, 106.0, bar_open=105.0),
        _snap(2, 107.0),
        _snap(3, 120.0),
        _snap(4, 999.0),
        *[_snap(i, 120.0) for i in range(5, 25)],
    ]

    outcome = extract_trade_outcomes(
        "2026-07-10", "WIN$N", snaps, is_b3=True)[0]

    assert outcome.fwd[3] == (120.0 - 105.0) - TARGET_COST_POINTS["WIN$N"]


def test_evento_de_venda_tem_sinal_invertido():
    """Venda no sinal da barra 0; entrada no open da barra 1 (95). Preço CAI
    5pts/barra -> favorável. h=3 encerra no close da barra 3 (85): retorno
    bruto +10 (não -10), líquido = 10 - custo (WDO$N=1)."""
    snaps = [_snap(0, 100.0, pair_venda=100.0)] + [
        _snap(i, 100.0 - i * 5.0) for i in range(1, 25)
    ]
    outcomes = extract_trade_outcomes("2026-07-10", "WDO$N", snaps, is_b3=True)
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.direction == "sell"
    assert o.entry_price == 95.0
    assert o.fwd[3] == (95.0 - 85.0) - TARGET_COST_POINTS["WDO$N"]


def test_sinal_na_ultima_barra_da_sessao_nao_gera_evento():
    """Sinal na ÚLTIMA barra real da sessão -> não há barra seguinte pra
    servir de preço de entrada -> evento não é registrado (não inventa
    fill hipotético)."""
    snaps = [_snap(i, 100.0) for i in range(5)]
    snaps[4] = _snap(4, 100.0, pair_compra=100.0)
    outcomes = extract_trade_outcomes("2026-07-10", "WIN$N", snaps, is_b3=True)
    assert outcomes == []


def test_evento_sem_open_executavel_e_rejeitado_sem_fallback_para_close():
    """VAL-04 não pode chamar o close seguinte de primeiro preço quando o
    OHLC está ausente. Sem open real, o evento fica fora da amostra."""
    snaps = [_snap(0, 100.0, pair_compra=100.0)] + [
        _snap(i, 100.0 + i) for i in range(1, 25)
    ]
    snaps[1].win_bar_open = None

    assert extract_trade_outcomes(
        "2026-07-10", "WIN$N", snaps, is_b3=True) == []


def test_sinal_sem_open_executavel_ainda_consume_cooldown():
    """Um sinal economicamente elegível não pode ser substituído por outro
    próximo só porque o feed não permite provar o fill do primeiro. O trade
    sem open fica fora da amostra, mas ocupa a janela da estratégia."""
    snaps = [_snap(0, 100.0, pair_compra=100.0)]
    snaps += [_snap(i, 100.0) for i in range(1, 5)]
    snaps.append(_snap(5, 105.0, pair_compra=105.0))
    snaps += [_snap(i, 105.0) for i in range(6, 30)]
    snaps[1].win_bar_open = None

    diagnostics = {}
    assert extract_trade_outcomes(
        "2026-07-10", "WIN$N", snaps, is_b3=True,
        diagnostics=diagnostics) == []
    assert diagnostics == {
        "signals_after_cooldown": 1,
        "rejected_missing_entry_open": 1,
    }


def test_quatro_timestamps_causais_do_evento():
    """Sinal na barra 0 (início 10:00, eixo Tickmill), entrada na barra 1
    (início 10:05). Contrato temporal (barra M5, timestamp = início; fecha
    +5min):
      observation_bar_end  = fim da barra 0 = 10:05
      confirmation_bar_end = == observation (marker X3 confirmado no
                             fechamento da própria barra 0)
      signal_available_at  = == confirmation
      entry_at             = open da barra 1 = 10:05
    E signal_available_at == entry_at (causal, primeiro preço M5)."""
    snaps = [_snap(0, 100.0, pair_compra=100.0)] + [
        _snap(i, 100.0 + i * 5.0) for i in range(1, 25)
    ]
    o = extract_trade_outcomes("2026-07-10", "WIN$N", snaps, is_b3=True)[0]
    assert o.observation_bar_end == "2026-07-10T10:05:00"
    assert o.confirmation_bar_end == o.observation_bar_end
    assert o.signal_available_at == o.confirmation_bar_end
    assert o.entry_at == "2026-07-10T10:05:00"
    assert o.signal_available_at <= o.entry_at  # nunca entra antes do sinal existir


def test_gap_intra_sessao_mantem_fill_posterior_ao_sinal():
    """Se a barra seguinte observada começa depois de um gap, seu open ainda
    é causal, mas não coincide com o fechamento nominal da barra do sinal."""
    snaps = [
        _snap(0, 100.0, pair_compra=100.0,
              timestamp="2026-07-10T10:00:00+00:00"),
        _snap(1, 101.0, bar_open=102.0,
              timestamp="2026-07-10T10:15:00+00:00"),
        *[
            _snap(i, 101.0 + i,
                  timestamp=f"2026-07-10T{10 + ((15 + (i - 1) * 5) // 60):02d}:"
                            f"{(15 + (i - 1) * 5) % 60:02d}:00+00:00")
            for i in range(2, 25)
        ],
    ]

    outcome = extract_trade_outcomes(
        "2026-07-10", "WIN$N", snaps, is_b3=True)[0]

    assert outcome.signal_available_at == "2026-07-10T10:05:00"
    assert outcome.entry_at == "2026-07-10T10:15:00"
    assert outcome.signal_available_at < outcome.entry_at


def test_hour_brt_usa_offset_sazonal_nao_5h_fixo():
    """A quebra por hora deve usar o offset sazonal oficial
    (brt_to_tickmill_offset_hours), não o -5h fixo anterior. Uma barra às
    15:00 no eixo Tickmill:
      - em julho (DST americano, offset 6) -> 09:00 BRT;
      - em janeiro (fora do DST, offset 5) -> 10:00 BRT.
    O -5h fixo antigo daria 10:00 nos DOIS casos — este teste falha com ele."""
    assert psv._hour_brt("2026-07-10T15:00:00", is_b3=True) == 9    # verão: -6h
    assert psv._hour_brt("2026-01-15T15:00:00", is_b3=True) == 10   # inverno: -5h
    # Ativo não-B3 (offset 0): hora Tickmill == hora reportada.
    assert psv._hour_brt("2026-07-10T15:00:00", is_b3=False) == 15


def test_outcome_to_dict_serializa_todos_os_campos():
    o = extract_trade_outcomes(
        "2026-07-10", "WIN$N",
        [_snap(0, 100.0, pair_compra=100.0)] + [_snap(i, 100.0 + i * 5.0) for i in range(1, 25)],
        is_b3=True)[0]
    d = psv.outcome_to_dict(o)
    for key in ("session_date", "target", "direction", "hour_brt", "pair_factor",
                "entry_price", "fwd", "mfe", "mae", "observation_bar_end",
                "confirmation_bar_end", "signal_available_at", "entry_at"):
        assert key in d, f"faltou {key} na serialização"
    assert set(d["fwd"].keys()) == {"3", "6", "10", "20"}  # chaves-string, round-trip JSON
    assert json.loads(json.dumps(d))["entry_at"] == o.entry_at  # sobrevive a round-trip


def test_cooldown_suprime_segunda_entrada_proxima():
    """2 transições de compra a 5 barras de distância (< COOLDOWN_BARS=20) ->
    só a 1ª conta."""
    snaps = [_snap(0, 100.0, pair_compra=100.0)]
    snaps += [_snap(i, 100.0) for i in range(1, 5)]
    snaps.append(_snap(5, 105.0, pair_compra=105.0))  # dentro do cooldown
    snaps += [_snap(i, 105.0) for i in range(6, 30)]
    outcomes = extract_trade_outcomes("2026-07-10", "WIN$N", snaps, is_b3=True)
    assert len(outcomes) == 1
    assert outcomes[0].entry_price == 100.0


def test_segunda_entrada_apos_cooldown_conta():
    """A mesma situação do teste anterior, mas com a 2ª transição EXATAMENTE
    em COOLDOWN_BARS de distância -> conta as duas."""
    snaps = [_snap(0, 100.0, pair_compra=100.0)]
    snaps += [_snap(i, 100.0) for i in range(1, COOLDOWN_BARS)]
    snaps.append(_snap(COOLDOWN_BARS, 110.0, pair_compra=110.0))
    snaps += [_snap(i, 110.0) for i in range(COOLDOWN_BARS + 1, COOLDOWN_BARS + 25)]
    outcomes = extract_trade_outcomes("2026-07-10", "WIN$N", snaps, is_b3=True)
    assert len(outcomes) == 2


def test_horizonte_truncado_na_fronteira_da_sessao():
    """Evento a 2 barras do fim da sessão -> h=3/6/10/20 ficam None (nunca
    olham pra fora da sessão, achado A5 do plano)."""
    snaps = [_snap(i, 100.0) for i in range(5)]
    snaps[3] = _snap(3, 100.0, pair_compra=100.0)
    outcomes = extract_trade_outcomes("2026-07-10", "WIN$N", snaps, is_b3=True)
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.fwd[3] is None and o.fwd[6] is None and o.fwd[10] is None and o.fwd[20] is None


def test_mfe_mae_direcionados_corretamente():
    """Sinal na barra 0; entrada na barra 1 (close=100, igual à barra 0
    aqui). Preço sobe até 120 (barra 3) depois cai até 90 (barra 5) ->
    MFE=+20 (favorável), MAE=-10 (adverso)."""
    closes = [100.0, 100.0, 110.0, 120.0, 105.0, 90.0, 95.0] + [95.0] * 20
    snaps = [_snap(i, c, pair_compra=100.0 if i == 0 else None) for i, c in enumerate(closes)]
    outcomes = extract_trade_outcomes("2026-07-10", "WIN$N", snaps, is_b3=True)
    o = outcomes[0]
    assert o.mfe == 20.0
    assert o.mae == -10.0


def test_mfe_mae_usam_extremos_ohlc_desde_a_barra_de_entrada():
    """Com fill no open da barra seguinte, os extremos dessa própria barra
    já ocorrem depois da entrada e pertencem ao caminho econômico. MFE/MAE
    devem usar HIGH/LOW, não somente os closes M5."""
    snaps = [_snap(0, 100.0, pair_compra=100.0)]
    snaps += [
        _snap(1, 101.0, bar_open=100.0, high=125.0, low=92.0),
        *[_snap(i, 101.0, high=110.0, low=95.0) for i in range(2, 25)],
    ]

    outcome = extract_trade_outcomes(
        "2026-07-10", "WIN$N", snaps, is_b3=True)[0]

    assert outcome.mfe == 25.0
    assert outcome.mae == -8.0


def test_ohlc_ausente_no_caminho_preserva_fwd_mas_anula_mfe_mae():
    """Close de saída continua permitindo medir retorno forward, mas um
    high/low ausente no caminho impede afirmar as excursões completas."""
    snaps = [_snap(0, 100.0, pair_compra=100.0)] + [
        _snap(i, 100.0 + i) for i in range(1, 25)
    ]
    snaps[7].win_high = None

    diagnostics = {}
    outcome = extract_trade_outcomes(
        "2026-07-10", "WIN$N", snaps, is_b3=True,
        diagnostics=diagnostics)[0]

    assert outcome.fwd[3] is not None
    assert outcome.fwd[6] is not None
    assert outcome.mfe is None
    assert outcome.mae is None
    assert diagnostics["events_with_incomplete_mfe_mae"] == 1
    assert diagnostics["events_emitted"] == 1


def test_mfe_mae_clampados_em_zero_quando_trajetoria_e_monotonica():
    """Compra que só perde (preço cai sempre) -> excursão favorável nunca
    existiu de verdade: MFE deve ficar em 0.0 (piso), não negativo. Achado
    do /codex-r: sem o clamp, uma trajetória monotonicamente perdedora
    produziria MFE negativo, contrariando a convenção usual da métrica."""
    closes = [100.0, 100.0, 95.0, 90.0, 85.0, 80.0] + [80.0] * 20
    snaps = [_snap(i, c, pair_compra=100.0 if i == 0 else None) for i, c in enumerate(closes)]
    outcomes = extract_trade_outcomes("2026-07-10", "WIN$N", snaps, is_b3=True)
    o = outcomes[0]
    assert o.mfe == 0.0
    assert o.mae == -20.0


def test_nunca_extrai_evento_sem_marker():
    snaps = [_snap(i, 100.0 + i) for i in range(30)]
    outcomes = extract_trade_outcomes("2026-07-10", "WIN$N", snaps, is_b3=True)
    assert outcomes == []


# ── 2. Bootstrap clusterizado por sessão ────────────────────────────────────

def test_bootstrap_detecta_media_claramente_positiva_como_significante():
    outcomes = [
        _mk_outcome(f"2026-07-{10+i:02d}", "WIN$N", "buy", 10, "us500", 100.0,
                    {3: 8.0, 6: 8.0, 10: 8.0, 20: 8.0}, None, None)
        for i in range(30)
    ]
    est = estimate_mean(outcomes, 3, iterations=500)
    assert est is not None
    assert est.value == 8.0
    assert est.significant is True
    assert est.ci_low > 0


def test_bootstrap_nao_significante_quando_ic_inclui_zero():
    """Metade das sessões com retorno bem positivo, metade bem negativo ->
    média perto de zero, IC deve conter zero (não significante)."""
    outcomes = []
    for i in range(20):
        val = 20.0 if i % 2 == 0 else -20.0
        outcomes.append(_mk_outcome(
            f"2026-07-{10+i:02d}", "WIN$N", "buy", 10, "us500", 100.0,
            {3: val, 6: val, 10: val, 20: val}, None, None))
    est = estimate_mean(outcomes, 3, iterations=500)
    assert est is not None
    assert est.ci_low < 0 < est.ci_high
    assert est.significant is False


def test_estimate_mean_permanece_media_aritmetica_base_do_shift_de_custo():
    """A reprecificação sem novo bootstrap depende de a estatística principal
    continuar sendo uma medida de localização deslocável; o contrato atual é
    explicitamente a média aritmética, não mediana, Sharpe ou outra métrica."""
    outcomes = [
        _mk_outcome(f"d{i}", "WIN$N", "buy", 10, None, 100.0,
                    {3: value}, None, None)
        for i, value in enumerate((0.0, 0.0, 9.0))
    ]

    estimate = estimate_mean(outcomes, 3, iterations=200)

    assert estimate is not None
    assert estimate.value == 3.0


def test_win_rate_conta_so_horizontes_medidos():
    outcomes = [
        _mk_outcome("d1", "WIN$N", "buy", 10, None, 100.0, {3: 5.0}, None, None),
        _mk_outcome("d1", "WIN$N", "buy", 10, None, 100.0, {3: -5.0}, None, None),
        _mk_outcome("d1", "WIN$N", "buy", 10, None, 100.0, {3: None}, None, None),  # truncado
    ]
    wins, total, pct = win_rate(outcomes, 3)
    assert wins == 1 and total == 2 and pct == 50.0


# ── 3. Encadeamento cronológico do Kalman (achado C1-b) ─────────────────────

class _FakeKalman:
    """Mesma interface mínima de SpyKalman (tests/test_premarket.py), mas
    registra toda chamada de set_state — pra provar que chronological_replay
    realmente REINJETA o estado da sessão anterior, em vez de ficar frio."""
    set_state_calls: list = []

    def __init__(self, n_dim_state, **kw):
        self.n = n_dim_state
        self.mean = [0.0] * n_dim_state
        self.cov = [[0.0] * n_dim_state for _ in range(n_dim_state)]

    def update(self, observation, observation_matrix):
        # Estado avança de forma determinística e observável (soma 1.0 na
        # média e na diagonal da covariância a cada chamada) só pra ter algo
        # não-trivial pra encadear e comparar — inclusive a covariância, não
        # só a média (achado do /codex-r: o teste original só validava a
        # média, deixando um mix-up mean/cov na chamada de set_state()
        # sem cobertura).
        self.mean = [m + 1.0 for m in self.mean]
        self.cov = [
            [c + (1.0 if r == col else 0.0) for col, c in enumerate(row)]
            for r, row in enumerate(self.cov)
        ]
        return self.mean, self.cov

    def predict(self, observation_matrix=None):
        return self.mean, self.cov

    def get_state(self):
        return list(self.mean), [row[:] for row in self.cov]

    def set_state(self, mean, cov):
        _FakeKalman.set_state_calls.append((list(mean), [list(r) for r in cov]))
        self.mean = list(mean)
        self.cov = [list(r) for r in cov]


def test_chronological_replay_encadeia_estado_entre_sessoes():
    """2 sessões seguidas do mesmo target: a 2ª deve receber set_state() com
    EXATAMENTE o que a 1ª devolveu em get_state() — prova de encadeamento
    real, não só 'não dá erro'."""
    _FakeKalman.set_state_calls = []
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    tp._seed(db)  # sessão SESSION (2026-07-10)

    # 2ª sessão: mesmas barras, 1 dia depois (mesma cesta/fatores -> mesma
    # factor_signature, condição pro state_ts<session_start passar). Só as
    # barras DATADAS 2026-07-10 (exclui a de "fechamento de ontem",
    # 2026-07-09T21:00 — se ela também fosse deslocada +1 dia, cairia dentro
    # da janela do dia 1 como 2026-07-10T21:00 e, já alinhada (+6h verão),
    # viraria a "última barra" bogus de 2026-07-11T03:00, contaminando
    # exatamente o que este teste quer medir).
    c = sqlite3.connect(db)
    rows = c.execute(
        "SELECT symbol, source, timeframe, timestamp_utc, open, high, low, close, "
        "volume, real_volume, delta FROM market_bars WHERE timestamp_utc LIKE '2026-07-10%'"
    ).fetchall()
    for row in rows:
        symbol, source, timeframe, ts, o, h, l, close, vol, rvol, delta = row
        new_ts = ts.replace("2026-07-10", "2026-07-11")
        c.execute(
            "INSERT OR IGNORE INTO market_bars (symbol, source, timeframe, timestamp_utc, "
            "open, high, low, close, volume, real_volume, delta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, source, timeframe, new_ts, o, h, l, close, vol, rvol, delta),
        )
    c.commit()
    c.close()

    with chronological_replay(db, kalman_cls=_FakeKalman) as (compute, _instance):
        snaps_dia1 = compute("2026-07-10", tp.TARGET)
        reais_dia1 = [s for s in snaps_dia1 if not s.is_ghost]
        assert reais_dia1, "fixture inválida: sem barras reais no dia 1"

        assert _FakeKalman.set_state_calls == [], (
            "1ª sessão não deveria ter estado anterior pra herdar")

        snaps_dia2 = compute("2026-07-11", tp.TARGET)
        reais_dia2 = [s for s in snaps_dia2 if not s.is_ghost]
        assert reais_dia2, "fixture inválida: sem barras reais no dia 2"

    assert len(_FakeKalman.set_state_calls) == 1, (
        "2ª sessão deveria herdar o estado da 1ª (achado C1-b) — "
        f"set_state chamado {len(_FakeKalman.set_state_calls)}x")
    # O _FakeKalman soma 1.0 por update() na média E na diagonal da
    # covariância; com N barras reais no dia 1, o estado final tem
    # mean=[N, N, ...] e cov=diag(N) — é exatamente isso que a 2ª sessão deve
    # receber via set_state (média E covariância, não só a média).
    expected_value = float(len(reais_dia1))
    got_mean, got_cov = _FakeKalman.set_state_calls[0]
    assert all(abs(v - expected_value) < 1e-9 for v in got_mean), (
        f"estado herdado não bate com o estado final da 1ª sessão: {got_mean} "
        f"vs esperado ~{expected_value}")
    n = len(got_mean)
    for r in range(n):
        for col in range(n):
            expected_cell = expected_value if r == col else 0.0
            assert abs(got_cov[r][col] - expected_cell) < 1e-9, (
                f"covariância herdada não bate com a da 1ª sessão: {got_cov} "
                f"vs esperado diag({expected_value})")


def test_chronological_replay_encadeia_ultimo_posterior_economico_com_print_pre_abertura():
    """Print B3 real antes de 09:00 não pode deslocar o índice do posterior."""
    _FakeKalman.set_state_calls = []
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    tp._seed(db)
    c = sqlite3.connect(db)
    c.execute(
        "INSERT INTO market_bars (symbol, source, timeframe, timestamp_utc, "
        "open, high, low, close, volume, real_volume, delta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (tp.TARGET, "br", "M5", "2026-07-10T08:55:00Z",
         tp.TODAY_OPEN, tp.TODAY_OPEN, tp.TODAY_OPEN, tp.TODAY_OPEN, 10, 10, 0),
    )
    rows = c.execute(
        "SELECT symbol, source, timeframe, timestamp_utc, open, high, low, close, "
        "volume, real_volume, delta FROM market_bars WHERE timestamp_utc LIKE '2026-07-10%'"
    ).fetchall()
    for row in rows:
        symbol, source, timeframe, ts, o, h, low, close, vol, rvol, delta = row
        c.execute(
            "INSERT OR IGNORE INTO market_bars (symbol, source, timeframe, timestamp_utc, "
            "open, high, low, close, volume, real_volume, delta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, source, timeframe, ts.replace("2026-07-10", "2026-07-11"),
             o, h, low, close, vol, rvol, delta),
        )
    c.commit()
    c.close()

    with chronological_replay(db, kalman_cls=_FakeKalman) as (compute, _instance):
        first = compute("2026-07-10", tp.TARGET)
        real = [snapshot for snapshot in first if not snapshot.is_ghost]
        eligible = [
            snapshot for snapshot in real
            if psv._state_snapshot_belongs_to_session(snapshot, "2026-07-10", tp.TARGET)
        ]
        assert len(real) == len(eligible) + 1
        compute("2026-07-11", tp.TARGET)

    got_mean, _ = _FakeKalman.set_state_calls[0]
    assert all(value == len(real) for value in got_mean), (
        "o estado seguinte deve usar o posterior da última barra elegível, "
        "não a contagem de barras elegíveis como índice"
    )


def test_chronological_replay_ignora_estado_de_barra_pos_pregao():
    """A última barra B3 pode cair no rótulo do dia seguinte após o alinhamento.

    Antes do fix, esse timestamp impedia ``state_ts < session_start`` e a
    sessão seguinte reiniciava o Kalman. Só trocar o timestamp também seria
    insuficiente: o posterior salvo precisa ser o da última atualização ANTES
    da barra pós-pregão, não o estado já contaminado por ela.
    """
    _FakeKalman.set_state_calls = []
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    tp._seed(db)
    c = sqlite3.connect(db)
    rows = c.execute(
        "SELECT symbol, source, timeframe, timestamp_utc, open, high, low, close, "
        "volume, real_volume, delta FROM market_bars WHERE timestamp_utc LIKE '2026-07-10%'"
    ).fetchall()
    for row in rows:
        symbol, source, timeframe, ts, o, h, l, close, vol, rvol, delta = row
        c.execute(
            "INSERT OR IGNORE INTO market_bars (symbol, source, timeframe, timestamp_utc, "
            "open, high, low, close, volume, real_volume, delta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, source, timeframe, ts.replace("2026-07-10", "2026-07-11"),
             o, h, l, close, vol, rvol, delta),
        )
    # B3 18:30, alinhada em 00:30 do rótulo seguinte: não pertence ao
    # estado que deve entrar na sessão de 2026-07-11.
    c.execute(
        "INSERT INTO market_bars (symbol, source, timeframe, timestamp_utc, "
        "open, high, low, close, volume, real_volume, delta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (tp.TARGET, "br", "M5", "2026-07-10T18:30:00Z",
         tp.TODAY_OPEN, tp.TODAY_OPEN, tp.TODAY_OPEN, tp.TODAY_OPEN, 10, 10, 0),
    )
    c.commit()
    c.close()

    with chronological_replay(db, kalman_cls=_FakeKalman) as (compute, _instance):
        first = compute("2026-07-10", tp.TARGET)
        before_next_label = [
            snapshot for snapshot in first
            if not snapshot.is_ghost and snapshot.timestamp < "2026-07-11T00:00:00"
        ]
        after_market = [
            snapshot for snapshot in first
            if not snapshot.is_ghost and snapshot.timestamp >= "2026-07-11T00:00:00"
        ]
        assert before_next_label and after_market, "fixture precisa cruzar o rótulo"
        compute("2026-07-11", tp.TARGET)

    assert len(_FakeKalman.set_state_calls) == 1
    got_mean, _ = _FakeKalman.set_state_calls[0]
    expected = float(len(before_next_label))
    assert all(value == expected for value in got_mean), (
        "o estado herdado deve parar na última barra anterior ao pós-pregão")


def test_chronological_replay_ignora_pos_pregao_no_inverno_sem_cruzar_rotulo():
    """Com offset 5, B3 18:30 vira 23:30 no MESMO rótulo Tickmill."""
    _FakeKalman.set_state_calls = []
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    tp._seed(db)
    session_date, following_date = "2026-01-12", "2026-01-13"
    c = sqlite3.connect(db)
    c.execute(
        "UPDATE market_bars SET timestamp_utc=REPLACE(timestamp_utc, ?, ?)",
        ("2026-07-10", session_date),
    )
    c.execute(
        "UPDATE market_bars SET timestamp_utc=REPLACE(timestamp_utc, ?, ?)",
        ("2026-07-09", "2026-01-11"),
    )
    rows = c.execute(
        "SELECT symbol, source, timeframe, timestamp_utc, open, high, low, close, "
        "volume, real_volume, delta FROM market_bars WHERE timestamp_utc LIKE ?",
        (f"{session_date}%",),
    ).fetchall()
    for row in rows:
        symbol, source, timeframe, ts, o, h, l, close, vol, rvol, delta = row
        c.execute(
            "INSERT OR IGNORE INTO market_bars (symbol, source, timeframe, timestamp_utc, "
            "open, high, low, close, volume, real_volume, delta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, source, timeframe, ts.replace(session_date, following_date),
             o, h, l, close, vol, rvol, delta),
        )
    c.execute(
        "INSERT INTO market_bars (symbol, source, timeframe, timestamp_utc, "
        "open, high, low, close, volume, real_volume, delta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (tp.TARGET, "br", "M5", f"{session_date}T18:30:00Z",
         tp.TODAY_OPEN, tp.TODAY_OPEN, tp.TODAY_OPEN, tp.TODAY_OPEN, 10, 10, 0),
    )
    c.commit()
    c.close()

    with chronological_replay(db, kalman_cls=_FakeKalman) as (compute, _instance):
        first = compute(session_date, tp.TARGET)
        cutoff = datetime.fromisoformat(f"{session_date}T23:00:00")
        before_close = [
            snapshot for snapshot in first
            if not snapshot.is_ghost and psv._parse_axis_ts(snapshot.timestamp) < cutoff
        ]
        after_close = [
            snapshot for snapshot in first
            if not snapshot.is_ghost and psv._parse_axis_ts(snapshot.timestamp) >= cutoff
        ]
        assert before_close and after_close, "fixture precisa conter pós-pregão de inverno"
        compute(following_date, tp.TARGET)

    assert len(_FakeKalman.set_state_calls) == 1
    got_mean, _ = _FakeKalman.set_state_calls[0]
    assert all(value == float(len(before_close)) for value in got_mean)


# ── 4. run() / burn-in ───────────────────────────────────────────────────

def test_run_exclui_eventos_das_sessoes_de_burn_in():
    """burn_in_sessions=2: as 2 primeiras sessões da ordem cronológica são
    replayadas (pra o Kalman encadeado esquentar) mas seus eventos não
    entram na medição — achado do /codex-r (2ª rodada), risco de maior
    prioridade apontado: estado inicial frio."""
    dates = ["2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10"]

    class _FakeCandidates:
        pass
    _FakeCandidates.dates = dates
    _FakeCandidates.discarded = []

    calls = []

    @contextmanager
    def fake_replay(db_path):
        def compute(date, target):
            calls.append(date)
            return [_snap(0, 100.0, pair_compra=100.0)] + [
                _snap(j, 100.0 + j * 5.0) for j in range(1, 25)
            ]
        yield compute, None

    with patch.object(psv, "candidate_sessions", lambda db, target, limit: _FakeCandidates), \
         patch.object(psv, "chronological_replay", fake_replay):
        report = psv.run("unused.db", ["WIN$N"], limit=5, iterations=50, burn_in_sessions=2)

    assert calls == dates, "todas as sessões devem ser replayadas pra aquecer o estado"
    t = report["targets"]["WIN$N"]
    assert t["sessions_burn_in"] == 2
    assert t["by_direction"]["all"]["n_events"] == 3  # 5 sessões - 2 de burn-in


# ── 5. run() / gate de amostra mínima e quebra por ano ──────────────────────

def _fake_run_com_n_sessoes(dates, *, min_events_for_gate=None):
    """3 datas -> 1 evento de compra por sessão (bar 0), sem burn-in."""
    class _FakeCandidates:
        pass
    _FakeCandidates.dates = dates
    _FakeCandidates.discarded = []

    @contextmanager
    def fake_replay(db_path):
        def compute(date, target):
            return [_snap(0, 100.0, pair_compra=100.0)] + [
                _snap(j, 100.0 + j * 5.0) for j in range(1, 25)
            ]
        yield compute, None

    kwargs = {}
    if min_events_for_gate is not None:
        kwargs["min_events_for_gate"] = min_events_for_gate
    with patch.object(psv, "candidate_sessions", lambda db, target, limit: _FakeCandidates), \
         patch.object(psv, "chronological_replay", fake_replay):
        return psv.run("unused.db", ["WIN$N"], limit=len(dates), iterations=50,
                        burn_in_sessions=0, **kwargs)


def test_run_marca_gate_inconclusivo_abaixo_do_minimo():
    """Só 3 eventos, bem abaixo do default MIN_EVENTS_FOR_GATE=100 -> alvo
    rotulado INCONCLUSIVO, não silenciosamente tratado como sem edge."""
    report = _fake_run_com_n_sessoes(["2026-07-08", "2026-07-09", "2026-07-10"])
    t = report["targets"]["WIN$N"]
    assert t["by_direction"]["all"]["n_events"] == 3
    assert t["gate_verdict"] == "INCONCLUSIVO (amostra abaixo do mínimo)"
    assert t["min_events_for_gate"] == psv.MIN_EVENTS_FOR_GATE
    assert t["data_quality"]["events_emitted"] == 3
    assert t["data_quality"]["missing_entry_open_pct_of_fill_candidates"] == 0.0


def test_run_marca_gate_suficiente_quando_atinge_o_minimo():
    """Mesmos 3 eventos, mas com min_events_for_gate=2 (abaixo da amostra) ->
    AMOSTRA_SUFICIENTE_PARA_GATE."""
    report = _fake_run_com_n_sessoes(
        ["2026-07-08", "2026-07-09", "2026-07-10"], min_events_for_gate=2)
    t = report["targets"]["WIN$N"]
    assert t["gate_verdict"] == "AMOSTRA_SUFICIENTE_PARA_GATE"


def test_run_reporta_by_year_h6_mean():
    """Sessões em anos diferentes -> by_year_h6_mean quebra por ano, cada um
    com sua contagem e média — pedido do usuário pra 'verificar
    estabilidade por período' ao expandir a janela de replay."""
    report = _fake_run_com_n_sessoes(["2025-07-08", "2025-07-09", "2026-07-10"])
    t = report["targets"]["WIN$N"]
    by_year = t["by_year_h6_mean"]
    assert set(by_year.keys()) == {"2025", "2026"}
    assert by_year["2025"]["n"] == 2
    assert by_year["2026"]["n"] == 1


def test_run_reporta_sensibilidade_a_quatro_cenarios_de_custo():
    """Na trajetória sintética, entrada=105 e h3 fecha em 115: retorno
    bruto=10. Para WIN (custo-base=10), as médias devem ser +5, 0, -5 e
    -10 nos cenários 0,5x/1x/1,5x/2x."""
    report = _fake_run_com_n_sessoes(["2026-07-10"])
    sensitivity = report["targets"]["WIN$N"]["by_direction"]["all"][
        "cost_sensitivity"
    ]

    assert set(sensitivity) == {"0.5x", "1.0x", "1.5x", "2.0x"}
    assert sensitivity["0.5x"]["horizons"]["3"]["estimate"]["value"] == 5.0
    assert sensitivity["1.0x"]["horizons"]["3"]["estimate"]["value"] == 0.0
    assert sensitivity["1.5x"]["horizons"]["3"]["estimate"]["value"] == -5.0
    assert sensitivity["2.0x"]["horizons"]["3"]["estimate"]["value"] == -10.0

    expected = {
        "0.5x": (5.0, True, 100.0, 1),
        "1.0x": (0.0, False, 0.0, 0),
        "1.5x": (-5.0, True, 0.0, 0),
        "2.0x": (-10.0, True, 0.0, 0),
    }
    for multiplier, (shifted, significant, win_rate_pct, wins) in expected.items():
        horizon = sensitivity[multiplier]["horizons"]["3"]
        estimate = horizon["estimate"]
        assert estimate["ci_low"] == shifted
        assert estimate["ci_high"] == shifted
        assert estimate["standard_error"] == 0.0
        assert estimate["n_sessions"] == 1
        assert estimate["n_events"] == 1
        assert estimate["significant"] is significant
        assert horizon["win_rate_pct"] == win_rate_pct
        assert horizon["wins"] == wins
        assert horizon["total"] == 1


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
    print("todos passaram" if not fails else f"{fails} falha(s)")
    sys.exit(1 if fails else 0)
