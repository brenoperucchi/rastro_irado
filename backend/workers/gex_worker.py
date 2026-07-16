"""IRAI — Worker diário de GEX (Gamma Exposure) das opções do índice IBOV.

Calcula os níveis de gamma walls do WIN$N a partir do bundle oficial EOD da
sessão WIN anterior observada no ledger: SPRE (OI), PE (cadastro/prêmio), IR
(IBOV), SPRD (ajuste WIN) e Selic causal BCB. A perna WIN não usa BDI parcial
nem ``session_close`` do MT5. A perna WDO preserva o fluxo BDI/MT5 existente.

Metodologia (ver docs/plans e o protótipo scripts/explorations):
  netGEX(K) = Σ_venc [ Γcall(K)·OIcall(K) − Γput(K)·OIput(K) ]   (dealer +call/−put)
  GammaFlip = cruzamento de zero do netGEX cumulativo (interp linear, mais
              próximo do spot);  GammaMax/Min = argmax/argmin (refino parabólico)
  Γ via BSM (q=0) com IV invertida do prêmio EOD; IV mediana por vencimento (v1).
  Níveis calculados em pontos de IBOV e convertidos p/ preço WIN pelo basis
  dinâmico f = WIN_settle / IBOV_spot do dia.

Rodar com o collector PARADO (MT5 = 1 conexão por terminal/processo).
Uso: py -3.12 -X utf8 backend/workers/gex_worker.py [--date YYYY-MM-DD] [--dry-run]
"""

import argparse
import json
import math
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backend.db import get_connection, DB_PATH
from backend import gex_official

R_FREE = 0.1425          # taxa livre de risco a.a. (aprox. DI; refinar depois)
BDI_TAKE = 1000          # máximo aceito pela API
IV_MIN, IV_MAX = 0.05, 1.5
MONEYNESS_IV = 0.15      # só inverte IV de strikes até ±15% do spot
GRID_STEP = 1000         # espaçamento de strikes do IBOV (validado em produção — não mexer)

# Targets cobertos pelo worker e a config específica de cada perna. IBOV usa o
# GRID_STEP hardcoded acima (produção já validada); DOL infere o próprio grid
# da grade real de strikes (Task #15, Q3). risk_free=0.0 na perna DOL: o
# "spot" ali é DOL$N, que já É um futuro — gamma sem o termo de drift r·T do
# BSM padrão equivale a Black-76 (Task #15, Q1). f_sanity_clamp só existe pra
# DOL/WDO$N (dólar cheio negocia esparso; IBOV/WIN$N tem basis real via carry,
# não é ruído — não pode ter esse clamp).
TARGETS = {
    "WIN$N": {"asset": "IBOV"},
    "WDO$N": {"asset": "DOL", "risk_free": 0.0, "vol_symbol": "WDO$N", "f_sanity_clamp": 0.005},
}

log = lambda *a: print(*a, flush=True)


# ── 1) Open interest (BDI/B3) ────────────────────────────────
def fetch_bdi_table(table: str, session_date: str, sort: str) -> tuple[list[str], list[list]]:
    """Pagina uma tabela do BDI/B3 (arquivos.b3.com.br) e devolve (colunas, linhas)."""
    base = f"https://arquivos.b3.com.br/bdi/table/{table}/{session_date}/{session_date}"

    def post(page):
        req = urllib.request.Request(
            f"{base}/{page}/{BDI_TAKE}?sort={sort}", data=b"{}", method="POST",
            headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)

    first = post(1)
    cols = [c["name"] for c in first["table"]["columns"]]
    rows = list(first["table"]["values"])
    page = 2
    while True:
        vals = post(page)["table"]["values"]
        if not vals:
            break
        rows.extend(vals)
        if len(vals) < BDI_TAKE:
            break
        page += 1
    return cols, rows


def fetch_bdi_oi(session_date: str, asset: str = "IBOV") -> list[dict]:
    """OI por série de opção de `asset` no fechamento de session_date."""
    cols, rows = fetch_bdi_table("OpenPositionsEquities", session_date, "TckrSymb")
    i = {c: k for k, c in enumerate(cols)}
    seen = {}
    for r in rows:  # dedupe defensivo por (ticker, distribuição)
        seen[(r[i["TckrSymb"]], r[i["DstrbtnId"]])] = r
    out = []
    for r in seen.values():
        if r[i["Asst"]] != asset:
            continue
        # IBOV (SgmtNm='EQUITY CALL/PUT') preenche TtlPos; DOL (SgmtNm='FINANCIAL')
        # preenche OpnIntrst e deixa TtlPos None — coluna autoritativa varia por segmento.
        oi = r[i["TtlPos"]] or r[i["OpnIntrst"]] or 0
        if oi > 0:
            out.append({"ticker": r[i["TckrSymb"]], "oi": float(oi)})
    log(f"  BDI: {len(rows)} linhas brutas -> {len(out)} séries {asset} com OI")
    return out


def fetch_bdi_instruments(session_date: str, asset: str) -> dict:
    """Strike/call-put/vencimento por ticker via cadastro oficial B3
    (InstrumentsDerivatives — classificação "Derivativos de bolsa"). Usado
    para ativos cujas séries de opção não existem no universo de símbolos do
    MT5 (ex.: DOL — só IBOV tem cobertura de opções no terminal XP)."""
    cols, rows = fetch_bdi_table("InstrumentsDerivatives", session_date, "TckrSymb")
    i = {c: k for k, c in enumerate(cols)}
    out = {}
    for r in rows:
        optn_tp = r[i["OptnTp"]]
        if r[i["Asst"]] != asset or optn_tp not in ("Call", "Put"):
            continue  # exclui futuros (OptnTp None) e qualquer valor não reconhecido
        xprtn = r[i["XprtnDt"]]
        out[r[i["TckrSymb"]]] = {
            "strike": float(r[i["ExrcPric"]]),
            "is_call": optn_tp == "Call",
            "expiry": xprtn[:10] if xprtn else None,
        }
    log(f"  BDI: cadastro de instrumentos -> {len(out)} séries de opção de {asset}")
    return out


def fetch_bdi_option_data(oi_rows: list[dict], session_date: str, asset: str) -> list[dict]:
    """Enriquece oi_rows (ticker+oi) com strike/CP/vencimento do cadastro B3.
    Sem prêmio (não existe fonte pública de prêmio EOD fora do MT5, e o MT5
    não tem essas séries) — compute_gex cai no fallback de IV nesse caso."""
    instruments = fetch_bdi_instruments(session_date, asset)
    options, miss = [], 0
    for row in oi_rows:
        meta = instruments.get(row["ticker"])
        if not meta:
            miss += 1
            continue
        options.append({
            "ticker": row["ticker"], "oi": row["oi"],
            "strike": meta["strike"], "is_call": meta["is_call"],
            "expiry": meta["expiry"], "premium": None,
        })
    log(f"  BDI: {len(options)} séries enriquecidas ({miss} sem cadastro)")
    return options


# ── 2) Metadados + prêmios (MT5) ─────────────────────────────
def load_mt5_terminal():
    import MetaTrader5 as mt5
    from backend.workers.collector_wsl import TERMINALS
    br = next(t for t in TERMINALS if t.get("is_br"))
    try:
        mt5.shutdown()
    except Exception:
        pass
    time.sleep(0.5)
    if not mt5.initialize(path=br["path"], portable=True, timeout=15000):
        raise RuntimeError(f"MT5 init falhou: {mt5.last_error()}")
    return mt5


def d1_close(mt5, symbol: str, ref: date):
    """Fechamento D1 do símbolo na data ref (None se não houver barra)."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 10)
    if rates is None:
        return None
    for bar in rates:
        if datetime.fromtimestamp(int(bar[0]), tz=timezone.utc).date() == ref:
            return float(bar[4])
    return None


def fetch_ibov_mt5_leg(mt5, oi_rows: list[dict], session_date: str, trust_session_close: bool = True) -> dict:
    """Enriquece as séries com strike/CP/venc/prêmio; pega spot IBOV e WIN.
    Não abre/fecha a sessão MT5 — reusa a conexão já aberta pelo caller
    (main), pra compartilhar o mesmo pause do collector entre as pernas
    IBOV e DOL (decisão do painel — Task #15, Q2)."""
    ref = date(*map(int, session_date.split("-")))
    spot = d1_close(mt5, "IBOV", ref)
    win = d1_close(mt5, "WIN$N", ref)
    log(f"  MT5: spot IBOV={spot} WIN settle={win}")

    # symbols_get em LOTE: uma chamada devolve o symbol_info completo (strike,
    # call/put, vencimento, session_close) de todas as séries IBOV* — sem
    # symbol_select por série (que sincroniza o símbolo no terminal e levou
    # 1h35–3h37 nas primeiras execuções).
    infos = {s_.name: s_ for s_ in (mt5.symbols_get("IBOV*") or [])}
    log(f"  MT5: symbols_get('IBOV*') -> {len(infos)} símbolos")
    options, miss = [], 0
    for row in oi_rows:
        info = infos.get(row["ticker"])
        if not info or not getattr(info, "option_strike", 0):
            miss += 1
            continue
        exp_ts = getattr(info, "expiration_time", 0)
        exp = datetime.fromtimestamp(exp_ts, tz=timezone.utc).date() if exp_ts else None
        # Prêmio EOD: session_close (fechamento da sessão anterior). Fallback
        # p/ barras D1 datadas só nos strikes ATM (os únicos que alimentam a
        # inversão de IV) e em reprocessamento histórico (--date).
        prem = float(getattr(info, "session_close", 0) or 0) or None if trust_session_close else None
        if prem is None and spot and abs(float(info.option_strike) - spot) / spot <= MONEYNESS_IV:
            mt5.symbol_select(row["ticker"], True)
            prem = d1_close(mt5, row["ticker"], ref)
            mt5.symbol_select(row["ticker"], False)
        options.append({
            "ticker": row["ticker"], "oi": row["oi"],
            "strike": float(info.option_strike),
            "is_call": getattr(info, "option_right", 0) == 0,
            "expiry": exp.isoformat() if exp else None,
            "premium": prem,
        })
    log(f"  MT5: {len(options)} séries enriquecidas ({miss} sem metadados)")
    return {"spot": spot, "win_settle": win, "options": options}


def fetch_dol_mt5_leg(mt5, session_date: str) -> dict:
    """Spot/settle da perna DOL -> WDO$N: DOL$N (dólar cheio, underlying das
    opções negociadas) faz o papel de 'spot' (== IBOV na perna original);
    WDO$N (mini dólar, o alvo exibido no dashboard) faz o papel de 'settle'
    (== WIN$N). Sem cobertura de opção no MT5 pra DOL (confirmado em produção
    — só IBOV tem série de opção nos 2 terminais); strike/CP/vencimento vêm
    do cadastro B3 via fetch_bdi_option_data, não daqui."""
    ref = date(*map(int, session_date.split("-")))
    dol = d1_close(mt5, "DOL$N", ref)
    wdo = d1_close(mt5, "WDO$N", ref)
    log(f"  MT5: spot DOL$N={dol} WDO$N settle={wdo}")
    return {"spot": dol, "future_settle": wdo}


# ── 3) Cálculo GEX ───────────────────────────────────────────
def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _bsm_price(S, K, T, r, sig, is_call):
    if T <= 0 or sig <= 0:
        return max(0.0, (S - K) if is_call else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    if is_call:
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _bsm_gamma(S, K, T, r, sig):
    if T <= 0 or sig <= 0:
        return 0.0
    st = max(sig * math.sqrt(T), 1e-6)
    d1 = (math.log(S / K) + (r + 0.5 * sig * sig) * T) / st
    return _norm_pdf(d1) / (S * st)


def _implied_vol(price, S, K, T, r, is_call):
    intrinsic = max(0.0, (S - K * math.exp(-r * T)) if is_call else (K * math.exp(-r * T) - S))
    if price is None or price <= intrinsic + 1e-9 or T <= 0:
        return None
    lo, hi = 1e-4, 5.0
    if _bsm_price(S, K, T, r, hi, is_call) < price:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if _bsm_price(S, K, T, r, mid, is_call) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _median(v):
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def infer_grid_step(options: list[dict], spot: float, default: float | None = GRID_STEP,
                     moneyness: float = MONEYNESS_IV) -> float | None:
    """Espaçamento de strikes = mediana do gap entre strikes distintos com OI
    perto do spot. Decisão do painel (Task #15, Q3): GRID_STEP não é
    cosmético — alimenta os gates `liquid`/`valid` em compute_gex — então não
    dá pra hardcodar por target. A amostra inicial de strikes de DOL (deep-OTM,
    500pts) não representa o espaçamento perto do ATM, que é bem mais fino.
    Cai no `default` se não houver strikes suficientes perto do spot."""
    strikes = sorted({o["strike"] for o in options if spot and abs(o["strike"] - spot) / spot <= moneyness})
    if len(strikes) < 3:
        return default
    gaps = [strikes[k] - strikes[k - 1] for k in range(1, len(strikes))]
    return _median(gaps)


def realized_vol(conn, symbol: str, session_date: str, window_days: int) -> float | None:
    """Vol anualizada (close-to-close diário) das últimas `window_days`
    sessões ANTERIORES a session_date. O collector só grava M5 (nunca D1) em
    market_bars, então o "close diário" é o último M5 close de cada data —
    não dá pra ler timeframe='D1' direto. None se não houver histórico
    suficiente (ex.: DB local de dev, sem o collector rodando)."""
    rows = conn.execute(
        """SELECT date(timestamp_utc) d, close FROM market_bars
           WHERE symbol=? AND timeframe='M5' AND date(timestamp_utc) < ?
           ORDER BY timestamp_utc""",
        (symbol, session_date)).fetchall()
    daily_close = {}
    for d, close in rows:
        daily_close[d] = close  # sobrescreve até sobrar o último close de cada dia
    days = sorted(daily_close)[-(window_days + 1):]
    closes = [daily_close[d] for d in days]
    if len(closes) < 5:
        return None
    rets = [math.log(closes[k] / closes[k - 1]) for k in range(1, len(closes))]
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / max(len(rets) - 1, 1)
    return math.sqrt(var) * math.sqrt(252)


def realized_iv_by_expiry(conn, symbol: str, session_date: str, expiries: list[str],
                           min_window: int = 10, max_window: int = 60) -> dict:
    """IV-proxy por vencimento = vol realizada anualizada numa janela
    horizon-matched clamp(dias_até_venc, min_window, max_window). Decisão do
    painel (Task #15, Q1): substitui o fallback fixo (0.20 — nível de índice,
    não de FX) quando não há prêmio EOD pra inverter (DOL, sem cobertura de
    opção no MT5). Clampa em [IV_MIN, IV_MAX] como a IV invertida por prêmio."""
    ref = date(*map(int, session_date.split("-")))
    out = {}
    for exp in expiries:
        days = max((date(*map(int, exp.split("-"))) - ref).days, 0)
        window = max(min_window, min(max_window, days)) if days else min_window
        iv = realized_vol(conn, symbol, session_date, window)
        if iv is not None:
            out[exp] = max(IV_MIN, min(IV_MAX, iv))
    return out


def compute_gex(spot, win_settle, options, session_date, grid_step=GRID_STEP,
                 risk_free=R_FREE, iv_fallback_by_expiry=None, iv_source="premium",
                 f_sanity_clamp=None):
    ref = date(*map(int, session_date.split("-")))
    # IV mediana por vencimento (dos prêmios que invertem perto do spot)
    iv_by_exp = defaultdict(list)
    for o in options:
        if not o["expiry"] or o["premium"] is None:
            continue
        if abs(o["strike"] - spot) / spot > MONEYNESS_IV:
            continue
        T = max((date(*map(int, o["expiry"].split("-"))) - ref).days, 0) / 365.0
        iv = _implied_vol(o["premium"], spot, o["strike"], T, risk_free, o["is_call"])
        if iv and IV_MIN < iv < IV_MAX:
            iv_by_exp[o["expiry"]].append(iv)
    iv_exp = {e: _median(v) for e, v in iv_by_exp.items() if v}
    all_iv = [x for v in iv_by_exp.values() for x in v]
    # premio invertido tem prioridade por vencimento; onde não há prêmio (ex.
    # DOL, sem cobertura MT5), usa a vol-proxy horizon-matched do caller —
    # decisão do painel (Task #15, Q1): NUNCA o 0.20 fixo pra ativos sem
    # prêmio, ele é nível de índice, não de FX. `is not None` (não truthy):
    # iv_fallback_by_expiry={} (sem histórico nenhum, ex. DB local sem
    # collector) ainda é um pedido explícito de modo "realized" — não pode
    # cair no 0.20 fixo por trás só porque o dict veio vazio (review codex).
    if iv_fallback_by_expiry is not None:
        for e, v in iv_fallback_by_expiry.items():
            iv_exp.setdefault(e, v)
        if all_iv:
            iv_fallback = _median(all_iv)
        elif iv_fallback_by_expiry:
            iv_fallback = _median(list(iv_fallback_by_expiry.values()))
        else:
            log(f"  AVISO: sem prêmio invertível e sem vol realizada pra nenhum "
                f"vencimento (iv_source={iv_source}) — não uso 0.20 fixo (nível de "
                f"índice); strikes sem IV confiável ficam fora do netGEX")
            iv_fallback = None
    else:
        iv_fallback = _median(all_iv) if all_iv else 0.20

    netgex = defaultdict(float)
    for o in options:
        if not o["expiry"]:
            continue
        T = max((date(*map(int, o["expiry"].split("-"))) - ref).days, 0) / 365.0
        if T <= 0:
            continue
        iv = iv_exp.get(o["expiry"], iv_fallback)
        if iv is None:
            continue  # sem IV confiável pra esse vencimento -- não inventa gamma
        g = _bsm_gamma(spot, o["strike"], T, risk_free, iv)
        netgex[o["strike"]] += (g * o["oi"]) if o["is_call"] else (-g * o["oi"])

    Ks = sorted(netgex)
    vals = [netgex[k] for k in Ks]
    if len(Ks) < 3:
        return None

    # flip: zero do cumulativo, interp linear, mais próximo do spot
    cum, acc = [], 0.0
    for v in vals:
        acc += v
        cum.append(acc)
    crossings = []
    for j in range(len(cum) - 1):
        if cum[j] == 0:
            crossings.append(Ks[j])
        elif cum[j] * cum[j + 1] < 0:
            k0, k1, c0, c1 = Ks[j], Ks[j + 1], cum[j], cum[j + 1]
            crossings.append(k0 + (k1 - k0) * (-c0) / (c1 - c0))
    flip = min(crossings, key=lambda k: abs(k - spot)) if crossings else None

    def refine(idx):
        if idx <= 0 or idx >= len(Ks) - 1:
            return Ks[idx]
        # fórmula do vértice vale p/ amostras equidistantes; com lacuna
        # de strike nos vizinhos, devolve o strike do pico sem refinar
        if abs((Ks[idx] - Ks[idx - 1]) - (Ks[idx + 1] - Ks[idx])) > 1e-6:
            return Ks[idx]
        y0, y1, y2 = vals[idx - 1], vals[idx], vals[idx + 1]
        den = y0 - 2 * y1 + y2
        if den == 0:
            return Ks[idx]
        delta = max(-1.0, min(1.0, 0.5 * (y0 - y2) / den))
        return Ks[idx] + delta * (Ks[idx + 1] - Ks[idx - 1]) / 2.0

    imax = max(range(len(vals)), key=lambda j: vals[j])
    imin = min(range(len(vals)), key=lambda j: vals[j])
    gmax, gmin = refine(imax), refine(imin)

    # Gates de validade. `flip` é o zero do GEX ACUMULADO, enquanto gmax/gmin
    # são as coordenadas dos extremos PONTUAIS de netGEX por strike. Portanto,
    # não há invariante que obrigue gmin < flip < gmax; essa relação é somente
    # diagnóstica e não pode esconder uma grade líquida/próxima do mercado.
    liquid = sum(1 for k in Ks if abs(k - spot) <= 5 * grid_step and netgex[k] != 0)
    diagnostic_warnings = []
    if flip is not None and not (gmax > flip > gmin):
        diagnostic_warnings.append("gamma_flip_not_between_pointwise_extrema")
    valid = (flip is not None and liquid >= 8
             and abs(flip - spot) < 15 * grid_step)

    # aviso de possível bug de escala (deep-reasoner, Task #15, Q3): se NENHUM
    # strike cai nem perto do spot, é mais provável um fator 1000 perdido no
    # parse do strike do que um pregão sem liquidez nenhuma perto do ATM.
    if Ks and not any(abs(k - spot) / spot <= 0.5 for k in Ks):
        log(f"  AVISO: nenhum strike dentro de ±50% do spot ({spot}) — possível "
            f"bug de escala no parse do strike, não necessariamente ausência de liquidez")

    f = win_settle / spot
    if f_sanity_clamp is not None and abs(f - 1.0) > f_sanity_clamp:
        log(f"  AVISO: conv_factor f={f:.6f} foge de 1.0 além do clamp de sanidade "
            f"({f_sanity_clamp:.1%}) — provável artefato de last-trade esparso; usando f=1.0")
        f = 1.0
    walls = []
    if flip is not None:
        walls = [
            {"type": "gex_max", "price": round(gmax * f), "color": "#22C55E", "style": "solid", "width": 3},
            {"type": "gex_flip", "price": round(flip * f), "color": "#EAB308", "style": "solid", "width": 2},
            {"type": "gex_min", "price": round(gmin * f), "color": "#EF4444", "style": "solid", "width": 3},
        ]
        centro = round(flip * f / (grid_step * f)) * grid_step
        for k in range(-8, 9):
            p = (centro + k * grid_step) * f
            # espessura por distância do centro (padrão do indicador NTSL de
            # referência): forte no ATM, média nas intermediárias, fraca longe
            w = 3 if abs(k) <= 1 else (2 if abs(k) <= 3 else 1)
            walls.append({"type": "wall", "price": round(p), "style": "solid", "width": w,
                          "color": "#84CC16" if p > flip * f else "#EF4444"})
        for k in range(-8, 8):
            p = (centro + (k + 0.5) * grid_step) * f
            walls.append({"type": "mid_wall", "price": round(p), "style": "dashed", "width": 1,
                          "color": "#9CA3AF" if p > flip * f else "#6B7280"})

    return {
        "gamma_max_ibov": gmax, "gamma_min_ibov": gmin, "gamma_flip_ibov": flip,
        "gamma_max": gmax * f, "gamma_min": gmin * f,
        "gamma_flip": (flip * f) if flip is not None else None,
        "spot": spot, "future_settle": win_settle, "conv_factor": f,
        "n_strikes": len(Ks), "liquid_strikes": liquid, "valid": bool(valid),
        "walls": walls,
        "meta": {"iv_by_exp": {k: round(v, 4) for k, v in iv_exp.items()},
                 "iv_fallback": round(iv_fallback, 4) if iv_fallback is not None else None,
                 "iv_source": iv_source, "grid_step": grid_step, "risk_free": risk_free,
                 "diagnostic_warnings": diagnostic_warnings},
    }


def _official_validity_reasons(result: dict, grid_step: float = GRID_STEP) -> list[str]:
    reasons = []
    flip = result.get("gamma_flip_ibov")
    if flip is None:
        reasons.append("missing_gamma_flip")
    elif abs(flip - result["spot"]) >= 15 * grid_step:
        reasons.append("gamma_flip_too_far_from_spot")
    if result.get("liquid_strikes", 0) < 8:
        reasons.append("insufficient_liquid_strikes")
    return reasons


def compute_official_win_snapshot(
    source_session_date: str,
    effective_session_date: str,
    risk_free: float,
    rate_source_date: str,
    *,
    cache_dir=gex_official.DEFAULT_CACHE_DIR,
) -> dict:
    """Calcula o snapshot WIN usado, sem divergência, por LIVE e backfill.

    A aquisição é exclusivamente o bundle oficial fechado SPRE/PE/IR/SPRD.
    Qualquer arquivo ausente/incompleto levanta exceção antes da persistência.
    """
    paths = gex_official.download_b3_bundle(source_session_date, cache_dir)
    bundle = gex_official.parse_official_bundle(paths, source_session_date)
    result = compute_gex(
        bundle["spot"],
        bundle["win"]["settle"],
        bundle["options"],
        source_session_date,
        grid_step=GRID_STEP,
        risk_free=risk_free,
        iv_source="b3_reference_premium",
    )
    if result is None:
        raise ValueError("bundle oficial sem strikes suficientes para netGEX")

    validity_reasons = _official_validity_reasons(result)
    # compute_gex é a autoridade do gate; este espelho torna o motivo auditável
    # e deve permanecer equivalente ao booleano persistido.
    if bool(result["valid"]) != (not validity_reasons):
        raise RuntimeError("gate GEX divergente dos motivos de validade")
    result["meta"].update({
        "source_session_date": source_session_date,
        "effective_session_date": effective_session_date,
        "available_from": f"{effective_session_date}T00:00:00-03:00",
        "causal_policy": "B3 EOD D usable only in next WIN session",
        "risk_free_source": "BCB SGS 1178",
        "risk_free_source_date": rate_source_date,
        "risk_free": risk_free,
        "win_contract": bundle["win"],
        "source_files": gex_official.source_file_provenance(paths),
        "source_counts": {
            key: bundle[key]
            for key in ("oi_series", "premium_series", "joined_series")
        },
        "liquid_strikes": result["liquid_strikes"],
        "validity_reasons": validity_reasons,
    })
    return result


def _next_observed_win_session(conn, source_session_date: str) -> str | None:
    row = conn.execute(
        """SELECT MIN(date(timestamp_utc)) FROM market_bars
           WHERE symbol='WIN$N' AND timeframe='M5' AND date(timestamp_utc)>?""",
        (source_session_date,),
    ).fetchone()
    return row[0] if row and row[0] else None


def _observed_win_session_pair(
    conn, effective_session_date: str,
) -> tuple[str, str]:
    """Resolve source/effective somente pelo ledger WIN observado.

    A sessão efetiva precisa ter ao menos uma M5; a fonte é exatamente a
    sessão WIN imediatamente anterior, incluindo corretamente feriados.
    """
    effective = conn.execute(
        """SELECT 1 FROM market_bars
           WHERE symbol='WIN$N' AND timeframe='M5' AND date(timestamp_utc)=?
           LIMIT 1""",
        (effective_session_date,),
    ).fetchone()
    if effective is None:
        raise ValueError(
            f"sessão WIN efetiva não observada no ledger: {effective_session_date}"
        )
    previous = conn.execute(
        """SELECT MAX(date(timestamp_utc)) FROM market_bars
           WHERE symbol='WIN$N' AND timeframe='M5' AND date(timestamp_utc)<?""",
        (effective_session_date,),
    ).fetchone()
    if previous is None or previous[0] is None:
        raise ValueError(
            f"sessão WIN fonte anterior não observada: {effective_session_date}"
        )
    return previous[0], effective_session_date


def _validate_official_source(source: str, cache_dir) -> str:
    """Valida exatamente a fonte dada pelo ledger, sem fallback temporal."""
    try:
        paths = gex_official.download_b3_bundle(source, cache_dir)
        gex_official.parse_official_bundle(paths, source)
    except Exception as exc:
        raise ValueError(f"bundle oficial esperado {source} indisponível: {exc}") from exc
    return source


def _official_rate(source_session_date: str, cache_dir) -> tuple[str, float]:
    start = (date.fromisoformat(source_session_date) - timedelta(days=10)).isoformat()
    rates = gex_official.fetch_selic_history(start, source_session_date, cache_dir)
    return gex_official.rate_at_or_before(rates, source_session_date)


# ── 4) Persistência ──────────────────────────────────────────
SCHEMA_GEX = """
CREATE TABLE IF NOT EXISTS gex_levels (
    session_date    TEXT NOT NULL,
    target          TEXT NOT NULL DEFAULT 'WIN$N',
    gamma_max       REAL, gamma_min REAL, gamma_flip REAL,
    gamma_max_ibov  REAL, gamma_min_ibov REAL, gamma_flip_ibov REAL,
    spot            REAL, future_settle REAL, conv_factor REAL,
    n_strikes       INTEGER, valid INTEGER DEFAULT 0,
    walls           TEXT,
    meta            TEXT,
    computed_at     TEXT,
    PRIMARY KEY (session_date, target)
);
"""


def save(conn, session_date, result, target="WIN$N"):
    conn.executescript(SCHEMA_GEX)
    conn.execute(
        """INSERT OR REPLACE INTO gex_levels
           (session_date, target, gamma_max, gamma_min, gamma_flip,
            gamma_max_ibov, gamma_min_ibov, gamma_flip_ibov,
            spot, future_settle, conv_factor, n_strikes, valid, walls, meta, computed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (session_date, target,
         result["gamma_max"], result["gamma_min"], result["gamma_flip"],
         result["gamma_max_ibov"], result["gamma_min_ibov"], result["gamma_flip_ibov"],
         result["spot"], result["future_settle"], result["conv_factor"],
         result["n_strikes"], 1 if result["valid"] else 0,
         json.dumps(result["walls"]), json.dumps(result["meta"]),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()


def last_session_with_oi(max_back=5):
    """Acha o último pregão com OI publicado no BDI (hoje-1 recuando)."""
    d = date.today() - timedelta(days=1)
    for _ in range(max_back):
        if d.weekday() < 5:
            try:
                rows = fetch_bdi_oi(d.isoformat())
                if len(rows) > 50:
                    return d.isoformat(), rows
            except Exception as e:
                log(f"  BDI {d}: {e}")
        d -= timedelta(days=1)
    return None, None


def main():
    ap = argparse.ArgumentParser(description="IRAI GEX worker (EOD)")
    ap.add_argument("--date", help="pregão de referência YYYY-MM-DD (default: último com OI)")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--dry-run", action="store_true", help="não grava no banco")
    ap.add_argument("--cache-dir", default=str(gex_official.DEFAULT_CACHE_DIR),
                    help="cache dos bundles oficiais B3/BCB")
    ap.add_argument("--target", choices=sorted(TARGETS), action="append",
                     help="restringe a 1+ targets (default: todos os configurados em TARGETS)")
    args = ap.parse_args()
    targets = args.target or list(TARGETS)

    log("=" * 50)
    log(f"IRAI GEX worker — gamma walls ({', '.join(targets)})")
    log("=" * 50)

    fmt = lambda v: f"{v:,.0f}" if v is not None else "N/A"
    conn = get_connection(args.db)
    mt5 = None
    exit_code, saved_any = 0, False
    try:
        for target in targets:
            cfg = TARGETS[target]
            asset = cfg["asset"]
            log(f"-- {target} ({asset}) --")
            try:
                if target == "WIN$N":
                    if args.date:
                        session_date = args.date
                        effective_session_date = _next_observed_win_session(
                            conn, session_date,
                        )
                        if effective_session_date is None:
                            raise ValueError(
                                f"sem próxima sessão WIN observada após {session_date}"
                            )
                        observed_source, _ = _observed_win_session_pair(
                            conn, effective_session_date,
                        )
                        if observed_source != session_date:
                            raise ValueError(
                                f"fonte {session_date} não é a sessão WIN anterior "
                                f"a {effective_session_date} ({observed_source})"
                            )
                    else:
                        session_date, effective_session_date = _observed_win_session_pair(
                            conn, date.today().isoformat(),
                        )
                    _validate_official_source(session_date, args.cache_dir)
                    rate_source_date, risk_free = _official_rate(
                        session_date, args.cache_dir,
                    )
                    result = compute_official_win_snapshot(
                        session_date,
                        effective_session_date,
                        risk_free,
                        rate_source_date,
                        cache_dir=args.cache_dir,
                    )
                    grid_step = GRID_STEP
                else:
                    if args.date:
                        session_date = args.date
                    else:
                        session_date, _rows = last_session_with_oi()
                        if not session_date:
                            raise ValueError("nenhum pregão recente com OI no BDI")
                    if mt5 is None:
                        mt5 = load_mt5_terminal()
                    oi_rows = fetch_bdi_oi(session_date, asset=asset)
                    options = fetch_bdi_option_data(oi_rows, session_date, asset=asset)
                    leg = fetch_dol_mt5_leg(mt5, session_date)
                    spot, future_settle = leg["spot"], leg["future_settle"]
                    # default=None (não GRID_STEP=1000, a escala do IBOV): dado
                    # esparso demais pra inferir o grid é sinal de "não confio
                    # nesse GEX", não motivo pra abrir os gates liquid/valid
                    # com um número da escala errada (review codex).
                    grid_step = infer_grid_step(options, spot, default=None) if spot else None
                    risk_free = cfg.get("risk_free", R_FREE)
                    expiries = sorted({o["expiry"] for o in options if o["expiry"]})
                    iv_fallback_by_expiry = realized_iv_by_expiry(conn, cfg["vol_symbol"], session_date, expiries)
                    iv_source, f_clamp = "realized", cfg.get("f_sanity_clamp")

                    if not spot or not future_settle:
                        raise ValueError("sem spot ou settle no MT5 p/ a data")
                    if not grid_step:
                        raise ValueError(
                            "strikes insuficientes perto do spot p/ inferir grid_step"
                        )
                    result = compute_gex(
                        spot, future_settle, options, session_date,
                        grid_step=grid_step, risk_free=risk_free,
                        iv_fallback_by_expiry=iv_fallback_by_expiry,
                        iv_source=iv_source, f_sanity_clamp=f_clamp,
                    )
                if not result:
                    log(f"FALHA [{target}]: netGEX insuficiente")
                    exit_code = 1
                    continue

                log(f"  GammaMax  = {fmt(result['gamma_max_ibov'])} -> {fmt(result['gamma_max'])} {target}")
                log(f"  GammaFlip = {fmt(result['gamma_flip_ibov'])} -> {fmt(result['gamma_flip'])} {target}")
                log(f"  GammaMin  = {fmt(result['gamma_min_ibov'])} -> {fmt(result['gamma_min'])} {target}")
                log(f"  válido={result['valid']} strikes={result['n_strikes']} "
                    f"grid_step={grid_step:.1f} f={result['conv_factor']:.6f}")

                if args.dry_run:
                    log(f"  [dry-run] {target}: nada gravado")
                    continue
                save(conn, session_date, result, target=target)
                saved_any = True
                log(f"  gravado em gex_levels ({session_date}, {target})")
            except Exception as e:
                log(f"FALHA [{target}]: {e}")
                exit_code = 1
    finally:
        if mt5 is not None:
            mt5.shutdown()
        conn.close()

    if saved_any:
        # acorda a API (cache) — mesmo padrão do collector
        try:
            urllib.request.urlopen(
                urllib.request.Request("http://127.0.0.1:8888/api/internal/notify_update",
                                       method="POST"), timeout=2)
        except Exception:
            pass
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
