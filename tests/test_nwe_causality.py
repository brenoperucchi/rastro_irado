"""Regressões de causalidade do NWE (Nadaraya-Watson Envelope).

Estes testes foram escritos ANTES/CONTRA a implementação não-causal:
- o `nwe_slope` de `backend/api/main.py` percorre a série INTEIRA (`for j in
  range(n)`) para o centro de cada ponto — anexar uma barra muda o passado;
- `computeNWE` do frontend usa lookback (causal) para centro/banda, mas espia
  `t+1` para decidir cor/transição (App.jsx:444-450), o que também é lookahead.

O módulo `backend/irai/nwe.py` é a fonte causal única. As invariantes abaixo
mapeiam 1:1 a seção 6 do plano `2026-07-13-nwe-causal-backend-foundation.md`.

`test_referencia_nao_causal_viola_invariancia_de_prefixo` documenta que a
fórmula ANTIGA (main.py) falharia a invariante 1 — ou seja, os testes têm dente.
"""

import json
import math
import os
import sqlite3
import sys
import types

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stubs p/ importar a engine sem pykalman/statsmodels reais (idem test_engine_timezone).
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
    sys.modules["statsmodels.tsa.vector_ar.vecm"].coint_johansen = lambda *a, **k: None

from backend.irai.nwe import (
    compute_nwe_series,
    NWE_BW,
    NWE_MULT,
    NWE_LOOKBACK,
    ATR_PERIOD,
    DIRECTION_FLAT_EPS,
)
from backend.db import SCHEMA, migrate_divergence_config
from backend.irai.engine import IRAIEngine


NWE_FIELDS = [
    "nwe_center_price", "nwe_upper_price", "nwe_lower_price",
    "nwe_center", "nwe_upper", "nwe_lower",
    "nwe_slope_price", "nwe_direction", "nwe_available",
    "atr_14", "atr_available", "session_vwap", "vwap_available",
    "distance_to_nwe_atr", "distance_to_vwap_atr",
]


# ── Helpers ───────────────────────────────────────────────────────────────

def _real_bar(close, win_open=100.0, high=None, low=None, volume=1.0, real_volume=1.0):
    return {
        "close": float(close),
        "high": float(high) if high is not None else float(close),
        "low": float(low) if low is not None else float(close),
        "volume": volume,
        "real_volume": real_volume,
        "is_ghost": False,
        "win_open": win_open,
    }


def _ghost_bar(close, win_open=100.0):
    return {
        "close": float(close), "high": None, "low": None,
        "volume": None, "real_volume": None,
        "is_ghost": True, "win_open": win_open,
    }


def _price_walk(seed, n, step=1.0):
    """Série de preços determinística (sobe e desce) para exercitar o kernel."""
    prices = []
    p = float(seed)
    for k in range(n):
        p += step * math.sin(k / 3.0) + 0.25 * ((k % 5) - 2)
        prices.append(round(p, 4))
    return prices


# ── Referência transcrita de frontend/src/App.jsx:342-475 ──────────────────
# NÃO chama o módulo — reimplementa a fórmula normativa (centro/banda/âncora/
# slope) diretamente, para servir de oráculo de paridade. Os campos de
# renderização com lookahead (nwe_up/nwe_down/transition) são omitidos de
# propósito: são não-normativos (D2 do plano).

def ref_compute_nwe(data, history_closes):
    BW, MULT, LOOKBACK = NWE_BW, NWE_MULT, NWE_LOOKBACK
    history_prices = [float(x) for x in (history_closes or [])]
    valid = [d for d in data if not d.get("is_ghost")]
    current_prices = [float(d["close"]) for d in valid]
    all_prices = history_prices + current_prices
    n_all = len(all_prices)
    if n_all < 3:
        return [None for _ in data]
    center = [0.0] * n_all
    for t in range(n_all):
        sw = sy = 0.0
        lim = min(t, LOOKBACK - 1)
        for i in range(lim + 1):
            w = math.exp(-(i * i) / (2 * BW * BW))
            sw += w
            sy += w * all_prices[t - i]
        center[t] = sy / sw
    env = [0.0] * n_all
    for t in range(n_all):
        se = 0.0
        lim = min(t, LOOKBACK - 1)
        count = lim + 1
        for i in range(lim + 1):
            se += abs(all_prices[t - i] - center[t - i])
        env[t] = (se / count) * MULT
    hlen = len(history_prices)
    cur_center = center[hlen:]
    cur_env = env[hlen:]
    last_c = last_u = last_l = None
    last_cp = last_up = last_lp = None
    last_slope = 0.0
    valid_idx = 0
    out = []
    for d in data:
        open_ = d.get("win_open") or 1
        if valid_idx == 0 and hlen > 0 and last_c is None:
            hc = center[hlen - 1]
            he = env[hlen - 1]
            last_c = (hc / open_ - 1) * 100
            last_u = ((hc + he) / open_ - 1) * 100
            last_l = ((hc - he) / open_ - 1) * 100
            last_cp, last_up, last_lp = hc, hc + he, hc - he
            prevh = center[hlen - 2] if hlen > 1 else center[hlen - 1]
            last_slope = hc - prevh
        if d.get("is_ghost"):
            wc = float(d["close"])
            out.append({
                "nwe_center": last_c if last_c is not None else ((wc / open_ - 1) * 100),
                "nwe_upper": last_u if last_u is not None else ((wc / open_ - 1) * 100),
                "nwe_lower": last_l if last_l is not None else ((wc / open_ - 1) * 100),
                "nwe_center_price": last_cp if last_cp is not None else wc,
                "nwe_upper_price": last_up if last_up is not None else wc,
                "nwe_lower_price": last_lp if last_lp is not None else wc,
                "nwe_slope_price": last_slope,
            })
            continue
        i = valid_idx
        valid_idx += 1
        c = cur_center[i]
        e = cur_env[i]
        cp, up, lp = c, c + e, c - e
        if i > 0:
            prevc = cur_center[i - 1]
        elif hlen > 0:
            prevc = center[hlen - 1]
        else:
            prevc = cur_center[0]
        slope = c - prevc
        row = {
            "nwe_center": (c / open_ - 1) * 100,
            "nwe_upper": ((c + e) / open_ - 1) * 100,
            "nwe_lower": ((c - e) / open_ - 1) * 100,
            "nwe_center_price": cp,
            "nwe_upper_price": up,
            "nwe_lower_price": lp,
            "nwe_slope_price": slope,
        }
        last_c, last_u, last_l = row["nwe_center"], row["nwe_upper"], row["nwe_lower"]
        last_cp, last_up, last_lp = cp, up, lp
        last_slope = slope
        out.append(row)
    return out


def _assert_field_equal(a, b, msg):
    """Compara 2 leituras de um campo NWE opcional (float|None), tratando
    None explicitamente — `pytest.approx(None)` levanta TypeError."""
    if a is None or b is None:
        assert a is b, msg
    else:
        assert a == pytest.approx(b, rel=1e-12, abs=1e-12), msg


# ── Invariante 1: invariância de prefixo ───────────────────────────────────

def test_invariancia_de_prefixo():
    """NWE(x[0:n]) == primeiros n resultados de NWE(x[0:n+k])."""
    prices = _price_walk(100.0, 40)
    bars = [_real_bar(p) for p in prices]
    n = 25
    full = compute_nwe_series(bars, [])
    prefix = compute_nwe_series(bars[:n], [])
    assert len(prefix) == n
    for t in range(n):
        for f in ("nwe_center_price", "nwe_upper_price", "nwe_lower_price",
                  "nwe_slope_price", "atr_14", "session_vwap",
                  "distance_to_nwe_atr", "distance_to_vwap_atr"):
            _assert_field_equal(full[t][f], prefix[t][f],
                                f"prefixo divergiu em t={t}, campo={f}")


# ── Invariante 2: sem futuro imediato ──────────────────────────────────────

def test_alterar_t_mais_1_nao_altera_t():
    prices = _price_walk(100.0, 30)
    bars = [_real_bar(p, high=p + 2, low=p - 2, volume=10.0, real_volume=10.0)
            for p in prices]
    base = compute_nwe_series(bars, [])

    t = 15
    perturbed = [dict(b) for b in bars]
    perturbed[t + 1]["close"] += 500.0  # choque só na barra seguinte
    perturbed[t + 1]["high"] = perturbed[t + 1]["close"]
    after = compute_nwe_series(perturbed, [])

    for f in ("nwe_center_price", "nwe_upper_price", "nwe_lower_price",
              "nwe_slope_price", "nwe_center", "nwe_upper", "nwe_lower",
              "atr_14", "session_vwap", "distance_to_nwe_atr", "distance_to_vwap_atr"):
        _assert_field_equal(base[t][f], after[t][f],
                             f"barra futura vazou para t={t}, campo={f}")
    # A barra t+1 (a alterada) DEVE mudar — senão o teste não prova nada.
    assert base[t + 1]["nwe_center_price"] != pytest.approx(after[t + 1]["nwe_center_price"])
    assert base[t + 1]["atr_14"] != pytest.approx(after[t + 1]["atr_14"])


# ── Invariante 3: warm-up (histórico) muda início, nunca usa futuro ─────────

def test_warmup_muda_primeiras_barras_mas_nao_usa_futuro():
    prices = _price_walk(100.0, 30)
    bars = [_real_bar(p) for p in prices]
    history = _price_walk(90.0, NWE_LOOKBACK)  # warm-up cheio

    with_hist = compute_nwe_series(bars, history)
    without = compute_nwe_series(bars, [])

    # (a) O histórico ALTERA as primeiras barras (senão o warm-up seria inócuo).
    assert with_hist[0]["nwe_center_price"] != pytest.approx(without[0]["nwe_center_price"])

    # (b) Para qualquer t, o valor com histórico é idêntico ao valor obtido
    #     truncando a série em t — ou seja, nenhuma observação posterior a t é
    #     usada, mesmo com warm-up presente.
    for t in (0, 5, 12, 29):
        truncated = compute_nwe_series(bars[: t + 1], history)
        assert with_hist[t]["nwe_center_price"] == pytest.approx(
            truncated[t]["nwe_center_price"], rel=1e-12, abs=1e-12)


# ── Invariante 4: ghost bars ───────────────────────────────────────────────

def test_ghost_nao_entra_no_kernel_nem_move_slope():
    prices = _price_walk(100.0, 20)
    real_bars = [_real_bar(p) for p in prices]
    baseline = compute_nwe_series(real_bars, [])

    # Insere uma ghost (com preço absurdo) entre a barra 10 e 11.
    with_ghost = real_bars[:11] + [_ghost_bar(99999.0)] + real_bars[11:]
    got = compute_nwe_series(with_ghost, [])

    # Índices das barras REAIS em `got`: 0..10, depois 12.. (a 11 é ghost).
    real_positions = [i for i, b in enumerate(with_ghost) if not b["is_ghost"]]
    for k, pos in enumerate(real_positions):
        for f in ("nwe_center_price", "nwe_slope_price"):
            assert got[pos][f] == pytest.approx(baseline[k][f], rel=1e-12, abs=1e-12), \
                f"ghost contaminou barra real k={k}, campo={f}"

    # A ghost repete o último valor causal conhecido (barra real 10).
    ghost_out = got[11]
    assert ghost_out["nwe_center_price"] == pytest.approx(baseline[10]["nwe_center_price"])
    assert ghost_out["nwe_slope_price"] == pytest.approx(baseline[10]["nwe_slope_price"])
    # Ghost não dispara ATR/VWAP novos (carrega o estado, não recalcula).
    assert ghost_out["atr_14"] == got[10]["atr_14"]


# ── Invariante 5: gap intrassessão não vira retorno zero ───────────────────

def test_gap_intrassessao_nao_vira_observacao_artificial():
    prices = _price_walk(100.0, 18)
    real_bars = [_real_bar(p) for p in prices]
    baseline = compute_nwe_series(real_bars, [])

    # Gap = ghost com forward-fill do último close (NÃO zero). O kernel deve
    # ignorá-la; a barra real seguinte deve ter o MESMO centro de baseline.
    gap_close = prices[9]  # forward-fill, não 0
    with_gap = real_bars[:10] + [_ghost_bar(gap_close)] + real_bars[10:]
    got = compute_nwe_series(with_gap, [])

    # Barra real logo após o gap (posição 11 em `got`) == baseline[10].
    assert got[11]["nwe_center_price"] == pytest.approx(baseline[10]["nwe_center_price"], rel=1e-12)
    # E o gap NÃO injetou um retorno 0: se um 0.0 tivesse entrado no kernel, o
    # centro seguinte despencaria. Confirmamos que continua próximo do nível.
    assert got[11]["nwe_center_price"] == pytest.approx(prices[10], abs=abs(prices[10]) * 0.1)


# ── Invariante 6: paridade com a referência (App.jsx) ──────────────────────

@pytest.mark.parametrize("history", [
    [],
    _price_walk(88.0, 30),
    _price_walk(120.0, NWE_LOOKBACK + 40),  # histórico > lookback (satura janela)
])
def test_paridade_com_referencia_appjsx(history):
    prices = _price_walk(100.0, 35)
    win_open = prices[0]
    # Mistura barras reais e ghosts para exercitar o carry-forward.
    bars = []
    for k, p in enumerate(prices):
        if k in (3, 4, 17):
            bars.append(_ghost_bar(p, win_open=win_open))
        else:
            bars.append(_real_bar(p, win_open=win_open))

    got = compute_nwe_series(bars, history)
    ref = ref_compute_nwe(bars, history)

    for i in range(len(bars)):
        if not got[i]["nwe_available"]:
            # `ref_compute_nwe` não modela o gate de prontidão POR BARRA
            # (NWE_MIN_READY, achado B1#2): ele só verifica se o LOTE inteiro
            # tem >=3 preços, então sempre calcula um número para toda barra.
            # Aqui só as 2 primeiras barras reais de uma sessão sem histórico
            # caem nesse caso — a fórmula em si é testada nas barras prontas.
            continue
        for f in ("nwe_center_price", "nwe_upper_price", "nwe_lower_price",
                  "nwe_center", "nwe_upper", "nwe_lower", "nwe_slope_price"):
            assert got[i][f] == pytest.approx(ref[i][f], rel=1e-6, abs=1e-9), \
                f"paridade divergiu (history={len(history)}) i={i} campo={f}"


def test_nwe_direction_e_causal_do_sinal_do_slope():
    prices = _price_walk(100.0, 20)
    bars = [_real_bar(p) for p in prices]
    got = compute_nwe_series(bars, [])
    for r in got:
        if not r["nwe_available"]:
            assert r["nwe_direction"] is None
            continue
        slope = r["nwe_slope_price"]
        expected = "flat" if math.isclose(slope, 0.0, abs_tol=DIRECTION_FLAT_EPS) \
            else "up" if slope > 0 else "down"
        assert r["nwe_direction"] == expected


def test_nwe_direction_flat_quando_slope_e_exatamente_zero():
    """Preço constante -> slope≈0 -> direction 'flat', não o tie-break
    silencioso pra 'up' (achado B1#3 da tri-review de 2026-07-14). O kernel
    ainda produz ruído de ponto flutuante (~1e-14) numa série constante —
    por isso o teste tolera o ruído em vez de exigir slope_price == 0.0 exato."""
    bars = [_real_bar(100.0) for _ in range(10)]
    got = compute_nwe_series(bars, [])
    for r in got[:2]:
        assert r["nwe_direction"] is None and r["nwe_available"] is False
    for r in got[2:]:
        assert r["nwe_available"] is True
        assert abs(r["nwe_slope_price"]) < 1e-9
        assert r["nwe_direction"] == "flat"


def test_direction_nao_mascara_tick_minimo_de_forex_como_flat():
    """DIRECTION_FLAT_EPS precisa ser pequeno o bastante pra não confundir o
    menor tick real de um par forex (~1.0, 5 casas decimais, tick ~1e-5) com
    ruído de ponto flutuante — achado da revisão do slice B1#2/#3/#5
    (2026-07-14): um epsilon grande demais (1e-6) mascararia isto como 'flat'."""
    prices = [1.0] * 94 + [1.00001]  # 1 tick real após 94 barras paradas
    bars = [_real_bar(p) for p in prices]
    got = compute_nwe_series(bars, [])
    assert got[-1]["nwe_slope_price"] > 0
    assert got[-1]["nwe_direction"] == "up"


def test_disponibilidade_nao_flutua_entre_live_e_replay():
    """Núcleo do achado B1#2: a leitura 'ao vivo' (só as 2 primeiras barras no
    banco) e o 'replay' posterior (sessão completa) não podem divergir sobre
    o status das 2 primeiras barras — elas ficam indisponíveis nos dois casos,
    não só disponíveis retroativamente quando mais barras chegam."""
    prices = _price_walk(100.0, 20)
    bars = [_real_bar(p) for p in prices]

    live_so_far = compute_nwe_series(bars[:2], [])
    replay_full = compute_nwe_series(bars, [])

    for t in range(2):
        assert live_so_far[t]["nwe_available"] is False
        assert replay_full[t]["nwe_available"] is False
        assert live_so_far[t]["nwe_center_price"] is None
        assert replay_full[t]["nwe_center_price"] is None


def test_ghost_antes_da_terceira_real_fica_indisponivel():
    """Ghost intercalado ANTES de NWE_MIN_READY barras reais acumuladas fica
    indisponível — não pode "adiantar" a prontidão nem herdar um centro que
    ainda não existe (achado B1#2)."""
    bars = [
        _ghost_bar(100.0),
        _real_bar(100.0),
        _ghost_bar(100.5),
        _real_bar(101.0),
        _real_bar(101.5),  # 3ª barra real -> prontidão
        _ghost_bar(101.5),
    ]
    got = compute_nwe_series(bars, [])
    for r in got[:4]:  # ghost, real#1, ghost, real#2 — só 2 reais vistas até aqui
        assert r["nwe_available"] is False
    assert got[4]["nwe_available"] is True   # real#3 — prontidão atingida
    assert got[5]["nwe_available"] is True   # ghost após prontidão, carry-forward


# ── Invariante 9: VWAP/ATR indisponíveis → flag, nunca NaN/Infinity ────────

def test_vwap_atr_indisponiveis_produzem_flag_e_json_valido():
    # Poucas barras (< ATR_PERIOD) e volume zero → ATR e VWAP indisponíveis.
    bars = [_real_bar(100.0 + k, volume=0.0, real_volume=0.0) for k in range(5)]
    got = compute_nwe_series(bars, [])

    for r in got:
        assert r["atr_available"] is False
        assert r["atr_14"] is None
        assert r["vwap_available"] is False
        assert r["session_vwap"] is None
        assert r["distance_to_nwe_atr"] is None
        assert r["distance_to_vwap_atr"] is None

    # NUNCA NaN/Infinity no payload — json.dumps padrão explode em NaN.
    payload = json.dumps(got, allow_nan=False)
    assert "NaN" not in payload and "Infinity" not in payload


def test_atr_fica_disponivel_com_barras_suficientes():
    prices = _price_walk(100.0, ATR_PERIOD + 6)
    bars = [_real_bar(p, high=p + 2, low=p - 2, volume=10.0, real_volume=10.0)
            for p in prices]
    got = compute_nwe_series(bars, [])

    # Antes de ATR_PERIOD barras: indisponível. A partir daí: disponível e finito.
    assert got[ATR_PERIOD - 2]["atr_available"] is False
    for r in got[ATR_PERIOD - 1:]:
        assert r["atr_available"] is True
        assert r["atr_14"] is not None and math.isfinite(r["atr_14"])
        assert r["vwap_available"] is True
        assert r["session_vwap"] is not None and math.isfinite(r["session_vwap"])
        assert r["distance_to_nwe_atr"] is not None
    json.dumps(got, allow_nan=False)  # não deve levantar


def test_atr_indisponivel_quando_atr_e_exatamente_zero():
    """Sessão sem volatilidade (high==low==close, sem gaps) -> atr_14==0.0,
    mas atr_available deve ser False — 0.0 não é uma leitura utilizável pras
    distâncias normalizadas por ATR (achado B1#5 da tri-review)."""
    bars = [_real_bar(100.0, high=100.0, low=100.0, volume=10.0, real_volume=10.0)
            for _ in range(ATR_PERIOD + 4)]
    got = compute_nwe_series(bars, [])
    for r in got[ATR_PERIOD - 1:]:
        assert r["atr_14"] == 0.0
        assert r["atr_available"] is False
        assert r["distance_to_nwe_atr"] is None
        assert r["distance_to_vwap_atr"] is None
    json.dumps(got, allow_nan=False)


# ── Invariante 10: entrada não-finita falha alto, nunca propaga (B1#5) ─────

def test_close_nao_finito_levanta_valueerror():
    """`close` alimenta a série inteira do kernel — um NaN/Infinity aqui
    corromperia todas as barras seguintes silenciosamente. Falha alto."""
    with pytest.raises(ValueError):
        compute_nwe_series(
            [_real_bar(100.0), _real_bar(float("nan")), _real_bar(102.0)], [])
    with pytest.raises(ValueError):
        compute_nwe_series([_real_bar(100.0), _real_bar(float("inf"))], [])


def test_history_close_nao_finito_levanta_valueerror():
    with pytest.raises(ValueError):
        compute_nwe_series([_real_bar(100.0)], [float("nan")])


def test_high_nao_finito_e_tratado_como_ausente_nao_propaga_nan():
    """high/low são opcionais; um valor presente mas não-finito vira ausente
    (não contamina TR/VWAP) em vez de propagar NaN pras barras seguintes."""
    n = ATR_PERIOD + 5
    bars = [_real_bar(100.0 + k, high=102.0 + k, low=98.0 + k,
                       volume=10.0, real_volume=10.0)
            for k in range(n)]
    bars[ATR_PERIOD]["high"] = float("nan")  # dado malformado no meio da janela
    got = compute_nwe_series(bars, [])
    payload = json.dumps(got, allow_nan=False)  # não deve levantar
    assert "NaN" not in payload
    # Sem o guard, o NaN entraria em true_ranges e poluiria o ATR de toda a
    # janela seguinte (14 barras) — a última barra segue finita.
    assert got[-1]["atr_14"] is not None and math.isfinite(got[-1]["atr_14"])


# ── "Anti-teste": a fórmula ANTIGA (main.py) violaria a invariante 1 ───────

def test_referencia_nao_causal_viola_invariancia_de_prefixo():
    """Documenta que o `nwe_slope` de main.py NÃO é causal: anexar uma barra
    muda o centro histórico. Se algum dia alguém 'simplificar' o módulo de volta
    para esse laço `for j in range(n)`, os testes causais acima quebram."""
    def noncausal_center(vals):
        # cópia fiel de backend/api/main.py:290-297 (kernel sobre a série INTEIRA)
        h = 8.0
        n = len(vals)
        out = []
        for i_idx in range(n):
            sw = sy = 0.0
            for j in range(n):
                w = math.exp(-((i_idx - j) ** 2) / (2 * h * h))
                sw += w
                sy += w * vals[j]
            out.append(sy / sw if sw > 0 else vals[i_idx])
        return out

    vals = _price_walk(100.0, 20)
    center_short = noncausal_center(vals[:15])
    center_long = noncausal_center(vals[:16])  # +1 barra futura
    # O centro do ponto 14 MUDA ao anexar a barra 15 -> não-causal.
    assert center_short[14] != pytest.approx(center_long[14], rel=1e-9)


# ── Invariante 7/8: integração na engine (DB) + fuso B3 vs global ──────────

def _seed_nwe_session(tmp_path, *, target, target_source, session_start_h,
                      prices, session="2026-07-10", factor="FAC", factor_source=None):
    """Semeia um DB de teste com `prices` barras M5 do target a partir de 09:00,
    mais um fator alinhado. Segue o padrão de tests/test_engine_timezone.py."""
    factor_source = factor_source or target_source
    db_path = tmp_path / "irai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.close()
    migrate_divergence_config(str(db_path))

    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO asset_models
           (target, slug, display_name, factors, factor_labels,
            session_start_h, session_end_h, active, accuracy, divergence_config)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        (
            target, "fixture", "Fixture",
            json.dumps([factor]), json.dumps({factor: "factor"}),
            session_start_h, 18 if session_start_h else 24, 0.55,
            json.dumps({"use_johansen": False}),
        ),
    )
    for name, value in (
        ("fixture_alpha", 1.0), ("fixture_intercept", 0.0),
        ("fixture_w_factor", 1.0), ("fixture_sigma_factor", 0.01),
    ):
        conn.execute(
            "INSERT INTO model_params (param_name, value, effective_from) VALUES (?, ?, ?)",
            (name, value, "2020-01-01"),
        )

    def insert(symbol, source, ts, price, high=None, low=None, vol=10.0, rvol=10.0):
        conn.execute(
            """INSERT INTO market_bars
               (symbol, source, timeframe, timestamp_utc, open, high, low, close,
                volume, real_volume, delta)
               VALUES (?, ?, 'M5', ?, ?, ?, ?, ?, ?, ?, 0)""",
            (symbol, source, ts, price,
             high if high is not None else price,
             low if low is not None else price,
             price, vol, rvol),
        )

    for k, p in enumerate(prices):
        total_min = 9 * 60 + k * 5
        hh, mm = divmod(total_min, 60)
        ts = f"{session}T{hh:02d}:{mm:02d}:00Z"
        insert(target, target_source, ts, p, high=p + 3, low=p - 3)
        insert(factor, factor_source, ts, 10.0)
    conn.commit()
    conn.close()
    return IRAIEngine(db_path=str(db_path))


def _naive_center_last(prices):
    """Centro causal da ÚLTIMA barra, calculado de forma independente do módulo."""
    n = len(prices)
    t = n - 1
    sw = sy = 0.0
    lim = min(t, NWE_LOOKBACK - 1)
    for i in range(lim + 1):
        w = math.exp(-(i * i) / (2 * NWE_BW * NWE_BW))
        sw += w
        sy += w * prices[t - i]
    return sy / sw


@pytest.mark.parametrize(
    ("target", "target_source", "session_start_h"),
    [
        ("WIN$N", "br", 9),       # B3 no eixo EEST (shift +6h no verão)
        ("GLOBAL", "tickmill", 0),  # ativo global, sem deslocamento
    ],
)
def test_engine_enriquece_snapshots_com_nwe(tmp_path, target, target_source, session_start_h):
    prices = _price_walk(100000.0, 24, step=40.0)
    engine = _seed_nwe_session(
        tmp_path, target=target, target_source=target_source,
        session_start_h=session_start_h, prices=prices,
    )

    snaps = engine.compute_from_db("2026-07-10", target=target, version="v1",
                                   persist_state=False)
    assert snaps

    real = [s for s in snaps if not getattr(s, "is_ghost", False)]
    assert len(real) == len(prices)

    # Todos os campos NWE presentes e serializáveis (nunca NaN/Infinity).
    for s in snaps:
        payload = {f: getattr(s, f) for f in NWE_FIELDS}
        json.dumps(payload, allow_nan=False)

    # As 2 primeiras barras reais da sessão (sem warm-up) ficam indisponíveis
    # de forma permanente — não retroativa conforme a sessão avança (achado
    # B1#2 da tri-review). A partir da 3ª (NWE_MIN_READY), disponível e finito.
    for s in real[:2]:
        assert s.nwe_available is False
        assert s.nwe_center_price is None
    for s in real[2:]:
        assert s.nwe_available is True
        assert s.nwe_center_price is not None and math.isfinite(s.nwe_center_price)

    # Paridade da INTEGRAÇÃO: o centro da última barra real bate com um cálculo
    # causal independente sobre a sequência de closes (fuso não altera o kernel).
    expected_last = _naive_center_last(prices)
    assert real[-1].nwe_center_price == pytest.approx(expected_last, rel=1e-9)

    # ATR disponível ao fim da sessão (barras suficientes) e distância definida.
    assert real[-1].atr_available is True
    assert real[-1].distance_to_nwe_atr is not None

    # OHLC da própria barra precisa sobreviver à engine. ``win_open`` é a
    # abertura da sessão e não pode ser sobrecarregado com essa finalidade.
    assert real[0].win_bar_open == prices[0]
    assert real[0].win_high == prices[0] + 3
    assert real[0].win_low == prices[0] - 3
    assert real[0].win_current == prices[0]


def test_engine_ghost_pre_mercado_repete_ultimo_valor_causal(tmp_path):
    """Fatores globais negociam antes da abertura B3 → barras ghost de pré-mercado
    na união dos timestamps. Elas não podem gerar NWE novo (carregam o último)."""
    prices = _price_walk(100000.0, 20, step=40.0)
    db_path = tmp_path / "irai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.close()
    migrate_divergence_config(str(db_path))

    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO asset_models
           (target, slug, display_name, factors, factor_labels,
            session_start_h, session_end_h, active, accuracy, divergence_config)
           VALUES ('WIN$N', 'fixture', 'Fixture', ?, ?, 9, 18, 1, 0.55, ?)""",
        (json.dumps(["MACRO"]), json.dumps({"MACRO": "factor"}),
         json.dumps({"use_johansen": False})),
    )
    for name, value in (
        ("fixture_alpha", 1.0), ("fixture_intercept", 0.0),
        ("fixture_w_factor", 1.0), ("fixture_sigma_factor", 0.01),
    ):
        conn.execute(
            "INSERT INTO model_params (param_name, value, effective_from) VALUES (?, ?, ?)",
            (name, value, "2020-01-01"),
        )

    def insert(symbol, source, ts, price):
        conn.execute(
            """INSERT INTO market_bars
               (symbol, source, timeframe, timestamp_utc, open, high, low, close,
                volume, real_volume, delta)
               VALUES (?, ?, 'M5', ?, ?, ?, ?, ?, 10, 10, 0)""",
            (symbol, source, ts, price, price + 3, price - 3, price),
        )

    # Fator global negocia das 08:00 (pré-mercado B3) — cria ghosts antes do target.
    for k in range(6):
        total_min = 8 * 60 + k * 5
        hh, mm = divmod(total_min, 60)
        insert("MACRO", "tickmill", f"2026-07-10T{hh:02d}:{mm:02d}:00Z", 10.0)
    # Target B3 e fator a partir das 09:00.
    for k, p in enumerate(prices):
        total_min = 9 * 60 + k * 5
        hh, mm = divmod(total_min, 60)
        ts = f"2026-07-10T{hh:02d}:{mm:02d}:00Z"
        insert("WIN$N", "br", ts, p)
        insert("MACRO", "tickmill", ts, 10.0)
    conn.commit()
    conn.close()

    engine = IRAIEngine(db_path=str(db_path))
    snaps = engine.compute_from_db("2026-07-10", target="WIN$N", version="v1",
                                   persist_state=False)
    assert snaps

    pre = [s for s in snaps if getattr(s, "is_ghost", False)]
    real = [s for s in snaps if not getattr(s, "is_ghost", False)]
    assert pre and len(real) == len(prices)

    # Ghost de pré-mercado não computa slope (não há barra real ainda) e não
    # dispara ATR/VWAP; permanece sem sinal.
    for s in pre:
        assert s.nwe_slope_price == 0.0
        assert s.atr_available is False
        assert s.vwap_available is False

    # As 2 primeiras barras reais ainda não atingem NWE_MIN_READY (nenhuma
    # ghost anterior "vazou" preço pro kernel — elas só carregam via carry-
    # forward, e não há centro conhecido ainda). A 3ª barra real inaugura o
    # NWE, usando só as 3 primeiras barras reais vistas.
    assert real[0].nwe_available is False and real[0].nwe_center_price is None
    assert real[1].nwe_available is False and real[1].nwe_center_price is None
    assert real[2].nwe_available is True
    assert real[2].nwe_center_price is not None and math.isfinite(real[2].nwe_center_price)
