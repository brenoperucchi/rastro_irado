#!/usr/bin/env bash
# Restore materialized IRAI user-unit fragments and their enabled state. This
# never starts services; callers validate the restored code/data first.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-common.sh
source "${SCRIPT_DIR}/runtime-common.sh"
# shellcheck source=runtime-data.sh
source "${SCRIPT_DIR}/runtime-data.sh"

usage() {
    cat <<'EOF'
Usage: restore-runtime-units.sh --backup-dir PATH [--unit-dir PATH] --apply

Restore unit fragments produced by snapshot-runtime-units.sh. All runtime
services and timers must be inactive. This command does not start them.
EOF
}

backup_dir=""
unit_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
apply=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup-dir)
            backup_dir="${2:-}"
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

[[ -n "$backup_dir" && "$apply" -eq 1 ]] || {
    usage >&2
    runtime_die "--backup-dir e --apply são obrigatórios"
}
runtime_require_all_units_inactive || exit 1
runtime_restore_materialized_units "$backup_dir" "$unit_dir" || exit 1

printf 'restored %d IRAI units from %s\n' "${#runtime_all_units[@]}" "$backup_dir"
