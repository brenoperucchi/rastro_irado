# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

IRAI (Intraday Risk Appetite Index) is a personal cross-asset trading-support dashboard. It estimates
directional probability (`P_up`, 0–100%) for 20 global assets (indices, forex, commodities, crypto, plus
two Brazilian futures) using a Ridge-regularized regression over cross-asset factors, recalculated every
5-minute bar during the session. It is a decision-support tool, not an automated execution system — see
`.planning/PROJECT.md` for the "Out of Scope" list (no auto order execution, no multi-user/SaaS).

Primary docs (read these before making non-trivial changes):
- `.planning/PROJECT.md` — requirements, key architectural decisions and their rationale, current model coverage
- `.planning/docs/SPEC.md` / `.planning/docs/PRD.md` — architecture, schema, algorithm, API contract
- `.planning/docs/FACTOR_MAP.md` — generated table of factors/weights/accuracy per asset (regenerate, don't hand-edit)
- `.planning/docs/TIMEZONE_ARCHITECTURE.md` — **read before touching any timestamp/session logic** (see Timezones below)
- `.planning/ROADMAP.md` — completed work log and backlog
- `docs/adr/` — architecture decision records (e.g. calibration factor-count constraints)

This is a Windows-only runtime (MetaTrader5 Python lib requires Windows), developed here on Linux for
code editing. Live collector/services cannot actually run in this environment.

## Commands

### Backend (Python 3.11+)
No `requirements.txt` exists — dependencies (`MetaTrader5`, `fastapi`, `uvicorn`, `numpy`, `pandas`,
`scikit-learn`, `scipy`, `statsmodels`, `pykalman`) are installed ad hoc into the environment.

```bash
# Run the API (port 8888)
python -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8888 --reload

# Run the collector worker (polls 3 MT5 terminals; Windows-only, needs live MT5 installs)
python backend/workers/collector.py --interval 60 --force   # --force skips B3-hours gating
python backend/workers/collector.py --once                  # single cycle, for debugging

# Init / migrate the SQLite schema
python backend/db.py

# Recalibrate all models (Ridge brute-force, min 6 / max 8 factors per basket)
python -X utf8 scripts/calibrate_universal.py --all --force
python -X utf8 scripts/calibrate_universal.py --target US500   # single asset

# Forex majors excluding DXY (mandatory — DXY is derived from these pairs, would be circular)
python scripts/calibrate_majors_nodxy.py

# Regenerate FACTOR_MAP.md after any calibration
python -X utf8 scripts/generate_factor_map.py

# Push current API state to Firebase (mobile hosting sync, runs every 30s in production)
python scripts/firebase_sync.py
```

Maintained regression tests live in `tests/` and can be run with `pytest`. Start with the narrowest
relevant test module, then run the broader maintained suite in proportion to the change. Some tests and
historical scripts require optional Windows/MT5 or scientific dependencies, so record explicit skips and
never treat `scripts/archive/` or `scripts/explorations/` as living regression coverage.

### Frontend (Node 18+)
```bash
cd frontend
npm run dev       # Vite dev server, fixed port 5175 (strictPort — fails instead of jumping ports)
npm run build
npm run lint       # eslint . — flat config, react-hooks + react-refresh rules
npm run preview
```

### Everything at once (Windows only)
```cmd
start_irai.bat
```
Launches API (8888), collector (`--interval 60 --force`), frontend dev server (5175), and Firebase sync in
separate windows. Production deployment uses NSSM Windows services instead (`scripts/install_nssm_services.ps1`
installs `IRAI_API`, `IRAI_Collector`, `IRAI_FirebaseSync`).

## Architecture

```
MT5 XP (BR futures)     -+                                   +-> Firebase Realtime DB
MT5 Tickmill (global)   --+-> collector.py -> SQLite -> FastAPI :8888 --+-> firebase_sync.py (30s)
MT5 Axi (iShares)       -+                                              v
                                                                React frontend (Firebase Hosting)
```

- **`backend/workers/collector.py`** — connects to 3 MT5 terminals *sequentially* (the MT5 Python lib only
  supports one connection per process, so it's `mt5.shutdown()` → `mt5.initialize()` per terminal each
  cycle): XP (`WIN$N`, `WDO$N`, `DI1$N`, BR session-gated), Tickmill (~23 global symbols, 24h), Axi (6
  iShares ETFs used only as calibration factors, never shown on the dashboard). Writes to `market_bars`
  with `INSERT OR REPLACE` for the in-formation bar and `INSERT OR IGNORE` for closed bars, then POSTs to
  `/api/internal/notify_update` to invalidate API caches and wake the WebSocket broadcast loop.
- **`backend/db.py`** — SQLite (WAL mode) schema owner. Key tables: `market_bars` (raw OHLCV per
  symbol/source/timeframe), `asset_models` (one row per target: factors JSON, session hours, latest
  accuracy/R²), `model_params` (versioned weights, keyed `{slug}_w_{factor}` / `sigma_{factor}` etc.),
  `kalman_state` (persisted Kalman mean/covariance + Johansen p-value per slug, so the V2 engine resumes
  causally across restarts instead of re-initializing), `calibration_log`, `session_opens`.
- **`backend/irai/engine.py`** (`IRAIEngine`) — the calculation core. Loads calibrated params from
  `asset_models`/`model_params` on init, then `compute_from_db(session_date, target, version)` walks the
  session bar-by-bar computing z-scores, factor contributions, and `P_up` via logistic sigmoid over a
  linear score. Two versions coexist:
  - **v1**: static OLS/z-score model.
  - **v2**: dynamic engine — `backend/irai/kalman.py` (`KalmanFilterWrapper`, causal-only via
    `filter_update`, no lookahead) computes a time-varying hedge ratio per bar, `backend/irai/johansen.py`
    (`check_cointegration`) gates signal validity by testing basket cointegration. A per-asset
    `use_johansen` flag exists because the cointegration filter helps mean-reversion assets (WDO$N, XAUUSD)
    but destroys PnL for momentum assets (WIN$N, BTCUSD) — see `docs/adr/ADR-001-*.md` and
    `.planning/ROADMAP.md` "Ablation Johansen" for why this is a per-asset toggle, not global.
- **`backend/api/main.py`** — FastAPI app, single `IRAIEngine` instance in `lifespan`. Two caches keyed by
  `(target, date, version)` / `(date, version)` (`series_cache`, `overview_cache_data`), cleared only by
  `/api/internal/notify_update` — **any new endpoint reading fresh data must go through the engine, not
  assume the cache is warm/cold correctly**. WebSocket (`/ws/irai`) exists for push updates but the
  frontend now defaults to HTTP polling (see Key Decisions below) — don't assume the WS path is exercised
  in practice. `/api/irai/overview` also derives `price_diverges` (return vs. `P_up` divergence z-score)
  and NWE slope (Nadaraya-Watson kernel over `win_return`) per target.
- **`frontend/src/App.jsx`** — single-file React dashboard (no router; `page` state toggles overview vs.
  detail view). Polls the API every 30s (`REFRESH_INTERVAL`) and renders the migrated charts with
  `lightweight-charts`. It retains dual-axis time-label handling (`toLocalTime`) for B3 assets.

## Timezones — read before touching timestamps

Two brokers, two server clocks, stored as naive UTC-looking strings in `market_bars`: **Tickmill is EEST
(UTC+3)**, **XP/B3 is BRT (UTC-3)** — a 6-hour gap. `engine.py` normalizes onto the **Tickmill (EEST)
axis**: when loading bars for a B3 target (`session_start_h != 0`), it shifts timestamps `+6h`
(`ts_dt += timedelta(hours=6)`) so `09:00 BRT` aligns with `15:00 EEST` in `all_timestamps`. Every JSON
response coming out of the API is therefore in EEST. The frontend reconstructs the BRT axis for B3 assets
by subtracting 6h again (`toLocalTime(timeTickmill, -6)`) for the secondary/amber x-axis. Before the B3
open, the engine pads the timeline with synthetic "ghost bars" and forces `win_return = 0.0` so the UI
shows a flat line instead of a false drift from yesterday's close. Getting this wrong reintroduces the bug
fixed in the `ca5adf7` commit (6-hour data misalignment + ghost bars extending across the whole pre-market
window) — if you change anything in the bar-loading/alignment path, verify visually against both axes,
not just against raw DB values.

## Calibration model

`scripts/calibrate_universal.py` runs a brute-force search over `ALL_FACTORS` (24 traditional symbols + 6
iShares ETFs), enforcing **min 6 / max 8 factors per basket** (raised from 4 after ADR-001 showed 4-factor
baskets overfit and produced weak R² on cross pairs) and a mixed score of 70% directional accuracy + 30%
R². Structural exclusion rules that must be preserved:
- BR assets (WIN$N/WDO$N) never mix factors with the international basket in the same way indices do —
  domestic/international separation is deliberate.
- US indices don't use DXY as a factor.
- Forex majors never use DXY (`DXY_COMPONENTS` set) — DXY is derived from those same pairs, so including it
  is circular/multicollinear.
- EWZ (`iSharesBrazil+`) is excluded for BR targets (same-market tautology); max 1 Treasury ETF and 1 EM
  bond ETF per basket (anti-multicollinearity).

After any calibration run, regenerate `.planning/docs/FACTOR_MAP.md` via `scripts/generate_factor_map.py`
— it's derived from `asset_models`/`model_params`, don't hand-edit it.

## Notable prior decisions (don't relitigate without cause)

See `.planning/PROJECT.md` "Key Decisions" for the full table with rationale. The ones most likely to trip
up a future change:
- **HTTP polling, not WebSocket**, for the frontend refresh loop — WebSocket caused flickering/race
  conditions and recompute overhead on broadcast; polling + server-side cache is deliberately preferred
  even though the WS endpoint still exists.
- **NWE replaced Cumulative Delta** on the dashboard — Cumulative Delta was noisy/low-signal.
- Session hours: global (Tickmill) assets run 24h; B3 (WIN$N/WDO$N) is gated to 09:00–18:00 BRT.

## Task management with Backlog.md

- The Markdown plans in `docs/plans/` remain authoritative for product scope, business rules, priority,
  and execution sequence. Backlog.md is the operational task board.
- Use the shared `backlog` MCP server or the `backlog` CLI; do not edit `backlog/tasks/` manually.
- Before implementation, read the task, dependencies, acceptance criteria, and
  `backlog://docs/task-workflow` when available.
- Set tasks to `In Progress` when starting, `Review` when ready for independent review, and `Done` only
  after acceptance criteria and validation pass.
- Store implementation notes, modified files, commands run, and final summaries in the task. Update the
  authoritative plan before closing any task that changes roadmap status or business rules.
