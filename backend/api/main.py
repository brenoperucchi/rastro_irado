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
import math
import time
from datetime import date, datetime, timedelta, timezone
from contextlib import asynccontextmanager
from dataclasses import asdict
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backend.db import get_connection, migrate_to_head, DB_PATH
from backend.workers.gex_worker import build_walls
from backend.irai.engine import (
    IRAIEngine, FACTOR_LABELS, TARGET,
    DEFAULT_DIV_THRESHOLD, DEFAULT_P_UP_GATE_HI, DEFAULT_P_UP_GATE_LO,
)
from backend.irai.timezones import brt_to_tickmill_offset_hours
from backend.irai.zscore import PAIR_THRESHOLD
from backend.irai.miqueias_static import (
    build_miqueias_static_rows,
    load_default_miqueias_static_config,
)

# ── Engine singleton ──────────────────────────────────────
engine: IRAIEngine = None
ws_clients: dict = {}
data_updated_event = asyncio.Event()

# ── Cache de resultados computados ─────────────────────
series_cache: dict = {}   # (target, date, version) → result dict
overview_cache_data: dict = {} # (date, version) → result dict
p_dynamic_comparison_cache: dict = {}  # (target, date) → payload diagnóstico
miqueias_public_cache: dict = {}       # payload remoto curto, compartilhado por sessão

MIQUEIAS_PUBLIC_SOURCE = (
    "https://rastromacro-default-rtdb.firebaseio.com/series/WIN_N.json"
)
MIQUEIAS_PUBLIC_CACHE_SECONDS = 60


def _current_gex_walls(row: dict, stored_walls: list) -> list:
    """Re-deriva a geometria visual (walls/mid-walls) a partir dos níveis já
    calculados na linha. Roda incondicionalmente para linhas live e
    históricas — para live é idempotente (build_walls já centra no spot),
    mas o principal motivo de existir é normalizar snapshots antigos cuja
    grade foi gravada com uma convenção de centro diferente da atual."""
    try:
        meta = json.loads(row.get("meta") or "{}")
        grid_step = float(meta["grid_step"])
        if grid_step <= 0:
            return stored_walls
        return build_walls(
            float(row["gamma_max_ibov"]), float(row["gamma_min_ibov"]),
            float(row["gamma_flip_ibov"]), float(row["spot"]),
            float(row["conv_factor"]), grid_step,
        )
    except (KeyError, TypeError, ValueError):
        return stored_walls


def _flip_grid_signal(walls: list, gamma_flip, spot, conv_factor) -> dict | None:
    """F1 (decisão do usuário, revisão tri-r): a grade de 17 walls é centrada
    no SPOT, não no Flip -- em mercado put-heavy ou após um movimento
    intraday grande, o Flip pode cair fora da faixa desenhada e o trader não
    vê no chart onde ele está, mesmo com as walls visíveis coloridas
    "corretamente" (a cor de cada wall segue relativa ao Flip, isso não muda
    aqui). Deriva só da geometria já calculada (`walls`, que já embutem
    conv_factor) -- não recalcula Gamma/Flip nem recolore wall nenhuma; é
    puramente informativo, direção e distância AO SPOT (não à grade)."""
    if gamma_flip is None or spot is None or not conv_factor:
        return None
    grid_prices = [w["price"] for w in walls if w.get("type") == "wall"]
    if not grid_prices:
        return None
    grid_min, grid_max = min(grid_prices), max(grid_prices)
    if grid_min <= gamma_flip <= grid_max:
        return {"outside_grid": False, "direction": None, "distance_to_spot": None}
    # spot*conv_factor == future_settle por definição de conv_factor
    # (f = win_settle/spot em compute_gex) -- mesmo espaço de preço dos walls.
    spot_price = spot * conv_factor
    distance = gamma_flip - spot_price
    return {
        "outside_grid": True,
        "direction": "above" if distance >= 0 else "below",
        "distance_to_spot": round(abs(distance), 2),
    }


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
    p_dynamic_comparison_cache.clear()
    miqueias_public_cache.clear()
    data_updated_event.set()
    return {"status": "ok"}


@app.get("/api/irai/gex")
async def get_gex(
    target: str = Query("WIN$N"),
    session_date: str | None = Query(None, alias="date"),
):
    """Níveis de GEX live ou do snapshot histórico da sessão pedida.

    Sem ``date``, entrega o último cálculo live, que precisa ser fresco. Com
    ``date``, consulta o snapshot PIT disponível naquela sessão em
    ``gex_history_levels``; não pode recuar para o live, que pertence a outro
    contexto temporal.
    """
    import sqlite3 as _sq
    conn = get_connection()
    # Em chamadas diretas de teste, FastAPI não resolve Query(None): chega o
    # objeto Query em vez de None. No request real, só uma string ISO ativa o
    # caminho histórico.
    historical = isinstance(session_date, str) and bool(session_date)
    try:
        if historical:
            row = conn.execute(
                """SELECT * FROM gex_history_levels
                   WHERE target=? AND effective_session_date=?
                   ORDER BY source_session_date DESC LIMIT 1""",
                (target, session_date),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM gex_levels WHERE target=? ORDER BY session_date DESC LIMIT 1",
                (target,),
            ).fetchone()
    except _sq.OperationalError as e:
        if "no such table" not in str(e):
            raise  # erro real de banco não pode virar "sem dados" silencioso
        row = None  # worker/backfill ainda não criou a tabela necessária
    conn.close()
    if not row:
        if historical:
            return {
                "active": False,
                "historical": True,
                "reason": "sem dados de GEX para a data selecionada",
            }
        return {"active": False, "reason": "sem dados de GEX"}
    d = dict(row)
    try:
        walls = json.loads(d.get("walls") or "[]")
    except Exception:
        walls = []
    walls = _current_gex_walls(d, walls)
    response = {
        "active": bool(d.get("valid")) and bool(walls),
        "historical": historical,
        "target": target,
        "valid": bool(d.get("valid")),
        "gamma_max": d.get("gamma_max"),
        "gamma_flip": d.get("gamma_flip"),
        "gamma_min": d.get("gamma_min"),
        "spot": d.get("spot"),
        "conv_factor": d.get("conv_factor"),
        "walls": walls,
        "flip_grid_signal": _flip_grid_signal(
            walls, d.get("gamma_flip"), d.get("spot"), d.get("conv_factor")),
    }
    if historical:
        response.update({
            "as_of": d["effective_session_date"],
            "source_as_of": d["source_session_date"],
            "age_days": None,
        })
        return response

    try:
        age = (date.today() - date.fromisoformat(d["session_date"])).days
    except Exception:
        age = 999
    fresh = 0 <= age <= 4  # idade negativa = data futura corrompida -> não fresco
    response.update({
        "active": response["active"] and fresh,
        "as_of": d["session_date"],
        "age_days": age,
    })
    return response


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


def _comparison_points(rows: list[dict]) -> list[dict]:
    """Reduz snapshots ao contrato necessário pelo chart de comparação."""
    return [
        {
            "timestamp": row.get("timestamp"),
            "p_up": row.get("p_up"),
            "is_ghost": bool(row.get("is_ghost", False)),
            "is_preview": bool(row.get("is_preview", False)),
        }
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("timestamp"), str)
    ]


def _fetch_miqueias_public_document():
    request = Request(MIQUEIAS_PUBLIC_SOURCE, headers={"User-Agent": "IRAI-dashboard/1.0"})
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _miqueias_public_rows(document: object, session_date: str) -> list[dict]:
    """Extrai uma sessão sem inventar alinhamento ou preencher barras ausentes."""
    if isinstance(document, list):
        rows = document
    elif isinstance(document, dict):
        series = document.get("series")
        if isinstance(series, list):
            rows = series
        elif isinstance(series, dict) and isinstance(series.get("WIN_N"), list):
            rows = series["WIN_N"]
        else:
            raise ValueError("payload público não contém série WIN_N")
    else:
        raise ValueError("payload público não é uma série JSON")

    points = []
    seen_timestamps = set()
    for row_number, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"barra pública {row_number} não é objeto JSON")
        timestamp = row.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            raise ValueError(f"barra pública {row_number} sem timestamp")
        try:
            moment = datetime.fromisoformat(
                timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
            )
        except ValueError as exc:
            raise ValueError(f"timestamp público inválido: {timestamp!r}") from exc
        if moment.utcoffset() is None:
            raise ValueError(f"timestamp público sem fuso explícito: {timestamp!r}")
        moment = moment.astimezone(timezone.utc)
        timestamp = moment.isoformat(timespec="seconds").replace("+00:00", "Z")
        if moment.date().isoformat() != session_date:
            continue
        value_field = "p_up_v1" if row.get("p_up_v1") is not None else "p_up"
        value = row.get(value_field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{value_field} público não é numérico")
        probability = float(value)
        if not math.isfinite(probability) or not 0 <= probability <= 100:
            raise ValueError(f"{value_field} público fora de 0..100")
        if timestamp in seen_timestamps:
            raise ValueError(f"timestamp público duplicado: {timestamp}")
        seen_timestamps.add(timestamp)
        points.append({
            "timestamp": timestamp,
            "p_up": probability,
            "is_ghost": bool(row.get("is_ghost", False)),
            "is_preview": bool(row.get("is_preview", False)),
            "source_field": value_field,
        })
    return sorted(points, key=lambda point: point["timestamp"])


async def _miqueias_public_points(session_date: str) -> tuple[list[dict], str | None]:
    """Carrega o feed externo fora do event loop e o reutiliza por 60 segundos."""
    now = time.monotonic()
    cached_at = miqueias_public_cache.get("fetched_at")
    if cached_at is None or now - cached_at >= MIQUEIAS_PUBLIC_CACHE_SECONDS:
        try:
            miqueias_public_cache["document"] = await asyncio.to_thread(
                _fetch_miqueias_public_document
            )
            miqueias_public_cache["error"] = None
        except Exception as exc:
            miqueias_public_cache["document"] = None
            miqueias_public_cache["error"] = type(exc).__name__
        miqueias_public_cache["fetched_at"] = now

    if miqueias_public_cache.get("error"):
        return [], f"falha ao carregar série pública ({miqueias_public_cache['error']})"
    try:
        return _miqueias_public_rows(miqueias_public_cache.get("document"), session_date), None
    except ValueError as exc:
        return [], f"série pública inválida ({exc})"


def _series_response_or_error(response, version: str) -> dict:
    if not isinstance(response, JSONResponse):
        return response
    try:
        content = json.loads(response.body)
        detail = content.get("error", "sem dados")
    except (TypeError, ValueError, AttributeError):
        detail = "sem dados"
    raise HTTPException(
        status_code=response.status_code,
        detail=f"série local {version} indisponível: {detail}",
    )


@app.get("/api/irai/p-dynamic-comparison")
async def p_dynamic_comparison(
    session_date: str = Query(None, description="Data YYYY-MM-DD (default: hoje)"),
    target: str = Query("WIN$N", description="Comparação disponível para WIN$N"),
):
    """Séries diagnósticas alinhadas para comparar P Dinâmico no gráfico.

    Não altera o ``P_up`` ativo: v1, v2, Miqueias público e a hipótese estática
    são apenas curvas paralelas de auditoria. A série pública é omitida quando
    a fonte estiver em outra sessão, para não comparar pregões diferentes.
    """
    if target != "WIN$N":
        raise HTTPException(status_code=400, detail="comparação disponível apenas para WIN$N")
    if session_date is None:
        session_date = date.today().isoformat()

    cache_key = (target, session_date)
    if cache_key in p_dynamic_comparison_cache:
        return p_dynamic_comparison_cache[cache_key]

    # A engine conserva estado Kalman no processo; calcular em sequência evita
    # duas recomputações concorrentes disputando o mesmo estado durante live.
    v1_response = await irai_series(session_date=session_date, target=target, version="v1")
    v2_response = await irai_series(session_date=session_date, target=target, version="v2")
    v1 = _series_response_or_error(v1_response, "v1")
    v2 = _series_response_or_error(v2_response, "v2")
    v2_rows = v2.get("series", [])

    try:
        static_config = load_default_miqueias_static_config()
        if static_config.target != target:
            raise ValueError(f"configuração é para {static_config.target}")
        static_points = build_miqueias_static_rows(v2_rows, static_config)
        static_availability = {"available": True}
    except ValueError as exc:
        static_points = []
        static_availability = {"available": False, "reason": str(exc)}

    public_points, public_error = await _miqueias_public_points(session_date)
    if public_error:
        public_availability = {"available": False, "reason": public_error}
    elif not public_points:
        public_availability = {
            "available": False,
            "reason": f"série pública indisponível para a sessão {session_date}",
        }
    else:
        public_availability = {"available": True}

    result = {
        "session_date": session_date,
        "target": target,
        "is_b3": bool(v2.get("is_b3", False)),
        "brt_offset_h": v2.get("brt_offset_h", 0),
        "series": {
            "miqueias_public": public_points,
            "v1": _comparison_points(v1.get("series", [])),
            "v2": _comparison_points(v2_rows),
            "miqueias_static": static_points,
        },
        "availability": {
            "miqueias_public": public_availability,
            "v1": {"available": True},
            "v2": {"available": True},
            "miqueias_static": static_availability,
        },
    }
    p_dynamic_comparison_cache[cache_key] = result
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
