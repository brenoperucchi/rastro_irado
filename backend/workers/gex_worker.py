"""IRAI — Worker diário de GEX (Gamma Exposure) das opções do índice IBOV.

Calcula os níveis de gamma walls do WIN$N a partir de dados EOD:
  1. Open interest por série: API pública do BDI/B3 (arquivos.b3.com.br)
     POST /bdi/table/OpenPositionsEquities/{d}/{d}/{pág}/1000?sort=TckrSymb
     (o sort é OBRIGATÓRIO: sem ele a paginação não é estável e gera
     duplicatas/faltantes — validado contra a consulta em tela da B3).
  2. Strike/call-put/vencimento/prêmio (D1) e spot IBOV + settle WIN: MT5 XP
     (as 2396 séries IBOV* existem no terminal; session_interest vem 0,
     por isso o OI vem do BDI — join por ticker).

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

R_FREE = 0.1425          # taxa livre de risco a.a. (aprox. DI; refinar depois)
BDI_TAKE = 1000          # máximo aceito pela API
IV_MIN, IV_MAX = 0.05, 1.5
MONEYNESS_IV = 0.15      # só inverte IV de strikes até ±15% do spot
GRID_STEP = 1000         # espaçamento de strikes do IBOV

log = lambda *a: print(*a, flush=True)


# ── 1) Open interest (BDI/B3) ────────────────────────────────
def fetch_bdi_oi(session_date: str) -> list[dict]:
    """OI por série de opção do IBOV no fechamento de session_date."""
    base = f"https://arquivos.b3.com.br/bdi/table/OpenPositionsEquities/{session_date}/{session_date}"

    def post(page):
        req = urllib.request.Request(
            f"{base}/{page}/{BDI_TAKE}?sort=TckrSymb", data=b"{}", method="POST",
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

    i = {c: k for k, c in enumerate(cols)}
    seen = {}
    for r in rows:  # dedupe defensivo por (ticker, distribuição)
        seen[(r[i["TckrSymb"]], r[i["DstrbtnId"]])] = r
    out = []
    for r in seen.values():
        if r[i["Asst"]] != "IBOV":
            continue
        oi = r[i["TtlPos"]] or 0
        if oi > 0:
            out.append({"ticker": r[i["TckrSymb"]], "oi": float(oi)})
    log(f"  BDI: {len(rows)} linhas brutas -> {len(out)} séries IBOV com OI")
    return out


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


def fetch_mt5_data(oi_rows: list[dict], session_date: str, trust_session_close: bool = True) -> dict:
    """Enriquece as séries com strike/CP/venc/prêmio; pega spot IBOV e WIN."""
    mt5 = load_mt5_terminal()
    ref = date(*map(int, session_date.split("-")))
    spot = d1_close(mt5, "IBOV", ref)
    win = d1_close(mt5, "WIN$N", ref)
    log(f"  MT5: spot IBOV={spot} WIN settle={win}")

    options, miss = [], 0
    for row in oi_rows:
        tk = row["ticker"]
        if not mt5.symbol_select(tk, True):
            miss += 1
            continue
        info = mt5.symbol_info(tk)
        if not info or not getattr(info, "option_strike", 0):
            miss += 1
            mt5.symbol_select(tk, False)
            continue
        exp_ts = getattr(info, "expiration_time", 0)
        exp = datetime.fromtimestamp(exp_ts, tz=timezone.utc).date() if exp_ts else None
        # Prêmio EOD: session_close do symbol_info (fechamento da sessão
        # anterior, instantâneo). copy_rates_from_pos força o terminal a
        # baixar o histórico do símbolo (~15s/série × 780 = horas) — só
        # usamos como fallback nos strikes ATM, os únicos que alimentam a
        # inversão de IV (|K−spot| ≤ MONEYNESS_IV).
        prem = float(getattr(info, "session_close", 0) or 0) or None if trust_session_close else None
        if prem is None and spot and abs(float(info.option_strike) - spot) / spot <= MONEYNESS_IV:
            prem = d1_close(mt5, tk, ref)
        options.append({
            "ticker": tk, "oi": row["oi"],
            "strike": float(info.option_strike),
            "is_call": getattr(info, "option_right", 0) == 0,
            "expiry": exp.isoformat() if exp else None,
            "premium": prem,
        })
        mt5.symbol_select(tk, False)  # não polui o Market Watch
    mt5.shutdown()
    log(f"  MT5: {len(options)} séries enriquecidas ({miss} sem metadados)")
    return {"spot": spot, "win_settle": win, "options": options}


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


def compute_gex(spot, win_settle, options, session_date):
    ref = date(*map(int, session_date.split("-")))
    # IV mediana por vencimento (dos prêmios que invertem perto do spot)
    iv_by_exp = defaultdict(list)
    for o in options:
        if not o["expiry"] or o["premium"] is None:
            continue
        if abs(o["strike"] - spot) / spot > MONEYNESS_IV:
            continue
        T = max((date(*map(int, o["expiry"].split("-"))) - ref).days, 0) / 365.0
        iv = _implied_vol(o["premium"], spot, o["strike"], T, R_FREE, o["is_call"])
        if iv and IV_MIN < iv < IV_MAX:
            iv_by_exp[o["expiry"]].append(iv)
    iv_exp = {e: _median(v) for e, v in iv_by_exp.items() if v}
    all_iv = [x for v in iv_by_exp.values() for x in v]
    iv_fallback = _median(all_iv) if all_iv else 0.20

    netgex = defaultdict(float)
    for o in options:
        if not o["expiry"]:
            continue
        T = max((date(*map(int, o["expiry"].split("-"))) - ref).days, 0) / 365.0
        if T <= 0:
            continue
        g = _bsm_gamma(spot, o["strike"], T, R_FREE, iv_exp.get(o["expiry"], iv_fallback))
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

    # gates de validade
    liquid = sum(1 for k in Ks if abs(k - spot) <= 5 * GRID_STEP and netgex[k] != 0)
    valid = (flip is not None and gmax > flip > gmin
             and liquid >= 8 and abs(flip - spot) < 15 * GRID_STEP)

    f = win_settle / spot
    walls = []
    if flip is not None:
        walls = [
            {"type": "gex_max", "price": round(gmax * f), "color": "#22C55E", "style": "solid"},
            {"type": "gex_flip", "price": round(flip * f), "color": "#EAB308", "style": "solid"},
            {"type": "gex_min", "price": round(gmin * f), "color": "#EF4444", "style": "solid"},
        ]
        centro = round(flip * f / (GRID_STEP * f)) * GRID_STEP
        for k in range(-8, 9):
            p = (centro + k * GRID_STEP) * f
            walls.append({"type": "wall", "price": round(p), "style": "solid",
                          "color": "#84CC16" if p > flip * f else "#EF4444"})
        for k in range(-8, 8):
            p = (centro + (k + 0.5) * GRID_STEP) * f
            walls.append({"type": "mid_wall", "price": round(p), "style": "dashed",
                          "color": "#9CA3AF" if p > flip * f else "#6B7280"})

    return {
        "gamma_max_ibov": gmax, "gamma_min_ibov": gmin, "gamma_flip_ibov": flip,
        "gamma_max": gmax * f, "gamma_min": gmin * f,
        "gamma_flip": (flip * f) if flip is not None else None,
        "spot": spot, "future_settle": win_settle, "conv_factor": f,
        "n_strikes": len(Ks), "liquid_strikes": liquid, "valid": bool(valid),
        "walls": walls,
        "meta": {"iv_by_exp": {k: round(v, 4) for k, v in iv_exp.items()},
                 "iv_fallback": round(iv_fallback, 4)},
    }


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
    args = ap.parse_args()

    log("=" * 50)
    log("IRAI GEX worker — gamma walls IBOV -> WIN$N")
    log("=" * 50)

    if args.date:
        session_date = args.date
        oi_rows = fetch_bdi_oi(session_date)
    else:
        session_date, oi_rows = last_session_with_oi()
        if not session_date:
            log("FALHA: nenhum pregão recente com OI no BDI")
            return 1
    log(f"pregão de referência: {session_date}")

    # session_close = "fechamento da sessão anterior" do terminal — só bate com
    # o pregão do OI no fluxo automático (timer pré-abertura). Com --date
    # explícito (reprocessamento histórico), usa as barras D1 datadas.
    data = fetch_mt5_data(oi_rows, session_date, trust_session_close=not args.date)
    if not data["spot"] or not data["win_settle"]:
        log("FALHA: sem spot IBOV ou settle WIN no MT5 p/ a data")
        return 1

    result = compute_gex(data["spot"], data["win_settle"], data["options"], session_date)
    if not result:
        log("FALHA: netGEX insuficiente")
        return 1

    fmt = lambda v: f"{v:,.0f}" if v is not None else "N/A"
    log(f"  GammaMax  = {fmt(result['gamma_max_ibov'])} IBOV -> {fmt(result['gamma_max'])} WIN")
    log(f"  GammaFlip = {fmt(result['gamma_flip_ibov'])} IBOV -> {fmt(result['gamma_flip'])} WIN")
    log(f"  GammaMin  = {fmt(result['gamma_min_ibov'])} IBOV -> {fmt(result['gamma_min'])} WIN")
    log(f"  válido={result['valid']} strikes={result['n_strikes']} f={result['conv_factor']:.6f}")

    if args.dry_run:
        log("[dry-run] nada gravado")
        return 0
    conn = get_connection(args.db)
    save(conn, session_date, result)
    conn.close()
    log(f"gravado em gex_levels ({session_date}, WIN$N)")
    # acorda a API (cache) — mesmo padrão do collector
    try:
        urllib.request.urlopen(
            urllib.request.Request("http://127.0.0.1:8888/api/internal/notify_update",
                                   method="POST"), timeout=2)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
