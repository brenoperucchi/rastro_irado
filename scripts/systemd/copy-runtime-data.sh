#!/usr/bin/env bash
# Copy runtime state only after systemd writers and timers are explicitly down.
# This initial-copy command refuses to overwrite an existing runtime database.
# Frontend dependencies are provisioned separately from the pinned lockfile.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-common.sh
source "${SCRIPT_DIR}/runtime-common.sh"
# shellcheck source=runtime-data.sh
source "${SCRIPT_DIR}/runtime-data.sh"

usage() {
    cat <<'EOF'
Usage: copy-runtime-data.sh --source-root PATH --runtime-root PATH [--apply]

Checkpoint the source SQLite WAL and stage a manifest-verified copy of data/
into a newly created runtime clone. The command is dry-run by default and
refuses to run while any IRAI writer or timer is active.
EOF
}

source_root=""
runtime_root=""
apply=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source-root)
            source_root="${2:-}"
            shift 2
            ;;
        --runtime-root)
            runtime_root="${2:-}"
            shift 2
            ;;
        --apply)
            apply=1
            shift
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

[[ -n "$source_root" && -n "$runtime_root" ]] || {
    usage >&2
    runtime_die "--source-root e --runtime-root são obrigatórios"
}

source_root="$(runtime_require_drvfs_root "$source_root")" || exit 1
runtime_root="$(runtime_require_drvfs_root "$runtime_root")" || exit 1
runtime_assert_independent_checkout "$runtime_root" "$source_root" || exit 1

source_db="${source_root}/data/irai.db"
runtime_db="${runtime_root}/data/irai.db"
[[ -f "$source_db" ]] || runtime_die "banco de origem ausente: $source_db"
[[ ! -e "$runtime_db" ]] || \
    runtime_die "runtime já possui irai.db; cópia inicial recusa sobrescrever histórico"

runtime_assert_tracked_data_matches_runtime_ref "$source_root" "$runtime_root" || exit 1
runtime_require_all_units_inactive || exit 1

if [[ "$apply" -eq 0 ]]; then
    printf 'would checkpoint %s, stage+verify %s/data, and replace runtime data\n' \
        "$source_root" "$runtime_root"
    exit 0
fi

runtime_checkpoint_wal "$source_db" || exit 1
stage_dir="$(runtime_stage_verified_data_copy "${source_root}/data" "$(dirname -- "$runtime_root")")" || exit 1
trap 'rm -rf -- "$stage_dir"' EXIT

# A clone limpa contém alguns artefatos data/ rastreados. Eles só saem da
# frente depois de existir uma cópia staged, manifestada e íntegra.
runtime_swap_staged_data "$stage_dir" "${runtime_root}/data" "${stage_dir}/runtime-data-before" || exit 1
rm -rf -- "${stage_dir}/runtime-data-before"
runtime_assert_data_inside_root "$runtime_root" || exit 1
runtime_verify_sqlite_integrity "$runtime_db" || exit 1

printf 'copied and verified runtime data from %s to %s\n' "$source_root" "$runtime_root"
