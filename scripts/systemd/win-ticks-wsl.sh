#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="${IRAI_TICKS_PYTHON:-/mnt/c/Users/brenoperucchi/AppData/Local/Microsoft/WindowsApps/py.exe}"
PYTHON_VERSION_FLAG="${IRAI_TICKS_PYTHON_VERSION_FLAG:--3.12}"
POWERSHELL_BIN="${IRAI_TICKS_POWERSHELL:-/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe}"
TERMINAL_PATH="${IRAI_TICKS_TERMINAL:-E:/MetaTradersWSL/wdowin/ira_ticks/terminal64.exe}"
OUTPUT_ROOT="${IRAI_TICKS_OUTPUT_ROOT:-data/ticks/win}"
LAUNCHER_WIN="$(wslpath -w "${SCRIPT_DIR}/start-mt5-portable.ps1")"

cd "${PROJECT_DIR}"
export PYTHONPATH="${PROJECT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

# Invariante IRAI-20: o Python nunca deve ser o responsável por abrir uma
# instância comum do terminal. Primeiro abrimos explicitamente o data directory
# dedicado com /portable; depois o initialize() apenas conecta e valida data_path.
"${POWERSHELL_BIN}" -NoProfile -NonInteractive -ExecutionPolicy Bypass \
    -File "${LAUNCHER_WIN}" -TerminalPath "${TERMINAL_PATH}"
sleep "${IRAI_TICKS_TERMINAL_WAIT_SECONDS:-8}"

exec "${PYTHON_BIN}" ${PYTHON_VERSION_FLAG} -X utf8 backend/workers/tick_collector_wsl.py \
    --terminal "${TERMINAL_PATH}" \
    --output-root "${OUTPUT_ROOT}" \
    --poll-seconds "${IRAI_TICKS_POLL_SECONDS:-2}" \
    --initial-backfill-minutes "${IRAI_TICKS_INITIAL_BACKFILL_MINUTES:-15}"

