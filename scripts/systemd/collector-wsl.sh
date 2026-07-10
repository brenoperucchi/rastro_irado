#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="${IRAI_PYTHON:-/mnt/c/Users/brenoperucchi/AppData/Local/Microsoft/WindowsApps/py.exe}"
# py.exe default aponta pro Python mais novo instalado (hoje 3.14), sem os
# pacotes de dados (numpy/pandas/pykalman) — precisa fixar 3.12.
PYTHON_VERSION_FLAG="${IRAI_PYTHON_VERSION_FLAG:--3.12}"

cd "${PROJECT_DIR}"
export PYTHONPATH="${PROJECT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

exec "${PYTHON_BIN}" ${PYTHON_VERSION_FLAG} -X utf8 backend/workers/collector_wsl.py \
    --interval "${IRAI_COLLECTOR_INTERVAL:-60}"
