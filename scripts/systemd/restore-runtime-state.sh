#!/usr/bin/env bash
# Restore an explicit update snapshot after a failed API/preflight before any
# writer resumes. The failed runtime data is retained in state-dir for audit.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-common.sh
source "${SCRIPT_DIR}/runtime-common.sh"
# shellcheck source=runtime-data.sh
source "${SCRIPT_DIR}/runtime-data.sh"

usage() {
    cat <<'EOF'
Usage: restore-runtime-state.sh --runtime-root PATH --state-dir PATH [--unit-dir PATH] --apply

Restore the detached SHA, verified data/ snapshot and materialized user units
captured before an explicit runtime update. All runtime units must be inactive.
This command does not start services.
EOF
}

runtime_root=""
state_dir=""
unit_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
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
        --unit-dir)
            unit_dir="${2:-}"
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

[[ -n "$runtime_root" && -n "$state_dir" && "$apply" -eq 1 ]] || {
    usage >&2
    runtime_die "--runtime-root, --state-dir e --apply são obrigatórios"
}

runtime_root="$(runtime_require_drvfs_root "$runtime_root")" || exit 1
state_dir="$(runtime_require_drvfs_root "$state_dir")" || exit 1
state_dir="$(runtime_assert_state_dir_outside_runtime "$runtime_root" "$state_dir")" || exit 1
[[ -f "${state_dir}/old-commit" && -f "${state_dir}/origin-url" && -f "${state_dir}/data-before.sha256" && -d "${state_dir}/data" ]] || \
    runtime_die "state-dir não contém snapshot completo: $state_dir"
runtime_assert_materialized_unit_backup "${state_dir}/units" || exit 1
runtime_assert_rollback_bootstrap "$state_dir" "$SCRIPT_DIR" || exit 1
old_commit="$(runtime_require_full_commit_sha "$(tr -d '\r\n' < "${state_dir}/old-commit")")" || exit 1
[[ "$(tr -d '\r\n' < "${state_dir}/origin-url")" == "$(runtime_origin_url "$runtime_root")" ]] || \
    runtime_die "origin do runtime diverge do snapshot de estado"
runtime_require_all_units_inactive || exit 1
runtime_write_data_manifest "${state_dir}/data" "${state_dir}/restore-source.sha256"
cmp -s "${state_dir}/data-before.sha256" "${state_dir}/restore-source.sha256" || \
    runtime_die "manifest do snapshot de rollback diverge: ${state_dir}/data"
runtime_verify_sqlite_integrity "${state_dir}/data/irai.db" || exit 1
git -C "$runtime_root" cat-file -e "${old_commit}^{commit}" || \
    runtime_die "commit de rollback não existe no runtime: $old_commit"
runtime_assert_git_clean "$runtime_root" || exit 1

stage_dir="$(runtime_stage_verified_data_copy "${state_dir}/data" "$(dirname -- "$runtime_root")")" || exit 1
trap 'rm -rf -- "$stage_dir"' EXIT
failed_data="${state_dir}/data-after-failed"
[[ ! -e "$failed_data" && ! -L "$failed_data" ]] || \
    runtime_die "state-dir já contém data-after-failed; não sobrescrevendo evidência: $failed_data"
failed_commit="$(git -C "$runtime_root" rev-parse HEAD)"
git -C "$runtime_root" checkout --detach "$old_commit"
runtime_assert_detached_head "$runtime_root" || exit 1
runtime_swap_staged_data "$stage_dir" "${runtime_root}/data" "$failed_data" || exit 1
printf '%s\n' "$failed_commit" > "${state_dir}/failed-commit"
runtime_assert_git_clean "$runtime_root" || exit 1
runtime_write_data_manifest "${runtime_root}/data" "${state_dir}/restore-target.sha256"
cmp -s "${state_dir}/data-before.sha256" "${state_dir}/restore-target.sha256" || \
    runtime_die "manifest de data restaurada diverge do snapshot"
runtime_verify_sqlite_integrity "${runtime_root}/data/irai.db" || exit 1

runtime_restore_materialized_units "${state_dir}/units" "$unit_dir" || exit 1
printf 'restored runtime %s and data snapshot from %s\n' "$old_commit" "$state_dir"
