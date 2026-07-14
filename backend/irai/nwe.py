"""Nadaraya-Watson Envelope (NWE) — cálculo puro, causal e determinístico.

Fonte autoritativa do NWE no backend. Este módulo NÃO depende de FastAPI, de
banco, nem de estado global: recebe uma lista ordenada de barras da sessão mais
um warm-up de closes anteriores e devolve, por barra, os campos do envelope,
ATR, VWAP e distâncias.

Referência normativa do centro/envelope/âncora: `computeNWE` em
`frontend/src/App.jsx` (linhas ~342-475). As constantes abaixo são as mesmas
declaradas lá (`NWE_BW`, `NWE_MULT`, `NWE_LOOKBACK`).

Regras causais (ver docs/plans/2026-07-13-nwe-causal-backend-foundation.md §3):

- kernel gaussiano UNILATERAL: cada barra `t` usa apenas observações `j <= t`
  dentro da janela de lookback. Nunca inspeciona `t+1`.
- MAE causal usa o centro CONTEMPORÂNEO de cada barra (`|price[t-i] - center[t-i]|`),
  não `center[t]` — exatamente como App.jsx:375.
- largura do envelope = MAE móvel × NWE_MULT.
- inclinação (`nwe_slope_price`) = center[t] - center[t-1] em espaço de PREÇO.
- `nwe_direction` = sinal causal da inclinação: "up" (>0), "down" (<0) ou
  "flat" (==0, empate exato — nunca um tie-break silencioso pra "up"); `None`
  quando `nwe_available=False`. Os campos de renderização não-normativos do
  frontend (isTransition/wasTransition/nwe_up/nwe_down) NÃO são reproduzidos
  aqui: eles espiam `t+1` (App.jsx:444-450) e violam a causalidade. Pertencem
  à camada de UI, reescritos sem lookahead.
- barras ghost (`is_ghost=True`) NÃO entram no kernel, não movem a inclinação e
  não disparam eventos; quando precisam aparecer na série, repetem o último
  valor causal conhecido.
- warm-up (`history_closes`): closes anteriores à sessão que dão contexto ao
  kernel. Podem alterar as primeiras barras da sessão, mas nunca introduzem
  informação POSTERIOR ao timestamp calculado.
- prontidão (`nwe_available`) exige `NWE_MIN_READY` preços reais VISTOS ATÉ
  aquela barra (contagem cumulativa, não sobre o lote inteiro): as 2 primeiras
  barras reais de toda sessão sem warm-up ficam indisponíveis permanentemente,
  ao vivo e no replay — nunca mudam de status retroativamente conforme mais
  barras chegam.

Indisponibilidade (volume/ATR inválidos/NWE ainda não pronto) vira flag
explícita + valor `None`; NUNCA `NaN`/`Infinity` (o payload precisa sobreviver
a `json.dumps`). Entrada não-finita (`close`/`high`/`low`) falha alto
(`ValueError`) em vez de propagar silenciosamente.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence


# ── Constantes (espelham frontend/src/App.jsx:338-340) ────────────────────
NWE_BW = 8          # bandwidth do kernel gaussiano
NWE_MULT = 3        # multiplicador da largura do envelope (MAE × MULT)
NWE_LOOKBACK = 95   # janela retroativa (barras válidas)

# ATR: média simples do True Range sobre as últimas ATR_PERIOD barras reais.
# Escolha deliberada de uma média móvel simples (não Wilder) por ser
# estritamente causal e trivial de verificar num teste determinístico; serve
# como escala para o Tactical Layer. Documentado no plano como "atr_14 causal".
ATR_PERIOD = 14


def _gaussian_weight(i: int) -> float:
    """Peso do kernel gaussiano para deslocamento `i` barras no passado."""
    return math.exp(-(i * i) / (2.0 * NWE_BW * NWE_BW))


def _causal_center(all_prices: Sequence[float]) -> list[float]:
    """Centro (kernel regression) causal sobre a série completa de preços.

    Réplica de App.jsx:353-365: para cada `t`, soma ponderada das observações
    `all_prices[t-i]` com `i in [0, min(t, NWE_LOOKBACK-1)]`.
    """
    n = len(all_prices)
    center = [0.0] * n
    for t in range(n):
        sum_w = 0.0
        sum_y = 0.0
        lookback_limit = min(t, NWE_LOOKBACK - 1)
        for i in range(lookback_limit + 1):
            w = _gaussian_weight(i)
            sum_w += w
            sum_y += w * all_prices[t - i]
        # sum_w >= 1.0 sempre (termo i=0 tem peso exp(0)=1): nunca divide por zero.
        center[t] = sum_y / sum_w
    return center


def _causal_env_width(all_prices: Sequence[float], center: Sequence[float]) -> list[float]:
    """Largura do envelope = MAE causal móvel × NWE_MULT.

    Réplica de App.jsx:367-380: MAE usa o centro CONTEMPORÂNEO de cada barra
    da janela (`|price[t-i] - center[t-i]|`), não o centro atual `center[t]`.
    """
    n = len(all_prices)
    env = [0.0] * n
    for t in range(n):
        sum_err = 0.0
        lookback_limit = min(t, NWE_LOOKBACK - 1)
        count = lookback_limit + 1
        for i in range(lookback_limit + 1):
            sum_err += abs(all_prices[t - i] - center[t - i])
        env[t] = (sum_err / count) * NWE_MULT
    return env


def _as_pct(price: float, open_price: float) -> float:
    """Retorno % desde `open` (âncora win_open), como App.jsx:431-437."""
    o = open_price or 1.0  # espelha `d.win_open || 1` — evita divisão por zero
    return (price / o - 1.0) * 100.0


def _bar_volume(bar) -> Optional[float]:
    """real_volume se válido; senão volume; senão None (barra sem fluxo)."""
    rv = bar.get("real_volume")
    if rv is not None and rv > 0:
        return float(rv)
    v = bar.get("volume")
    if v is not None and v > 0:
        return float(v)
    return None


def _empty_fields() -> dict:
    """Snapshot NWE totalmente indisponível (sem valores numéricos nem direção)."""
    return {
        "nwe_center_price": None,
        "nwe_upper_price": None,
        "nwe_lower_price": None,
        "nwe_center": None,
        "nwe_upper": None,
        "nwe_lower": None,
        "nwe_slope_price": 0.0,
        "nwe_direction": None,
        "nwe_available": False,
        "atr_14": None,
        "atr_available": False,
        "session_vwap": None,
        "vwap_available": False,
        "distance_to_nwe_atr": None,
        "distance_to_vwap_atr": None,
    }


# Barras reais mínimas (App.jsx:351) antes de o NWE ser considerado "pronto".
# Avaliado POR BARRA (contagem cumulativa), não sobre o lote inteiro: senão as
# 2 primeiras barras de cada sessão mudam de indisponível(live, poucas barras
# no banco) pra disponível(replay, sessão completa) conforme mais barras
# chegam — achado B1#2 da tri-review de 2026-07-14.
NWE_MIN_READY = 3

# Tolerância p/ considerar o slope "flat": absorve APENAS o ruído de ponto
# flutuante do kernel (~1e-14 numa série de preço constante), nunca um
# movimento de preço real. Deliberadamente pequena: um valor maior (ex. 1e-6)
# mascararia como "flat" o menor tick real de um par forex ~1.0 (5 casas
# decimais, tick ~1e-5, produz slope suavizado da ordem de ~1e-6/1e-7 — achado
# da revisão do slice B1#2/#3/#5, 2026-07-14). 1e-9 fica ~5 ordens de grandeza
# acima do ruído observado e ~2-3 ordens abaixo do menor movimento real.
DIRECTION_FLAT_EPS = 1e-9


def _direction(slope_price: float) -> str:
    """Direção causal da inclinação — três estados, sem tie-break silencioso.

    slope_price≈0 (dentro de DIRECTION_FLAT_EPS) usa "flat", não "up": um
    centro que não se moveu não é um sinal de alta — achado B1#3 da
    tri-review de 2026-07-14. A tolerância (não igualdade exata) é necessária
    porque uma série de preço literalmente constante ainda produz ruído de
    ponto flutuante da ordem de 1e-14 na soma ponderada do kernel — um preço
    genuinamente parado não pode virar "up"/"down" por acaso do arredondamento.
    """
    if math.isclose(slope_price, 0.0, abs_tol=DIRECTION_FLAT_EPS):
        return "flat"
    return "up" if slope_price > 0.0 else "down"


def compute_nwe_series(bars: Sequence[dict], history_closes: Optional[Sequence[float]] = None) -> list[dict]:
    """Calcula os campos do NWE/ATR/VWAP por barra, de forma causal.

    Args:
        bars: lista ORDENADA (por timestamp já alinhado ao eixo do servidor) de
            dicts com, no mínimo, as chaves:
              - ``close`` (float): preço de fechamento (== win_current).
              - ``is_ghost`` (bool): barra sintética/forward-fill/pré-mercado.
              - ``win_open`` (float): âncora de normalização em %.
              - ``high``/``low`` (float|None): usados no True Range (ATR). Podem
                ser None em barras ghost.
              - ``real_volume``/``volume`` (float|None): usados no VWAP.
        history_closes: closes anteriores à sessão (warm-up do kernel). Apenas
            preço; entram no kernel como contexto, jamais como observação
            posterior ao timestamp calculado.

    Returns:
        Lista de dicts (mesma ordem/tamanho de ``bars``). Chaves = campos NWE.
        Valores indisponíveis são ``None`` + flag booleana; nunca NaN/Infinity.
    """
    n_bars = len(bars)
    if n_bars == 0:
        return []

    history_prices = [float(x) for x in (history_closes or [])]
    for p in history_prices:
        if not math.isfinite(p):
            raise ValueError(
                f"nwe: close não-finito no warm-up ({p!r}) — dado malformado do DB")

    # Apenas barras REAIS (não-ghost) alimentam o kernel — App.jsx:347 filtra
    # `!d.is_ghost`. A ordem preserva a sequência da sessão. `close` é
    # obrigatório e alimenta a série inteira do kernel: um valor não-finito
    # corromperia todas as barras subsequentes silenciosamente, então falha
    # alto em vez de propagar NaN/Infinity pro output (achado B1#5).
    current_prices = []
    for b in bars:
        if b.get("is_ghost"):
            continue
        close_val = float(b["close"])
        if not math.isfinite(close_val):
            raise ValueError(
                f"nwe: close não-finito na entrada ({close_val!r}) — dado malformado do DB")
        current_prices.append(close_val)

    all_prices = history_prices + current_prices
    n_all = len(all_prices)
    hlen = len(history_prices)

    has_data = n_all >= 1
    if has_data:
        center = _causal_center(all_prices)
        env_width = _causal_env_width(all_prices, center)
        current_center = center[hlen:]
        current_env = env_width[hlen:]
    else:
        center = env_width = current_center = current_env = []

    # Estado carry-forward (para barras ghost) — espelha App.jsx:387-408.
    last_center = last_upper = last_lower = None
    last_center_price = last_upper_price = last_lower_price = None
    last_slope = 0.0

    # Estado causal de ATR/VWAP acumulado sobre barras REAIS.
    true_ranges: list[float] = []   # TR por barra real, em ordem
    prev_real_close: Optional[float] = None
    cum_pv = 0.0                    # Σ (typical × volume)
    cum_vol = 0.0                   # Σ volume
    last_atr: Optional[float] = None
    last_vwap: Optional[float] = None
    last_dist_nwe: Optional[float] = None
    last_dist_vwap: Optional[float] = None

    results: list[dict] = []
    valid_idx = 0
    seen_count = hlen  # nº de preços já acumulados no eixo causal até este ponto

    for b in bars:
        open_price = b.get("win_open") or 1.0
        close = float(b["close"])
        ready_before = seen_count >= NWE_MIN_READY  # prontidão ANTES desta barra (usada por ghosts)

        # Inicialização do carry-forward a partir do histórico, na 1ª barra
        # processada quando há warm-up (App.jsx:400-409). Independe do gate de
        # prontidão: histórico > 0 já dá um centro/slope reais para carregar.
        if valid_idx == 0 and hlen > 0 and last_center is None:
            hc = center[hlen - 1]
            he = env_width[hlen - 1]
            last_center = _as_pct(hc, open_price)
            last_upper = _as_pct(hc + he, open_price)
            last_lower = _as_pct(hc - he, open_price)
            last_center_price = hc
            last_upper_price = hc + he
            last_lower_price = hc - he
            prev_hist_center = center[hlen - 2] if hlen > 1 else center[hlen - 1]
            last_slope = hc - prev_hist_center

        if b.get("is_ghost"):
            # Ghost: repete o último valor causal conhecido; não move o kernel,
            # não atualiza ATR/VWAP. Se nada foi visto ainda, cai no preço atual.
            fields = _empty_fields()
            if ready_before and last_center_price is not None:
                fields.update({
                    "nwe_center_price": last_center_price,
                    "nwe_upper_price": last_upper_price,
                    "nwe_lower_price": last_lower_price,
                    "nwe_center": last_center,
                    "nwe_upper": last_upper,
                    "nwe_lower": last_lower,
                    "nwe_slope_price": last_slope,
                    "nwe_direction": _direction(last_slope),
                    "nwe_available": True,
                })
            elif ready_before:
                # Ghost antes de qualquer centro conhecido: âncora no preço atual.
                pct = _as_pct(close, open_price)
                fields.update({
                    "nwe_center_price": close,
                    "nwe_upper_price": close,
                    "nwe_lower_price": close,
                    "nwe_center": pct,
                    "nwe_upper": pct,
                    "nwe_lower": pct,
                    "nwe_slope_price": 0.0,
                    "nwe_direction": "flat",
                    "nwe_available": True,
                })
            # ATR/VWAP: carrega o último estado causal (não recalcula, não move).
            fields.update({
                "atr_14": last_atr,
                "atr_available": last_atr is not None and last_atr > 0,
                "session_vwap": last_vwap,
                "vwap_available": last_vwap is not None,
                "distance_to_nwe_atr": last_dist_nwe,
                "distance_to_vwap_atr": last_dist_vwap,
            })
            results.append(fields)
            continue

        # ── Barra REAL ────────────────────────────────────────────────────
        seen_count += 1
        ready_after = seen_count >= NWE_MIN_READY  # prontidão INCLUINDO esta barra
        fields = _empty_fields()

        if ready_after and has_data:
            i = valid_idx
            c = current_center[i]
            e = current_env[i]
            center_price = c
            upper_price = c + e
            lower_price = c - e

            # Inclinação causal em espaço de preço (App.jsx:439-440).
            if i > 0:
                prev_center = current_center[i - 1]
            elif hlen > 0:
                prev_center = center[hlen - 1]
            else:
                prev_center = current_center[0]
            slope_price = c - prev_center

            fields.update({
                "nwe_center_price": center_price,
                "nwe_upper_price": upper_price,
                "nwe_lower_price": lower_price,
                "nwe_center": _as_pct(center_price, open_price),
                "nwe_upper": _as_pct(upper_price, open_price),
                "nwe_lower": _as_pct(lower_price, open_price),
                "nwe_slope_price": slope_price,
                "nwe_direction": _direction(slope_price),
                "nwe_available": True,
            })

            last_center = fields["nwe_center"]
            last_upper = fields["nwe_upper"]
            last_lower = fields["nwe_lower"]
            last_center_price = center_price
            last_upper_price = upper_price
            last_lower_price = lower_price
            last_slope = slope_price

        valid_idx += 1

        # ── ATR causal (True Range sobre barras reais) ────────────────────
        high = b.get("high")
        low = b.get("low")
        if high is not None and low is not None:
            high = float(high)
            low = float(low)
            if not (math.isfinite(high) and math.isfinite(low)):
                # high/low são opcionais (podem faltar em barras ghost já
                # filtradas acima); um valor presente mas não-finito é dado
                # malformado — trata como ausente em vez de propagar (B1#5).
                high = low = None
        if high is not None and low is not None:
            if prev_real_close is None:
                tr = high - low
            else:
                tr = max(
                    high - low,
                    abs(high - prev_real_close),
                    abs(low - prev_real_close),
                )
            true_ranges.append(tr)
        prev_real_close = close

        atr_val: Optional[float] = None
        if len(true_ranges) >= ATR_PERIOD:
            atr_val = sum(true_ranges[-ATR_PERIOD:]) / ATR_PERIOD
        fields["atr_14"] = atr_val
        # >0, não só "is not None": sessão sem volatilidade (atr_val==0.0) não
        # é uma leitura utilizável — as distâncias abaixo já exigem >0, então
        # a flag ficava inconsistente com o resto do contrato (B1#5).
        fields["atr_available"] = atr_val is not None and atr_val > 0
        last_atr = atr_val

        # ── VWAP de sessão (typical × volume acumulado) ───────────────────
        vol = _bar_volume(b)
        if vol is not None and high is not None and low is not None:
            typical = (high + low + close) / 3.0
            cum_pv += typical * vol
            cum_vol += vol
        vwap_val: Optional[float] = (cum_pv / cum_vol) if cum_vol > 0 else None
        fields["session_vwap"] = vwap_val
        fields["vwap_available"] = vwap_val is not None
        last_vwap = vwap_val

        # ── Distâncias normalizadas por ATR ───────────────────────────────
        if atr_val is not None and atr_val > 0 and fields["nwe_center_price"] is not None:
            fields["distance_to_nwe_atr"] = (close - fields["nwe_center_price"]) / atr_val
        if atr_val is not None and atr_val > 0 and vwap_val is not None:
            fields["distance_to_vwap_atr"] = (close - vwap_val) / atr_val
        last_dist_nwe = fields["distance_to_nwe_atr"]
        last_dist_vwap = fields["distance_to_vwap_atr"]

        results.append(fields)

    return results
