#!/usr/bin/env bash
# Capture the code/data rollback state of a stopped runtime before an explicit
# ref update. Unit fragments must already have been captured by
# snapshot-runtime-units.sh while they were still loaded.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-common.sh
source "${SCRIPT_DIR}/runtime-common.sh"
# shellcheck source=runtime-data.sh
source "${SCRIPT_DIR}/runtime-data.sh"

usage() {
    cat <<'EOF'
Usage: snapshot-runtime-state.sh --runtime-root PATH --state-dir PATH [--apply]

Capture the detached runtime SHA and a verified data/ copy before an update.
state-dir must already contain units/ created by snapshot-runtime-units.sh.
The command is dry-run by default and requires all runtime units inactive.
EOF
}

runtime_root=""
state_dir=""
apply=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --runtime-root)
            runtime_root="${2:-}"
            shift 2
            ;;
        --state-dir)
            state_dir="${2:-}"
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

[[ -n "$runtime_root" && -n "$state_dir" ]] || {
    usage >&2
    runtime_die "--runtime-root e --state-dir são obrigatórios"
}

runtime_root="$(runtime_require_drvfs_root "$runtime_root")" || exit 1
runtime_assert_script_dir_inside_root "$SCRIPT_DIR" "$runtime_root" || exit 1
state_parent="$(runtime_require_drvfs_root "$(dirname -- "$state_dir")")" || exit 1
state_dir="${state_parent}/$(basename -- "$state_dir")"
state_dir="$(runtime_assert_state_dir_outside_runtime "$runtime_root" "$state_dir")" || exit 1
[[ ! -e "${state_dir}/data" && ! -e "${state_dir}/old-commit" && ! -e "${state_dir}/rollback-bin" ]] || \
    runtime_die "state-dir já contém snapshot de estado: $state_dir"
runtime_assert_materialized_unit_backup "${state_dir}/units" || exit 1

runtime_assert_git_clean "$runtime_root" || exit 1
runtime_assert_detached_head "$runtime_root" || exit 1
runtime_require_all_units_inactive || exit 1
db_path="${runtime_root}/data/irai.db"

if [[ "$apply" -eq 0 ]]; then
    printf 'would checkpoint and snapshot %s/data into %s\n' "$runtime_root" "$state_dir"
    exit 0
fi

runtime_capture_rollback_bootstrap "$SCRIPT_DIR" "$state_dir" || exit 1
runtime_checkpoint_wal "$db_path" || exit 1
stage_dir="$(runtime_stage_verified_data_copy "${runtime_root}/data" "$state_parent")" || exit 1
trap 'rm -rf -- "$stage_dir"' EXIT
mv -- "${stage_dir}/data" "${state_dir}/data"
install -m 0644 -- "${stage_dir}/source-before.sha256" "${state_dir}/data-before.sha256"
install -m 0644 -- "${stage_dir}/target.sha256" "${state_dir}/data-snapshot.sha256"
cmp -s "${state_dir}/data-before.sha256" "${state_dir}/data-snapshot.sha256" || \
    runtime_die "manifest do snapshot de data diverge da origem"
git -C "$runtime_root" rev-parse HEAD > "${state_dir}/old-commit"
runtime_origin_url "$runtime_root" > "${state_dir}/origin-url"
printf 'snapshot created for %s in %s\n' "$(cat "${state_dir}/old-commit")" "$state_dir"
