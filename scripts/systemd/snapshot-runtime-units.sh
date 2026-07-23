#!/usr/bin/env bash
# Snapshot the live IRAI user-unit definitions before a runtime cutover. This
# includes transient units, materialized as regular files for deterministic
# rollback after they are stopped and collected.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-common.sh
source "${SCRIPT_DIR}/runtime-common.sh"

usage() {
    cat <<'EOF'
Usage: snapshot-runtime-units.sh --backup-dir PATH [--apply]

Copy every currently loaded IRAI user-unit fragment into PATH as a regular
file, including a transient frontend definition. The command is dry-run by
default. Run it before stopping any unit for a runtime cutover.
EOF
}

backup_dir=""
apply=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup-dir)
            backup_dir="${2:-}"
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

[[ -n "$backup_dir" ]] || {
    usage >&2
    runtime_die "--backup-dir é obrigatório"
}
runtime_require_command systemctl || exit 1

[[ ! -e "$backup_dir" && ! -L "$backup_dir" ]] || \
    runtime_die "diretório de backup já existe: $backup_dir"

declare -a unit_fragments=()
declare -a unit_enabled_states=()
for unit_name in "${runtime_all_units[@]}"; do
    fragment="$(
        systemctl --user show --property=FragmentPath --value "$unit_name" 2>/dev/null || true
    )"
    [[ -n "$fragment" && -f "$fragment" ]] || \
        runtime_die "unit carregada sem FragmentPath legível: $unit_name ($fragment)"
    drop_ins="$(
        systemctl --user show --property=DropInPaths --value "$unit_name" 2>/dev/null || true
    )"
    [[ -z "$drop_ins" ]] || \
        runtime_die "unit possui drop-in não capturado; revise antes do corte: $unit_name ($drop_ins)"
    unit_fragments+=("${unit_name}:${fragment}")
    unit_enabled_states+=("${unit_name}:$(systemctl --user is-enabled "$unit_name" 2>/dev/null || true)")
done

if [[ "$apply" -eq 0 ]]; then
    printf 'would snapshot %d loaded IRAI units into %s\n' "${#runtime_all_units[@]}" "$backup_dir"
    exit 0
fi

mkdir -p -- "$backup_dir"
for entry in "${unit_fragments[@]}"; do
    unit_name="${entry%%:*}"
    fragment="${entry#*:}"
    install -m 0644 -- "$(realpath -e -- "$fragment")" "${backup_dir}/${unit_name}"
done
{
    printf '# IRAI runtime-unit snapshot; created before cutover.\n'
    printf '# Each fragment above was materialized as a regular file.\n'
    for state in "${unit_enabled_states[@]}"; do
        printf '%s\n' "$state"
    done
} > "${backup_dir}/enabled-states.txt"

printf 'snapshotted %d loaded IRAI units into %s\n' "${#runtime_all_units[@]}" "$backup_dir"
