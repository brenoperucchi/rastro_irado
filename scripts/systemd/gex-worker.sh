#!/usr/bin/env bash
# Roda o gex_worker (gamma walls EOD) uma vez. O MT5 só aceita uma conexão por
# terminal/processo, então paramos o collector durante a execução e religamos
# ao final (mesmo se o worker falhar).
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="${IRAI_PYTHON:-/mnt/c/Users/brenoperucchi/AppData/Local/Microsoft/WindowsApps/py.exe}"
PYTHON_VERSION_FLAG="${IRAI_PYTHON_VERSION_FLAG:--3.12}"

cd "${PROJECT_DIR}"
export PYTHONPATH="${PROJECT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

systemctl --user stop rastro-irado-collector || true
sleep 2

"${PYTHON_BIN}" ${PYTHON_VERSION_FLAG} -X utf8 backend/workers/gex_worker.py "$@"
rc=$?

systemctl --user start rastro-irado-collector || true
exit $rc
