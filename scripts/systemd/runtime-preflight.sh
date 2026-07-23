#!/usr/bin/env bash
# Validate a staged IRAI runtime checkout before cutover, and optionally prove
# that the started API is serving the exact revision calculated from that disk.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-common.sh
source "${SCRIPT_DIR}/runtime-common.sh"

usage() {
    cat <<'EOF'
Usage: runtime-preflight.sh --runtime-root PATH --development-root PATH --expected-ref REF [options]

Options:
  --unit-dir PATH  Installed unit directory (default: ~/.config/systemd/user)
  --api-url URL    After API startup, compare its frozen runtime revision to
                   the revision calculated from the staged runtime disk.
  -h, --help       Show this help.
EOF
}

runtime_root=""
development_root=""
expected_ref=""
unit_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
api_url=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --runtime-root)
            runtime_root="${2:-}"
            shift 2
            ;;
        --development-root)
            development_root="${2:-}"
            shift 2
            ;;
        --expected-ref)
            expected_ref="${2:-}"
            shift 2
            ;;
        --unit-dir)
            unit_dir="${2:-}"
            shift 2
            ;;
        --api-url)
            api_url="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            runtime_die "argumento desconhecido: $1"
            ;;
    esac
done

[[ -n "$runtime_root" && -n "$development_root" && -n "$expected_ref" ]] || {
    usage >&2
    runtime_die "--runtime-root, --development-root e --expected-ref são obrigatórios"
}

runtime_root="$(runtime_require_drvfs_root "$runtime_root")" || exit 1
development_root="$(runtime_require_drvfs_root "$development_root")" || exit 1
expected_ref="$(runtime_require_full_commit_sha "$expected_ref")" || exit 1
runtime_assert_independent_checkout "$runtime_root" "$development_root" || exit 1
runtime_assert_data_inside_root "$runtime_root" || exit 1
runtime_assert_git_clean "$runtime_root" || exit 1
runtime_assert_detached_head "$runtime_root" || exit 1
runtime_require_command python3
runtime_require_command git

runtime_origin="$(runtime_origin_url "$runtime_root")" || exit 1
development_origin="$(runtime_origin_url "$development_root")" || exit 1
[[ "$runtime_origin" == "$development_origin" ]] || \
    runtime_die "origin do runtime diverge do origin de desenvolvimento: $runtime_origin != $development_origin"

actual_ref="$(git -C "$runtime_root" rev-parse HEAD)"
pinned_ref="$(git -C "$runtime_root" rev-parse --verify "${expected_ref}^{commit}")" || \
    runtime_die "ref esperada não existe no runtime: $expected_ref"
[[ "$actual_ref" == "$pinned_ref" ]] || \
    runtime_die "HEAD do runtime diverge da ref esperada: $actual_ref != $pinned_ref"

for unit_name in "${runtime_all_units[@]}"; do
    unit_path="${unit_dir}/${unit_name}"
    [[ -f "$unit_path" && ! -L "$unit_path" ]] || \
        runtime_die "unit precisa ser arquivo real: $unit_path"
    if [[ "$unit_name" == *.service ]]; then
        grep -Fqx "WorkingDirectory=${runtime_root}" "$unit_path" || \
            runtime_die "WorkingDirectory divergente em $unit_path"
    fi
done

db_path="${runtime_root}/data/irai.db"
[[ -f "$db_path" ]] || runtime_die "banco de runtime ausente: $db_path"
"${SCRIPT_DIR}/provision-runtime-frontend.sh" --runtime-root "$runtime_root" --verify

# Before API startup the cutover has already checkpointed every writer, so an
# immutable read proves the copied database without creating sidecar files.
# Once --api-url is supplied, API writes can live only in the WAL; immutable
# would intentionally ignore them and could validate a stale main database.
sqlite_integrity_scope="quiescent-immutable"
sqlite_uri_query="mode=ro&immutable=1"
if [[ -n "$api_url" ]]; then
    sqlite_integrity_scope="live-wal-aware"
    sqlite_uri_query="mode=ro"
fi

python3 - "$db_path" "$sqlite_uri_query" <<'PY'
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1]).resolve()
connection = sqlite3.connect(f"{path.as_uri()}?{sys.argv[2]}", uri=True, timeout=10)
try:
    row = connection.execute("PRAGMA integrity_check").fetchone()
finally:
    connection.close()
if row != ("ok",):
    raise SystemExit(f"integrity_check falhou para {path}: {row!r}")
PY

disk_revision="$(
    (
    cd "$runtime_root"
    PYTHONPATH="$runtime_root" python3 - "$runtime_root" "$db_path" <<'PY'
import json
import sys
from pathlib import Path

from backend.irai.runtime_revision import build_engine_revision

revision = build_engine_revision(root=Path(sys.argv[1]), db_path=Path(sys.argv[2]))
print(json.dumps(revision, sort_keys=True, separators=(",", ":")))
PY
    )
)"

printf 'runtime_root=%s\n' "$runtime_root"
printf 'runtime_ref=%s\n' "$actual_ref"
printf 'sqlite_integrity_scope=%s\n' "$sqlite_integrity_scope"
printf 'runtime_engine_revision=%s\n' "$disk_revision"

if [[ -n "$api_url" ]]; then
    runtime_require_command curl
    api_payload="$(curl --fail --silent --show-error "${api_url%/}/api/internal/p-dynamic-runtime-revision")" || \
        runtime_die "API não retornou revisão de runtime: $api_url"
    DISK_REVISION="$disk_revision" API_PAYLOAD="$api_payload" python3 - <<'PY'
import json
import os

expected = json.loads(os.environ["DISK_REVISION"])
payload = json.loads(os.environ["API_PAYLOAD"])
actual = payload.get("engine_revision")
if actual != expected:
    raise SystemExit(
        "revisão da API não corresponde ao disco do runtime: "
        f"expected={expected!r} actual={actual!r}"
    )
PY
    printf 'api_runtime_revision=matches_disk\n'
fi
