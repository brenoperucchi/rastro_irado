"""
IRAI — FastAPI Backend.

Endpoints:
  GET /api/irai/current       → snapshot corrente (live ou última sessão)
  GET /api/irai/series        → série completa de uma sessão
  GET /api/irai/dates         → datas disponíveis
  GET /api/model/params       → parâmetros do modelo
  GET /api/health             → status do sistema
"""

import os
import sys
from datetime import date, datetime, timedelta
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.db import get_connection, DB_PATH
from backend.irai.engine import IRAIEngine, FACTORS, FACTOR_LABELS, TARGET

# ── Engine singleton ──────────────────────────────────────
engine: IRAIEngine = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = IRAIEngine()
    print(f"IRAI Engine loaded: alpha={engine.alpha:.4f}, {len(engine.weights)} weights")
    yield
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
        "model": {
            "alpha": engine.alpha,
            "intercept": engine.intercept,
            "weights": engine.weights,
        },
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
    session_date: str = Query(None, description="Data YYYY-MM-DD (default: hoje)")
):
    """Série IRAI completa para uma sessão."""
    if session_date is None:
        session_date = date.today().isoformat()

    snapshots = engine.compute_from_db(session_date)

    if not snapshots:
        return JSONResponse(
            status_code=404,
            content={"error": f"Sem dados para sessão {session_date}"}
        )

    return {
        "session_date": session_date,
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
async def model_params():
    """Parâmetros do modelo calibrado."""
    return {
        "weights": engine.weights,
        "sigmas": engine.sigmas,
        "alpha": engine.alpha,
        "intercept": engine.intercept,
        "factors": list(FACTOR_LABELS.values()),
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.api.main:app", host="0.0.0.0", port=8888, reload=True)
