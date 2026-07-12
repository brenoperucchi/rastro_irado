"""
IRAI — FastAPI Backend.

Endpoints:
  GET  /api/irai/current       → snapshot corrente (live ou última sessão)
  GET  /api/irai/series        → série completa de uma sessão (target=WIN$N|WDO$N)
  GET  /api/irai/dates         → datas disponíveis
  GET  /api/model/params       → parâmetros do modelo
  GET  /api/health             → status do sistema
  WS   /ws/irai                → push em tempo real (5s)
"""

import os
import math
import sys
import asyncio
import json
from datetime import date, datetime, timedelta
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backend.db import get_connection, DB_PATH
from backend.irai.engine import IRAIEngine, FACTOR_LABELS, TARGET

# ── Engine singleton ──────────────────────────────────────
engine: IRAIEngine = None
ws_clients: dict = {}
data_updated_event = asyncio.Event()

# ── Cache de resultados computados ─────────────────────
series_cache: dict = {}   # (target, date, version) → result dict
overview_cache_data: dict = {} # (date, version) → result dict


async def ws_broadcast_loop():
    """Push dados para todos os WebSocket clients quando ativado pelo collector."""
    while True:
        await data_updated_event.wait()
        data_updated_event.clear()
        
        if not ws_clients:
            continue
            
        try:
            # Usar a data mais recente do banco (mesma lógica do overview/dates)
            conn = get_connection()
            row = conn.execute("""
                SELECT DISTINCT substr(timestamp_utc, 1, 10) as d
                FROM market_bars WHERE timeframe='M5'
                ORDER BY d DESC LIMIT 1
            """).fetchone()
            conn.close()
            session_date = row["d"] if row else date.today().isoformat()

            overview_cache = None
            
            dead = set()
            for ws, config in ws_clients.copy().items():
                try:
                    target = config.get("target", "WIN$N")
                    version = config.get("version", "v1")
                    
                    ov_key = (session_date, version)
                    if ov_key not in overview_cache_data:
                        await irai_overview(session_date, version) # Isso vai popular o cache
                        
                    se_key = (target, session_date, version)
                    if se_key not in series_cache:
                        res = await irai_series(session_date, target, version)
                        if isinstance(res, JSONResponse):
                            series_cache[se_key] = {"error": "Sem dados"}
                        else:
                            series_cache[se_key] = res
                            
                    payload = json.dumps({
                        "type": "update",
                        "session_date": session_date,
                        "overview": overview_cache_data[ov_key],
                        "series": series_cache[se_key]
                    })
                    await ws.send_text(payload)
                except Exception:
                    dead.add(ws)
            for ws in dead:
                ws_clients.pop(ws, None)
        except Exception as e:
            print(f"WS broadcast error: {e}")




@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = IRAIEngine()
    print(f"IRAI Engine loaded: {len(engine.models)} models, {len(engine.registered_targets)} targets")
    task = asyncio.create_task(ws_broadcast_loop())
    yield
    task.cancel()
    print("IRAI Engine shutdown")


app = FastAPI(
    title="IRAI API",
    description="Intraday Risk Appetite Index — Cross-asset IBOV probability",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/internal/notify_update")
async def notify_update():
    """Chamado pelo collector.py após inserir novas barras."""
    series_cache.clear()
    overview_cache_data.clear()
    data_updated_event.set()
    return {"status": "ok"}


@app.get("/api/irai/gex")
async def get_gex(target: str = Query("WIN$N")):
    """Últimos níveis de GEX (gamma walls) do target, gerados pelo gex_worker.

    `active` = dado válido E fresco (≤4 dias corridos cobre fim de semana +
    feriado). O frontend só desenha as walls quando active=True — nunca plota
    GEX envelhecido como se fosse do dia.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM gex_levels WHERE target=? ORDER BY session_date DESC LIMIT 1",
            (target,),
        ).fetchone()
    except Exception:
        row = None  # tabela ainda não existe (worker nunca rodou)
    conn.close()
    if not row:
        return {"active": False, "reason": "sem dados de GEX"}
    d = dict(row)
    try:
        age = (date.today() - date.fromisoformat(d["session_date"])).days
    except Exception:
        age = 999
    fresh = age <= 4
    return {
        "active": bool(d.get("valid")) and fresh,
        "as_of": d["session_date"],
        "age_days": age,
        "valid": bool(d.get("valid")),
        "gamma_max": d.get("gamma_max"),
        "gamma_flip": d.get("gamma_flip"),
        "gamma_min": d.get("gamma_min"),
        "spot": d.get("spot"),
        "conv_factor": d.get("conv_factor"),
        "walls": json.loads(d.get("walls") or "[]"),
    }


@app.get("/api/health")
async def health():
    """Status do sistema."""
    conn = get_connection()
    bar_count = conn.execute("SELECT COUNT(*) as c FROM market_bars").fetchone()["c"]
    last_bar = conn.execute(
        "SELECT MAX(timestamp_utc) as ts FROM market_bars WHERE timeframe='M5'"
    ).fetchone()["ts"]
    conn.close()
    return {
        "status": "ok",
        "bars_total": bar_count,
        "last_bar": last_bar,
        "models_loaded": len(engine.models),
        "targets": [t["target"] for t in engine.registered_targets],
    }


@app.get("/api/irai/targets")
async def irai_targets():
    """Lista todos os targets disponíveis com status."""
    return {
        "targets": [
            {
                "target": t["target"],
                "slug": t["slug"],
                "display_name": t["display_name"],
                "icon": t["icon"],
                "accuracy": t.get("accuracy"),
                "r_squared": t.get("r_squared"),
                "calibrated": t.get("accuracy") is not None,
                "session_hours": f"{t['session_start_h']:02d}h-{t['session_end_h']:02d}h",
            }
            for t in engine.registered_targets
        ]
    }


@app.get("/api/irai/overview")
async def irai_overview(
    session_date: str = Query(None, description="Data YYYY-MM-DD (default: hoje)"),
    version: str = Query("v2", description="Versão do motor (v1=estático, v2=dinâmico)"),
):
    """Snapshot atual de TODOS os targets calibrados."""
    if session_date is None:
        # Usar último dia com dados (qualquer símbolo — inclui internacional no fim de semana)
        conn = get_connection()
        row = conn.execute("""
            SELECT DISTINCT substr(timestamp_utc, 1, 10) as d
            FROM market_bars WHERE timeframe='M5'
            ORDER BY d DESC LIMIT 1
        """).fetchone()
        conn.close()
        if row:
            session_date = row["d"]
        else:
            session_date = date.today().isoformat()

    # Return cached overview if available for the same date
    cache_key = (session_date, version)
    if cache_key in overview_cache_data:
        return overview_cache_data[cache_key]

    results = []
    for t in engine.registered_targets:
        if not t.get("accuracy"):
            continue  # Skip não-calibrados

        try:
            primary = engine.compute_from_db(session_date, target=t["target"], version=version)
            if not primary:
                continue
                
            last = primary[-1]

            # Sparklines
            sparkline = [round(s.p_up, 1) for s in primary[-24:]] if primary else []

            flow_confirms = getattr(last, "flow_confirms", None)
            
            # Price diverges
            price_diverges = False
            price_diverge_z = None
            try:
                slug = t["slug"]
                m = engine.models.get(slug, {})
                div_cfg = m.get("divergence_config", {"sigma": 0.005, "threshold": 0.5})
                target_div_sigma = div_cfg.get("sigma", 0.005)
                target_div_threshold = div_cfg.get("threshold", 0.5)
                
                if target_div_sigma > 0 and last.t_frac > 0:
                    ret_frac = last.win_return / 100.0
                    ret_z = ret_frac / (target_div_sigma * math.sqrt(last.t_frac))
                    price_diverge_z = round(ret_z, 2)
                    
                    if last.p_up > 55 and ret_z < -target_div_threshold:
                        price_diverges = True
                    elif last.p_up < 45 and ret_z > target_div_threshold:
                        price_diverges = True
            except Exception:
                pass

            # NWE
            nwe_slope = 0.0
            try:
                if len(primary) >= 2:
                    h = 8
                    n = len(primary)
                    vals = [s.win_return for s in primary]
                    def get_center(i_idx):
                        sum_w = 0.0
                        sum_y = 0.0
                        for j in range(n):
                            w = math.exp(-((i_idx - j) ** 2) / (2 * h * h))
                            sum_w += w
                            sum_y += w * vals[j]
                        return sum_y / sum_w if sum_w > 0 else vals[i_idx]
                    
                    c_last = get_center(n - 1)
                    c_prev = get_center(n - 2)
                    nwe_slope = round(c_last - c_prev, 6)
            except Exception:
                pass
                
            res_obj = {
                "target": t["target"],
                "slug": t["slug"],
                "display_name": t["display_name"],
                "icon": t["icon"],
                "win_return": round(last.win_return, 4),
                "bars": len(primary),
                "accuracy": t.get("accuracy"),
                "flow_confirms": flow_confirms,
                "price_diverges": price_diverges,
                "price_diverge_z": price_diverge_z,
                "nwe_slope": nwe_slope,
                # Pair z-score do último bar (gauge "Par: X | β=…" + badge Pr)
                "pair_z": round(getattr(last, "pair_z", 0.0), 2),
                "pair_factor": getattr(last, "pair_factor", None),
                "pair_beta": round(getattr(last, "pair_beta", 0.0), 4),
                "pair_signal": getattr(last, "pair_signal", "neutral"),
                "is_preview": getattr(last, "is_preview", False),
            }

            res_obj.update({
                "p_up": round(last.p_up, 1),
                "score": round(last.score, 4),
                "verdict": last.verdict,
                "sparkline": sparkline,
            })

            results.append(res_obj)
        except Exception as e:
            print(f"Overview error for {t['target']}: {e}")

    result = {
        "session_date": session_date,
        "version": version,
        "targets": results,
    }
    overview_cache_data[cache_key] = result
    return result


@app.get("/api/irai/dates")
async def irai_dates(
    target: str = Query(None, description="Filtrar datas por target específico"),
):
    """Datas com dados disponíveis."""
    conn = get_connection()
    if target:
        rows = conn.execute("""
            SELECT DISTINCT substr(timestamp_utc, 1, 10) as d
            FROM market_bars
            WHERE symbol = ? AND timeframe = 'M5'
            ORDER BY d DESC
            LIMIT 60
        """, [target]).fetchall()
    else:
        rows = conn.execute("""
            SELECT DISTINCT substr(timestamp_utc, 1, 10) as d
            FROM market_bars
            WHERE timeframe = 'M5'
            ORDER BY d DESC
            LIMIT 60
        """).fetchall()
    conn.close()
    return {"dates": [r["d"] for r in rows]}


@app.get("/api/irai/series")
async def irai_series(
    session_date: str = Query(None, description="Data YYYY-MM-DD (default: hoje)"),
    target: str = Query("WIN$N", description="Target: WIN$N ou WDO$N"),
    version: str = Query("v2", description="Versão do motor (v1=estático, v2=dinâmico)"),
):
    """Série IRAI completa para uma sessão. Suporta multi-target."""
    if session_date is None:
        session_date = date.today().isoformat()

    # Check cache first
    cache_key = (target, session_date, version)
    if cache_key in series_cache:
        return series_cache[cache_key]

    conn = get_connection()
    target_db = next((t["data_proxy"] for t in engine.registered_targets if t["target"] == target), target)
    if not target_db: target_db = target
    prev_rows = conn.execute("""
        SELECT close FROM market_bars
        WHERE symbol = ? AND timeframe = 'M5' AND timestamp_utc < ?
        ORDER BY timestamp_utc DESC LIMIT 95
    """, (target_db, f"{session_date}T00:00:00Z")).fetchall()
    conn.close()
    history_closes = [r["close"] for r in reversed(prev_rows)]

    snapshots = engine.compute_from_db(session_date, target=target, version=version)
    if not snapshots:
        return JSONResponse(status_code=404, content={"error": f"Sem dados para sessão {session_date}"})

    target_info = next((t for t in engine.registered_targets if t["target"] == target), {})
    # B3 assets (WIN$N, WDO$N) need BRT offset (UTC-3) for dual axis
    is_b3 = target_info.get("session_start_h", 0) != 0
    result = {
        "session_date": session_date,
        "target": target,
        "display_name": target_info.get("display_name", target),
        "icon": target_info.get("icon", "📊"),
        "bars": len(snapshots),
        "series": [_snap_to_dict(s) for s in snapshots],
        "history_closes": history_closes,
        "is_b3": is_b3,
        "summary": {
            "p_up_min": min(s.p_up for s in snapshots),
            "p_up_max": max(s.p_up for s in snapshots),
            "p_up_final": snapshots[-1].p_up,
            "score_final": snapshots[-1].score,
            "verdict": snapshots[-1].verdict,
            "win_return": snapshots[-1].win_return,
            "timestamp": snapshots[-1].timestamp,
            "accuracy": target_info.get("accuracy"),
        }
    }
    series_cache[(target, session_date, version)] = result
    return result


@app.get("/api/irai/current")
async def irai_current(version: str = Query("v1")):
    """Snapshot mais recente (última barra processada)."""
    # Tentar sessão de hoje, senão último dia disponível
    today = date.today().isoformat()
    snapshots = engine.compute_from_db(today, version=version)

    if not snapshots:
        # Pegar último dia com dados
        conn = get_connection()
        row = conn.execute("""
            SELECT DISTINCT substr(timestamp_utc, 1, 10) as d
            FROM market_bars
            WHERE symbol = ? AND timeframe = 'M5'
            ORDER BY d DESC LIMIT 1
        """, [TARGET]).fetchone()
        conn.close()

        if row:
            snapshots = engine.compute_from_db(row["d"], version=version)

    if not snapshots:
        return JSONResponse(status_code=404, content={"error": "Sem dados"})

    last = snapshots[-1]
    return _snap_to_dict(last)


@app.get("/api/model/params")
async def model_params(target: str = Query("WIN$N")):
    """Parâmetros do modelo calibrado para um target."""
    slug = engine.target_slugs.get(target, "win")
    m = engine.models.get(slug, {})
    return {
        "target": target,
        "slug": slug,
        "weights": m.get("weights", {}),
        "sigmas": m.get("sigmas", {}),
        "alpha": m.get("alpha", 1.0),
        "intercept": m.get("intercept", 0.0),
        "factors": list(m.get("factor_labels", {}).values()),
    }


def _snap_to_dict(snap) -> dict:
    """Converte snapshot para dict serializável."""
    return {
        "timestamp": snap.timestamp,
        "session_date": snap.session_date,
        "bar_idx": snap.bar_idx,
        "t_frac": snap.t_frac,
        "p_up": snap.p_up,
        "score": snap.score,
        "verdict": snap.verdict,
        "verdict_color": snap.verdict_color,
        "factors": snap.factors,
        "win_return": snap.win_return,
        "win_open": snap.win_open,
        "win_current": snap.win_current,
        "stale_factors": snap.stale_factors,
        "bar_delta": snap.bar_delta,
        "cum_delta": snap.cum_delta,
        "cum_delta_norm": snap.cum_delta_norm,
        "flow_confirms": snap.flow_confirms,
        "price_diverges": snap.price_diverges,
        "price_diverge_z": snap.price_diverge_z,
        # Pair z-score (sinal pairwise; só populado no v2, senão defaults)
        "pair_z": getattr(snap, "pair_z", 0.0),
        "pair_factor": getattr(snap, "pair_factor", None),
        "pair_beta": getattr(snap, "pair_beta", 0.0),
        "pair_signal": getattr(snap, "pair_signal", "neutral"),
        "is_preview": getattr(snap, "is_preview", False),
        "is_ghost": getattr(snap, "is_ghost", False),
    }


def _bar_time(bar_idx: int) -> str:
    """Converte índice de barra para horário BRT."""
    total_min = 10 * 60 + bar_idx * 5
    h = total_min // 60
    m = total_min % 60
    return f"{h:02d}:{m:02d}"


@app.websocket("/ws/irai")
async def websocket_irai(ws: WebSocket):
    """WebSocket push: envia série IRAI atualizada baseada no target."""
    await ws.accept()
    ws_clients[ws] = {"target": "WIN$N", "version": "both"} # Default
    print(f"WS client connected ({len(ws_clients)} total)")
    
    # Enviar o estado atual imediatamente na conexão
    try:
        today = date.today().isoformat()
        ov = await irai_overview(today, "both")
        se = await irai_series(today, "WIN$N", "both")
        if isinstance(se, JSONResponse): se = {"error": "Sem dados"}
        await ws.send_text(json.dumps({"type": "update", "overview": ov, "series": se}))
    except Exception as e:
        print(f"Initial WS send error: {e}")
        
    try:
        while True:
            # Recebe mensagens de configuração (mudança de target)
            data = await ws.receive_json()
            if data:
                if "target" in data:
                    ws_clients[ws]["target"] = data["target"]
                if "version" in data:
                    ws_clients[ws]["version"] = data["version"]
                
                # Força um envio imediato com as novas configurações
                today = date.today().isoformat()
                t = ws_clients[ws]["target"]
                v = ws_clients[ws]["version"]
                ov = await irai_overview(today, v)
                se = await irai_series(today, t, v)
                if isinstance(se, JSONResponse): se = {"error": "Sem dados"}
                await ws.send_text(json.dumps({"type": "update", "overview": ov, "series": se}))
    except WebSocketDisconnect:
        ws_clients.pop(ws, None)
        print(f"WS client disconnected ({len(ws_clients)} total)")
    except Exception:
        ws_clients.pop(ws, None)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.api.main:app", host="0.0.0.0", port=8888, reload=True)
