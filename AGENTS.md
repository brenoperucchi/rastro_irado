# AGENTS.md

## Engineering pipeline

- For feature implementation, bug fixes, material refactors, runtime configuration changes, and code
  reviews, use the `engineering-pipeline` skill.
- Let that skill control risk classification, model/reasoning escalation, independent review, and
  bounded remediation.
- Do not use the pipeline for explanation, status, or investigation-only requests unless the user
  explicitly asks for it.
- Never substitute a missing configured pipeline agent silently; report the configuration failure.

## Bug-fix workflow

- Reproduce reported bugs with a permanent regression test whenever feasible.
- The test must describe the expected behavior, fail before the fix, and pass only after the fix.
- Keep the regression in the maintained suite unless the user explicitly requested investigation only.
- In the final handoff, report the regression that failed, the code changed, and all validation commands.

## Project overview

IRAI (Intraday Risk Appetite Index) is a personal cross-asset trading-support dashboard. It estimates
directional probability (`P_up`, 0–100%) for 20 global assets every five minutes with Ridge-regularized
cross-asset models. It supports trading decisions; it does not execute orders and is not a multi-user
or SaaS product.

The production runtime is Windows-only because the `MetaTrader5` Python package requires installed MT5
terminals. Linux is suitable for editing, unit tests, frontend checks, and code paths that do not connect
to MT5. Do not claim to have validated live collection from this environment.

## Read before changing code

- Start with `CLAUDE.md` for the full repository guide.
- Read `.planning/PROJECT.md` for requirements, scope, and architectural decisions.
- Read `.planning/docs/SPEC.md` and `.planning/docs/PRD.md` for architecture, schema, algorithms, and API contracts.
- Read `.planning/docs/TIMEZONE_ARCHITECTURE.md` before changing timestamps, sessions, bar alignment, or chart axes.
- Read `docs/adr/` before revisiting recorded architectural choices.
- Treat `.planning/docs/FACTOR_MAP.md` as generated output; regenerate it instead of editing it manually.

## Repository map

- `backend/api/main.py`: FastAPI application, HTTP endpoints, caches, and WebSocket support.
- `backend/db.py`: SQLite schema and migrations; the database runs in WAL mode.
- `backend/irai/engine.py`: core v1/v2 IRAI calculation and bar alignment.
- `backend/irai/kalman.py`: causal Kalman updates; do not introduce lookahead.
- `backend/irai/johansen.py`: cointegration gate, controlled per asset by `use_johansen`.
- `backend/workers/collector.py`: sequential XP, Tickmill, and Axi MT5 collection.
- `frontend/src/App.jsx`: single-page React dashboard and detail view.
- `frontend/src/Overview.jsx`: overview UI.
- `scripts/`: calibration, synchronization, maintenance, and validation tooling.
- `scripts/archive/` and `scripts/explorations/`: historical or one-off tools, not maintained production code.
- `tests/`: lightweight Python regression tests.

## Common commands

Run commands from the repository root unless noted otherwise.

```bash
# Existing Python regression test (also compatible with pytest)
python3 tests/test_zscore.py
pytest tests/test_zscore.py

# Initialize or migrate the SQLite schema
python backend/db.py

# API (port 8888)
python -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8888 --reload

# Recalibrate models and regenerate the derived factor map
python -X utf8 scripts/calibrate_universal.py --all --force
python -X utf8 scripts/calibrate_universal.py --target US500
python -X utf8 scripts/generate_factor_map.py

# Frontend
cd frontend
npm run dev
npm run lint
npm run build
```

There is no pinned Python dependency file. Dependencies are installed ad hoc and include FastAPI,
Uvicorn, NumPy, pandas, scikit-learn, SciPy, statsmodels, pykalman, and MetaTrader5. Do not add or update
dependency manifests incidentally.

Live collector commands are Windows-only and require configured terminals:

```bash
python backend/workers/collector.py --interval 60 --force
python backend/workers/collector.py --once
```

`start_irai.bat` starts the API, collector, frontend, and Firebase sync on Windows. Production uses the
NSSM services installed by `scripts/install_nssm_services.ps1`.

## Architecture invariants

- MT5 supports one connection per process. The collector must connect to XP, Tickmill, and Axi
  sequentially with `shutdown()`/`initialize()` between terminals.
- HTTP polling plus server-side caching is the intended frontend refresh path. Do not switch back to
  WebSocket-driven refresh without a concrete reason and validation against flicker/race regressions.
- API caches are invalidated through `/api/internal/notify_update`. New fresh-data endpoints must use the
  engine and respect cache invalidation rather than relying on incidental cache state.
- V2 Kalman processing is causal. Never use smoothing or future bars in live calculations.
- Johansen filtering is a per-asset decision: it helps mean-reversion assets and hurts some momentum
  assets. Preserve the `use_johansen` toggle.
- Calibration baskets contain 6–8 factors. Preserve exclusions against circularity and
  multicollinearity: forex majors and US indices do not use DXY as described in `CLAUDE.md`; BR targets
  exclude EWZ; use at most one Treasury ETF and one EM bond ETF.
- After calibration, run `scripts/generate_factor_map.py`; never hand-edit the factor map.

## Timezone invariant

Raw SQLite timestamps are naive broker-server times: Tickmill uses EEST (UTC+3), while XP/B3 uses BRT
(UTC-3). The engine aligns B3 target bars to the Tickmill axis by adding six hours. API timestamps are
therefore EEST, and the frontend subtracts six hours for the BRT secondary axis. Pre-market B3 ghost bars
must keep `win_return = 0.0`.

Any change to loading, timestamp conversion, session gating, ghost bars, or axes must be checked against
both a global asset and a B3 asset. Preserve causality and the six-hour alignment documented in
`.planning/docs/TIMEZONE_ARCHITECTURE.md`.

## Change workflow

- Keep changes focused and preserve unrelated user modifications in the worktree.
- For a reported bug, first add a permanent regression test whenever feasible. It must express the
  correct behavior, fail before the fix, and pass afterward.
- For persisted form-data bugs, prefer a request/model/service test for the server-side invariant and add
  a browser/system test when the interaction itself is broken.
- Put maintained tests in `tests/`; do not use archived exploration scripts as proof of correctness.
- Run the narrowest relevant test first, then broader checks proportional to the change.
- Backend changes should run applicable Python tests. Frontend changes should run `npm run lint` and
  `npm run build`. Calibration/model changes should run the relevant backtest or calibration validation.
- In the final handoff for a bug fix, report the regression test that failed before the fix, the code
  changed, and every validation command run after the fix.

## Coding guidance

- Follow the existing local style; avoid broad formatting or cleanup unrelated to the task.
- Keep calculation and persistence logic in the backend rather than duplicating authoritative rules in
  the UI.
- Use parameterized SQL and retain the existing SQLite/WAL behavior.
- Preserve existing API response fields unless the task explicitly calls for a contract change.
- Never commit generated databases, WAL/SHM files, logs, secrets, `.env`, `node_modules`, or Firebase
  local state.

## Task management with Backlog.md

- `docs/plans/2026-07-13-irai-plano-consolidado.md` remains the authority for status, scope, priority,
  and sequence; the Tactical plan remains the normative behavior specification.
- Use the shared `backlog` MCP server or the `backlog` CLI for operational task tracking. Do not edit
  files under `backlog/tasks/` manually.
- Before starting planned work, inspect the relevant task, dependencies, acceptance criteria, and the
  `backlog://docs/task-workflow` MCP resource when available.
- Move the task to `In Progress` when work starts, `Review` when implementation is ready for independent
  review, and `Done` only after its acceptance criteria and relevant validation are complete.
- Record implementation notes, modified files, validation commands, and the final summary in the task.
- If a completed task changes roadmap status or business rules, update the authoritative plan before
  closing the task.
