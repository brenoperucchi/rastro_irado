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
from backend.db import get_connection, migrate_to_head, DB_PATH
from backend.irai.engine import (
    IRAIEngine, FACTOR_LABELS, TARGET,
    DEFAULT_DIV_THRESHOLD, DEFAULT_P_UP_GATE_HI, DEFAULT_P_UP_GATE_LO,
)
from backend.irai.timezones import brt_to_tickmill_offset_hours
from backend.irai.zscore import PAIR_THRESHOLD

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
    migrate_to_head(DB_PATH)
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
    import sqlite3 as _sq
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM gex_levels WHERE target=? ORDER BY session_date DESC LIMIT 1",
            (target,),
        ).fetchone()
    except _sq.OperationalError as e:
        if "no such table" not in str(e):
            raise  # erro real de banco não pode virar "sem dados" silencioso
        row = None  # tabela ainda não existe (worker nunca rodou)
    conn.close()
    if not row:
        return {"active": False, "reason": "sem dados de GEX"}
    d = dict(row)
    try:
        age = (date.today() - date.fromisoformat(d["session_date"])).days
    except Exception:
        age = 999
    fresh = 0 <= age <= 4  # idade negativa = data futura corrompida -> não fresco
    try:
        walls = json.loads(d.get("walls") or "[]")
    except Exception:
        walls = []
    return {
        "active": bool(d.get("valid")) and fresh and bool(walls),
        "target": target,
        "as_of": d["session_date"],
        "age_days": age,
        "valid": bool(d.get("valid")),
        "gamma_max": d.get("gamma_max"),
        "gamma_flip": d.get("gamma_flip"),
        "gamma_min": d.get("gamma_min"),
        "spot": d.get("spot"),
        "conv_factor": d.get("conv_factor"),
        "walls": walls,
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


def _target_thresholds(slug: str) -> dict:
    """Thresholds efetivos (config + fallback) usados pelo engine pra este
    target — mesma leitura de divergence_config que compute_from_db faz
    (backend/irai/engine.py), pra nunca divergir do que o sinal usa de verdade."""
    div_cfg = engine.models.get(slug, {}).get("divergence_config", {})
    return {
        "pair_threshold": float(div_cfg.get("pair_threshold", PAIR_THRESHOLD)),
        "price_diverge_threshold": float(div_cfg.get("threshold", DEFAULT_DIV_THRESHOLD)),
        "p_up_gate_hi": float(div_cfg.get("p_up_gate_hi", DEFAULT_P_UP_GATE_HI)),
        "p_up_gate_lo": float(div_cfg.get("p_up_gate_lo", DEFAULT_P_UP_GATE_LO)),
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
                # Thresholds canônicos deste target (divergence_config, com os
                # MESMOS defaults que o engine usa pra calcular pair_signal/
                # price_diverges — backend/irai/engine.py). O frontend deve
                # desenhar/decidir a partir daqui, não hardcodar ±2 ou 55/45.
                **_target_thresholds(t["slug"]),
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

            res_obj = {
                "target": t["target"],
                "slug": t["slug"],
                "display_name": t["display_name"],
                "icon": t["icon"],
                "win_return": round(last.win_return, 4),
                "bars": len(primary),
                "accuracy": t.get("accuracy"),
                "flow_confirms": flow_confirms,
                # price_diverges/price_diverge_z/price_diverge_dir vêm direto do
                # snapshot já calculado pelo engine (compute_from_db acima) — não
                # recalcular aqui. Antes disto, este endpoint tinha uma 2ª cópia
                # independente da mesma fórmula (thresholds canônicos: unifica com
                # o que a engine já decide, elimina o risco de as duas divergirem).
                "price_diverges": getattr(last, "price_diverges", False),
                "price_diverge_z": getattr(last, "price_diverge_z", None),
                "price_diverge_dir": getattr(last, "price_diverge_dir", None),
                # NWE causal do último snapshot (já enriquecido pela engine).
                # NÃO reintroduzir "nwe_slope" sem sufixo (ver engine.py:109).
                "nwe_direction": getattr(last, "nwe_direction", None),
                "nwe_slope_price": getattr(last, "nwe_slope_price", 0.0),
                "nwe_center": getattr(last, "nwe_center", None),
                "nwe_upper": getattr(last, "nwe_upper", None),
                "nwe_lower": getattr(last, "nwe_lower", None),
                "nwe_available": getattr(last, "nwe_available", False),
                # Pair z-score do último bar (gauge "Par: X | β=…" + badge Pr)
                "pair_z": round(getattr(last, "pair_z", 0.0), 2),
                "pair_factor": getattr(last, "pair_factor", None),
                "pair_beta": round(getattr(last, "pair_beta", 0.0), 4),
                "pair_signal": getattr(last, "pair_signal", "neutral"),
                "is_preview": getattr(last, "is_preview", False),
            }

            res_obj.update({
                "p_up": round(last.p_up, 1),
                "version": version,   # qual motor gerou este p_up (v1 estático | v2 Kalman)
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
    # O engine desloca as barras da B3 em +brt_offset_h para o eixo do servidor.
    # O offset varia com o horário de verão (6h ou 5h), então o cliente não pode
    # assumir -6h fixo ao reconstruir o eixo BRT — precisa do valor da sessão.
    brt_offset_h = (
        brt_to_tickmill_offset_hours(datetime.fromisoformat(session_date))
        if is_b3 else 0
    )
    result = {
        "session_date": session_date,
        "target": target,
        "display_name": target_info.get("display_name", target),
        "icon": target_info.get("icon", "📊"),
        "bars": len(snapshots),
        "series": [_snap_to_dict(s) for s in snapshots],
        "history_closes": history_closes,
        "is_b3": is_b3,
        "brt_offset_h": brt_offset_h,
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
        "win_bar_open": getattr(snap, "win_bar_open", None),
        "win_high": getattr(snap, "win_high", None),
        "win_low": getattr(snap, "win_low", None),
        "win_current": snap.win_current,
        "stale_factors": snap.stale_factors,
        "bar_delta": snap.bar_delta,
        "cum_delta": snap.cum_delta,
        "cum_delta_norm": snap.cum_delta_norm,
        "flow_confirms": snap.flow_confirms,
        "price_diverges": snap.price_diverges,
        "price_diverge_z": snap.price_diverge_z,
        "price_diverge_dir": getattr(snap, "price_diverge_dir", None),
        # Pair z-score (sinal pairwise; só populado no v2, senão defaults)
        "pair_z": getattr(snap, "pair_z", 0.0),
        "pair_factor": getattr(snap, "pair_factor", None),
        "pair_beta": getattr(snap, "pair_beta", 0.0),
        "pair_signal": getattr(snap, "pair_signal", "neutral"),
        # Eventos discretos -> markers do chart de preço (TVNweChart). None em
        # toda barra que não é a transição do sinal, e em toda barra sintética.
        "pair_compra": getattr(snap, "pair_compra", None),
        "pair_venda": getattr(snap, "pair_venda", None),
        "z_compra_val": getattr(snap, "z_compra_val", None),
        "z_venda_val": getattr(snap, "z_venda_val", None),
        "is_preview": getattr(snap, "is_preview", False),
        "is_ghost": getattr(snap, "is_ghost", False),
        # NWE (Nadaraya-Watson Envelope) — fonte causal única (backend/irai/nwe.py),
        # enriquecido no snapshot pela engine. Floats opcionais viram None quando
        # indisponíveis; nunca "nwe_slope" sem sufixo (ver engine.py:109).
        "nwe_center_price": getattr(snap, "nwe_center_price", None),
        "nwe_upper_price": getattr(snap, "nwe_upper_price", None),
        "nwe_lower_price": getattr(snap, "nwe_lower_price", None),
        "nwe_center": getattr(snap, "nwe_center", None),
        "nwe_upper": getattr(snap, "nwe_upper", None),
        "nwe_lower": getattr(snap, "nwe_lower", None),
        "nwe_slope_price": getattr(snap, "nwe_slope_price", 0.0),
        "nwe_direction": getattr(snap, "nwe_direction", None),
        "nwe_available": getattr(snap, "nwe_available", False),
        "atr_14": getattr(snap, "atr_14", None),
        "atr_available": getattr(snap, "atr_available", False),
        "session_vwap": getattr(snap, "session_vwap", None),
        "vwap_available": getattr(snap, "vwap_available", False),
        "distance_to_nwe_atr": getattr(snap, "distance_to_nwe_atr", None),
        "distance_to_vwap_atr": getattr(snap, "distance_to_vwap_atr", None),
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
    ws_clients[ws] = {"target": "WIN$N", "version": "v2"}  # v2 = Kalman (antes: "both", que o engine resolve como V1 estático)
    print(f"WS client connected ({len(ws_clients)} total)")
    
    # Enviar o estado atual imediatamente na conexão
    try:
        today = date.today().isoformat()
        ov = await irai_overview(today, "v2")
        se = await irai_series(today, "WIN$N", "v2")
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
