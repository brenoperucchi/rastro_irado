"""
IRAI — FastAPI Backend.

Endpoints:
  GET  /api/irai/current       → snapshot corrente (live ou última sessão)
  GET  /api/irai/series        → série completa de uma sessão (target=WIN$N|DOL$N)
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.db import get_connection, DB_PATH
from backend.irai.engine import IRAIEngine, FACTOR_LABELS, TARGET

# ── Engine singleton ──────────────────────────────────────
engine: IRAIEngine = None
ws_clients: set = set()


async def ws_broadcast_loop():
    """Push dados para todos os WebSocket clients a cada 5s."""
    while True:
        await asyncio.sleep(5)
        if not ws_clients:
            continue
        try:
            today = date.today().isoformat()
            snapshots = engine.compute_from_db(today)
            if not snapshots:
                continue
            payload = json.dumps({
                "type": "series",
                "session_date": today,
                "bars": len(snapshots),
                "series": [_snap_to_dict(s) for s in snapshots],
                "summary": {
                    "p_up_min": min(s.p_up for s in snapshots),
                    "p_up_max": max(s.p_up for s in snapshots),
                    "p_up_final": snapshots[-1].p_up,
                    "score_final": snapshots[-1].score,
                    "verdict": snapshots[-1].verdict,
                    "win_return": snapshots[-1].win_return,
                },
            })
            dead = set()
            for ws in ws_clients.copy():
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.add(ws)
            ws_clients -= dead
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
                "session_hours": f"{t['session_start_h']:02d}-{t['session_end_h']:02d} UTC",
            }
            for t in engine.registered_targets
        ]
    }


@app.get("/api/irai/overview")
async def irai_overview(
    session_date: str = Query(None, description="Data YYYY-MM-DD (default: hoje)"),
):
    """Snapshot atual de TODOS os targets calibrados."""
    if session_date is None:
        # Usar último dia útil com dados completos
        conn = get_connection()
        row = conn.execute("""
            SELECT DISTINCT substr(timestamp_utc, 1, 10) as d
            FROM market_bars WHERE timeframe='M5' AND symbol='WIN$N'
            ORDER BY d DESC LIMIT 1
        """).fetchone()
        conn.close()
        if row:
            session_date = row["d"]
        else:
            session_date = date.today().isoformat()

    results = []
    for t in engine.registered_targets:
        if not t.get("accuracy"):
            continue  # Skip não-calibrados

        try:
            snapshots = engine.compute_from_db(session_date, target=t["target"])
            if not snapshots:
                continue
            last = snapshots[-1]
            # Sparkline: P_up de cada barra
            sparkline = [round(s.p_up, 1) for s in snapshots[-24:]]  # últimas 2h
            results.append({
                "target": t["target"],
                "slug": t["slug"],
                "display_name": t["display_name"],
                "icon": t["icon"],
                "p_up": round(last.p_up, 1),
                "score": round(last.score, 4),
                "verdict": last.verdict,
                "win_return": round(last.win_return, 4),
                "bars": len(snapshots),
                "accuracy": t.get("accuracy"),
                "sparkline": sparkline,
            })
        except Exception as e:
            print(f"Overview error for {t['target']}: {e}")

    return {
        "session_date": session_date,
        "targets": results,
    }


@app.get("/api/irai/dates")
async def irai_dates():
    """Datas com dados disponíveis."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT substr(timestamp_utc, 1, 10) as d
        FROM market_bars
        WHERE symbol = ? AND timeframe = 'M5'
        ORDER BY d DESC
        LIMIT 60
    """, [TARGET]).fetchall()
    conn.close()
    return {"dates": [r["d"] for r in rows]}


@app.get("/api/irai/series")
async def irai_series(
    session_date: str = Query(None, description="Data YYYY-MM-DD (default: hoje)"),
    target: str = Query("WIN$N", description="Target: WIN$N ou DOL$N"),
):
    """Série IRAI completa para uma sessão. Suporta multi-target."""
    if session_date is None:
        session_date = date.today().isoformat()

    snapshots = engine.compute_from_db(session_date, target=target)

    if not snapshots:
        return JSONResponse(
            status_code=404,
            content={"error": f"Sem dados para sessão {session_date}"}
        )

    # Lookup display info
    target_info = next((t for t in engine.registered_targets if t["target"] == target), {})
    return {
        "session_date": session_date,
        "target": target,
        "display_name": target_info.get("display_name", target),
        "icon": target_info.get("icon", "📊"),
        "bars": len(snapshots),
        "series": [_snap_to_dict(s) for s in snapshots],
        "summary": {
            "p_up_min": min(s.p_up for s in snapshots),
            "p_up_max": max(s.p_up for s in snapshots),
            "p_up_final": snapshots[-1].p_up,
            "score_final": snapshots[-1].score,
            "verdict": snapshots[-1].verdict,
            "return": snapshots[-1].win_return,
        },
    }


@app.get("/api/irai/current")
async def irai_current():
    """Snapshot mais recente (última barra processada)."""
    # Tentar sessão de hoje, senão último dia disponível
    today = date.today().isoformat()
    snapshots = engine.compute_from_db(today)

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
            snapshots = engine.compute_from_db(row["d"])

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
    }


def _bar_time(bar_idx: int) -> str:
    """Converte índice de barra para horário BRT."""
    total_min = 10 * 60 + bar_idx * 5
    h = total_min // 60
    m = total_min % 60
    return f"{h:02d}:{m:02d}"


@app.websocket("/ws/irai")
async def websocket_irai(ws: WebSocket):
    """WebSocket push: envia série IRAI a cada 5s."""
    await ws.accept()
    ws_clients.add(ws)
    print(f"WS client connected ({len(ws_clients)} total)")
    try:
        while True:
            # Keep alive — espera por mensagens do client (ping/pong)
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_clients.discard(ws)
        print(f"WS client disconnected ({len(ws_clients)} total)")
    except Exception:
        ws_clients.discard(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.api.main:app", host="0.0.0.0", port=8888, reload=True)
