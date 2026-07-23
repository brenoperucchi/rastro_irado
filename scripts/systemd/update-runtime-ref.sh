#!/usr/bin/env bash
# Update only a stopped runtime checkout to an explicit Git ref. The caller
# must first snapshot units and data into --state-dir, so a failed API/preflight
# can restore the old code and the pre-migration SQLite state together.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-common.sh
source "${SCRIPT_DIR}/runtime-common.sh"
# shellcheck source=runtime-data.sh
source "${SCRIPT_DIR}/runtime-data.sh"

usage() {
    cat <<'EOF'
Usage: update-runtime-ref.sh --runtime-root PATH --state-dir PATH --ref REF [--fetch] [--apply]

The runtime must be clean and every IRAI service/timer must be stopped.
state-dir must contain snapshot-runtime-units.sh + snapshot-runtime-state.sh
output for the current detached SHA. The command is dry-run by default;
--fetch fetches remotes before resolving REF.
EOF
}

runtime_root=""
state_dir=""
ref=""
fetch=0
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
        --ref)
            ref="${2:-}"
            shift 2
            ;;
        --fetch)
            fetch=1
            shift
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

[[ -n "$runtime_root" && -n "$ref" ]] || {
    usage >&2
    runtime_die "--runtime-root e --ref são obrigatórios"
}
[[ "$fetch" -eq 0 || "$apply" -eq 1 ]] || \
    runtime_die "--fetch altera o clone e exige --apply"
ref="$(runtime_require_full_commit_sha "$ref")" || exit 1
[[ -n "$state_dir" ]] || {
    usage >&2
    runtime_die "--state-dir é obrigatório"
}

runtime_root="$(runtime_require_drvfs_root "$runtime_root")" || exit 1
state_dir="$(runtime_require_drvfs_root "$state_dir")" || exit 1
state_dir="$(runtime_assert_state_dir_outside_runtime "$runtime_root" "$state_dir")" || exit 1
runtime_assert_git_clean "$runtime_root" || exit 1
runtime_assert_detached_head "$runtime_root" || exit 1
runtime_origin="$(runtime_origin_url "$runtime_root")" || exit 1
[[ -f "${state_dir}/old-commit" && -f "${state_dir}/origin-url" && -f "${state_dir}/data-before.sha256" && -d "${state_dir}/data" ]] || \
    runtime_die "state-dir não contém snapshot completo: $state_dir"
runtime_assert_materialized_unit_backup "${state_dir}/units" || exit 1
runtime_assert_rollback_bootstrap "$state_dir" || exit 1
[[ "$(tr -d '\r\n' < "${state_dir}/origin-url")" == "$runtime_origin" ]] || \
    runtime_die "origin do runtime diverge do snapshot de estado"
old_commit="$(runtime_require_full_commit_sha "$(tr -d '\r\n' < "${state_dir}/old-commit")")" || exit 1
[[ "$(git -C "$runtime_root" rev-parse HEAD)" == "$old_commit" ]] || \
    runtime_die "snapshot de estado não corresponde ao HEAD atual do runtime"
current_manifest="$(mktemp)" || runtime_die "não foi possível criar manifest temporário"
trap 'rm -f -- "$current_manifest"' EXIT
runtime_write_data_manifest "${runtime_root}/data" "$current_manifest"
cmp -s "${state_dir}/data-before.sha256" "$current_manifest" || \
    runtime_die "data/ do runtime diverge do snapshot; recrie o snapshot antes de atualizar"
runtime_write_data_manifest "${state_dir}/data" "${current_manifest}"
cmp -s "${state_dir}/data-before.sha256" "$current_manifest" || \
    runtime_die "backup de data diverge do manifesto; recrie o snapshot antes de atualizar"
runtime_verify_sqlite_integrity "${state_dir}/data/irai.db" || exit 1
runtime_verify_sqlite_integrity "${runtime_root}/data/irai.db" || exit 1
runtime_require_all_units_inactive || exit 1
[[ ! -e "${state_dir}/target-commit" && ! -e "${state_dir}/failed-commit" ]] || \
    runtime_die "state-dir já foi usado por uma atualização; crie um snapshot novo: $state_dir"

if [[ "$fetch" -eq 1 ]]; then
    git -C "$runtime_root" fetch --all --tags --prune
fi

commit="$(git -C "$runtime_root" rev-parse --verify "${ref}^{commit}")" || \
    runtime_die "ref não encontrada no checkout runtime: $ref"

if [[ "$apply" -eq 0 ]]; then
    printf 'would check out detached %s in %s\n' "$commit" "$runtime_root"
    exit 0
fi

git -C "$runtime_root" checkout --detach "$commit"
runtime_assert_git_clean "$runtime_root" || exit 1
runtime_assert_detached_head "$runtime_root" || exit 1
printf '%s\n' "$commit" > "${state_dir}/target-commit"
printf 'runtime now pinned to %s\n' "$commit"
