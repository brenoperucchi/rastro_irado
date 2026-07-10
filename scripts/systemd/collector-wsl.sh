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

# --force: coleta o B3 em todos os ciclos, sem a barreira is_b3_session() — que
# só libera às 09:55 e faz o collector perder as barras da abertura (09:00–09:55),
# ancorando o win_open no preço errado (~09:35 em vez de 09:00). Alinha com a
# produção, que roda sempre com --force. Para desativar: IRAI_COLLECTOR_FORCE=0.
FORCE_FLAG="--force"
if [ "${IRAI_COLLECTOR_FORCE:-1}" = "0" ]; then
    FORCE_FLAG=""
fi

exec "${PYTHON_BIN}" ${PYTHON_VERSION_FLAG} -X utf8 backend/workers/collector_wsl.py \
    --interval "${IRAI_COLLECTOR_INTERVAL:-60}" \
    ${FORCE_FLAG}
