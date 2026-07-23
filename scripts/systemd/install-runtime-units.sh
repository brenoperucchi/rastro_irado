#!/usr/bin/env bash
# Render and materialize the IRAI runtime unit files. This command is dry-run
# by default so preparing a checkout cannot change the current user services.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-common.sh
source "${SCRIPT_DIR}/runtime-common.sh"

usage() {
    cat <<'EOF'
Usage: install-runtime-units.sh --runtime-root PATH [options]

Render the IRAI user units for a distinct NTFS/DrvFs runtime checkout.

Options:
  --runtime-root PATH  Existing runtime checkout on DrvFs/NTFS (required)
  --unit-dir PATH      Destination user-unit directory
                       (default: ~/.config/systemd/user)
  --backup-dir PATH    Copy existing unit files here before replacing them
  --apply              Write regular unit files; requires --daemon-reload.
                       Without this flag, only prints the planned writes.
  --daemon-reload      Reload and verify the units loaded by systemd.
  -h, --help           Show this help.
EOF
}

runtime_root=""
unit_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
backup_dir=""
apply=0
daemon_reload=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --runtime-root)
            runtime_root="${2:-}"
            shift 2
            ;;
        --unit-dir)
            unit_dir="${2:-}"
            shift 2
            ;;
        --backup-dir)
            backup_dir="${2:-}"
            shift 2
            ;;
        --apply)
            apply=1
            shift
            ;;
        --daemon-reload)
            daemon_reload=1
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

[[ -n "$runtime_root" ]] || runtime_die "--runtime-root é obrigatório"
[[ "$daemon_reload" -eq 0 || "$apply" -eq 1 ]] || \
    runtime_die "--daemon-reload exige --apply"
[[ "$apply" -eq 0 || "$daemon_reload" -eq 1 ]] || \
    runtime_die "--apply exige --daemon-reload para validar a unit carregada"

runtime_root="$(runtime_require_drvfs_root "$runtime_root")" || exit 1
[[ -d "$runtime_root/.git" ]] || runtime_die "runtime root não é checkout Git: $runtime_root"

if [[ "$apply" -eq 1 && "$daemon_reload" -eq 1 ]]; then
    runtime_require_command systemctl || exit 1
    # A unit frontend atual nasceu de systemd-run. Enquanto a definição
    # transient estiver carregada, ela tem precedência sobre ~/.config e uma
    # instalação aparentemente bem-sucedida continuaria servindo o checkout
    # de desenvolvimento. O corte deve parar essa unit e deixá-la descarregar
    # antes de materializar a unit persistente.
    loaded_frontend_fragment="$(
        systemctl --user show --property=FragmentPath --value rastro-irado-frontend.service 2>/dev/null || true
    )"
    if [[ "$loaded_frontend_fragment" == /run/user/*/systemd/transient/* ]]; then
        runtime_die "frontend transient ainda carregado: $loaded_frontend_fragment; pare a unit e aguarde descarregar antes da instalação"
    fi
fi

template_dir="${SCRIPT_DIR}/runtime-units"
[[ -d "$template_dir" ]] || runtime_die "templates de unit ausentes: $template_dir"

render_template() {
    local template="$1" destination="$2"
    python3 - "$template" "$destination" "$runtime_root" <<'PY'
from pathlib import Path
import sys

template_path = Path(sys.argv[1])
destination_path = Path(sys.argv[2])
runtime_root = sys.argv[3]
text = template_path.read_text(encoding="utf-8")
rendered = text.replace("@RUNTIME_ROOT@", runtime_root)
if "@RUNTIME_ROOT@" in rendered:
    raise SystemExit(f"placeholder não resolvido em {template_path}")
destination_path.write_text(rendered, encoding="utf-8")
PY
}

tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "$tmp_dir"' EXIT

for unit_name in "${runtime_all_units[@]}"; do
    template="${template_dir}/${unit_name}.in"
    [[ -f "$template" ]] || runtime_die "template ausente: $template"
    rendered="${tmp_dir}/${unit_name}"
    render_template "$template" "$rendered"
    destination="${unit_dir}/${unit_name}"

    if [[ "$apply" -eq 0 ]]; then
        printf 'would install %s from %s\n' "$destination" "$template"
        continue
    fi

    mkdir -p -- "$unit_dir"
    if [[ -n "$backup_dir" && ( -e "$destination" || -L "$destination" ) ]]; then
        mkdir -p -- "$backup_dir"
        backup_path="${backup_dir}/${unit_name}"
        if [[ ! -e "$backup_path" && ! -L "$backup_path" ]]; then
            # Materialize a previous symlink as a regular rollback file. A
            # snapshot made before stopping the transient frontend already
            # owns its backup path and is never overwritten here.
            install -m 0644 -- "$(realpath -e -- "$destination")" "$backup_path"
        fi
    fi
    rm -f -- "$destination"
    install -m 0644 -- "$rendered" "$destination"
    [[ ! -L "$destination" ]] || runtime_die "instalação deixou symlink: $destination"
    printf 'installed %s\n' "$destination"
done

if [[ "$daemon_reload" -eq 1 ]]; then
    systemctl --user daemon-reload
    for unit_name in "${runtime_all_units[@]}"; do
        expected_fragment="$(realpath -e -- "${unit_dir}/${unit_name}")" || \
            runtime_die "unit instalada não pode ser resolvida: ${unit_dir}/${unit_name}"
        loaded_fragment="$(
            systemctl --user show --property=FragmentPath --value "$unit_name" 2>/dev/null || true
        )"
        [[ -n "$loaded_fragment" ]] || \
            runtime_die "systemd não carregou a unit instalada: $unit_name"
        loaded_fragment="$(realpath -e -- "$loaded_fragment")" || \
            runtime_die "FragmentPath inválido para $unit_name: $loaded_fragment"
        [[ "$loaded_fragment" == "$expected_fragment" ]] || \
            runtime_die "systemd ainda carrega $unit_name de $loaded_fragment, esperado $expected_fragment"
    done
    printf 'systemd user daemon reloaded\n'
fi
