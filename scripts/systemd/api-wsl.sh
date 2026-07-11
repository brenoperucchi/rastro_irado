#!/usr/bin/env bash
# Launcher da API IRAI (FastAPI/uvicorn) no host WSL, via py.exe do Windows
# (a lib MetaTrader5 exige Windows). Espelha o wrapper do collector.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="${IRAI_PYTHON:-/mnt/c/Users/brenoperucchi/AppData/Local/Microsoft/WindowsApps/py.exe}"
# py.exe default aponta pro Python mais novo instalado (sem numpy/pandas/pykalman)
# — precisa fixar 3.12.
PYTHON_VERSION_FLAG="${IRAI_PYTHON_VERSION_FLAG:--3.12}"

cd "${PROJECT_DIR}"
export PYTHONPATH="${PROJECT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

exec "${PYTHON_BIN}" ${PYTHON_VERSION_FLAG} -X utf8 -m uvicorn backend.api.main:app \
    --host 0.0.0.0 --port "${IRAI_API_PORT:-8888}"
