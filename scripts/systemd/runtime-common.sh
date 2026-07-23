#!/usr/bin/env bash
# Shared checks for the IRAI runtime-isolation commands.

set -euo pipefail

runtime_die() {
    printf 'runtime-isolation: %s\n' "$*" >&2
    exit 1
}

runtime_error() {
    printf 'runtime-isolation: %s\n' "$*" >&2
}

runtime_require_command() {
    command -v "$1" >/dev/null 2>&1 && return 0
    runtime_error "comando obrigatório ausente: $1"
    return 1
}

runtime_resolve_existing() {
    realpath -e -- "$1" 2>/dev/null && return 0
    runtime_error "caminho inexistente: $1"
    return 1
}

runtime_require_drvfs_root() {
    local root windows_path
    root="$(runtime_resolve_existing "$1")" || return 1
    runtime_require_command wslpath || return 1
    windows_path="$(wslpath -w "$root")" || {
        runtime_error "não foi possível converter para caminho Windows: $root"
        return 1
    }

    # DrvFs becomes a drive path to Windows. A UNC path means WSL ext4 is being
    # accessed through \\wsl.localhost, which is not safe for this SQLite WAL runtime.
    if [[ ! "${windows_path:0:1}" =~ [A-Za-z] || "${windows_path:1:2}" != ':\' ]]; then
        runtime_error "raiz precisa resolver para drive Windows (não UNC/9p): $root -> $windows_path"
        return 1
    fi
    printf '%s\n' "$root"
}

runtime_inode() {
    stat -c '%d:%i' -- "$1"
}

runtime_git_common_dir() {
    local worktree="$1" raw
    raw="$(git -C "$worktree" rev-parse --git-common-dir)" || {
        runtime_error "não é checkout Git: $worktree"
        return 1
    }
    if [[ "$raw" = /* ]]; then
        runtime_resolve_existing "$raw"
    else
        (cd "$worktree" && runtime_resolve_existing "$raw")
    fi
}

runtime_assert_independent_checkout() {
    local runtime_root development_root runtime_common development_common
    runtime_root="$(runtime_require_drvfs_root "$1")" || return 1
    development_root="$(runtime_require_drvfs_root "$2")" || return 1

    if [[ "$(runtime_inode "$runtime_root")" == "$(runtime_inode "$development_root")" ]]; then
        runtime_error "runtime e desenvolvimento apontam para o mesmo inode"
        return 1
    fi

    runtime_common="$(runtime_git_common_dir "$runtime_root")" || return 1
    development_common="$(runtime_git_common_dir "$development_root")" || return 1
    if [[ "$runtime_common" == "$development_common" ]]; then
        runtime_error "runtime e desenvolvimento compartilham git common-dir; use clone, não worktree"
        return 1
    fi
}

runtime_assert_data_inside_root() {
    local runtime_root data_root
    runtime_root="$(runtime_resolve_existing "$1")" || return 1
    data_root="$(runtime_resolve_existing "$runtime_root/data")" || return 1
    if [[ "$data_root" != "$runtime_root/data" ]]; then
        runtime_error "data/ precisa residir fisicamente dentro da raiz de runtime: $data_root"
        return 1
    fi
}

runtime_assert_git_clean() {
    local root="$1" status
    status="$(git -C "$root" status --porcelain --untracked-files=all)"
    if [[ -n "$status" ]]; then
        runtime_error "checkout de runtime não está limpo:\n$status"
        return 1
    fi
}

runtime_require_full_commit_sha() {
    local ref="$1"
    if [[ ! "$ref" =~ ^[0-9A-Fa-f]{40}$ ]]; then
        runtime_error "ref precisa ser SHA Git completo de 40 caracteres, não branch/tag/HEAD: $ref"
        return 1
    fi
    printf '%s\n' "$ref"
}

runtime_origin_url() {
    local root="$1" origin
    origin="$(git -C "$root" remote get-url origin 2>/dev/null)" || {
        runtime_error "checkout não possui remote origin: $root"
        return 1
    }
    [[ -n "$origin" ]] || {
        runtime_error "remote origin vazio: $root"
        return 1
    }
    printf '%s\n' "$origin"
}

runtime_assert_detached_head() {
    local root="$1" branch
    branch="$(git -C "$root" symbolic-ref -q --short HEAD 2>/dev/null || true)"
    [[ -z "$branch" ]] || {
        runtime_error "runtime precisa estar em HEAD destacado, não no branch $branch"
        return 1
    }
}

runtime_assert_state_dir_outside_runtime() {
    # A rollback state must survive a checkout and share the same Windows
    # volume as runtime/data so its verified directory swaps remain local.
    local runtime_root state_dir runtime_device state_device
    runtime_root="$(runtime_require_drvfs_root "$1")" || return 1
    state_dir="$(runtime_require_drvfs_root "$2")" || return 1
    case "${state_dir}/" in
        "${runtime_root}/"*)
            runtime_error "state-dir precisa ficar fora do checkout de runtime: $state_dir"
            return 1
            ;;
    esac
    runtime_device="$(stat -c '%d' -- "$runtime_root")" || return 1
    state_device="$(stat -c '%d' -- "$state_dir")" || return 1
    [[ "$runtime_device" == "$state_device" ]] || {
        runtime_error "state-dir precisa ficar no mesmo volume do runtime: $state_dir"
        return 1
    }
    printf '%s\n' "$state_dir"
}

runtime_assert_materialized_unit_backup() {
    local backup_dir="$1" unit_name
    [[ -f "${backup_dir}/enabled-states.txt" ]] || {
        runtime_error "backup sem enabled-states.txt: $backup_dir"
        return 1
    }
    for unit_name in "${runtime_all_units[@]}"; do
        [[ -f "${backup_dir}/${unit_name}" && ! -L "${backup_dir}/${unit_name}" ]] || {
            runtime_error "backup de unit ausente ou não regular: ${backup_dir}/${unit_name}"
            return 1
        }
    done
}

runtime_rollback_bootstrap_files=(
    runtime-common.sh
    runtime-data.sh
    restore-runtime-units.sh
    restore-runtime-state.sh
)

runtime_capture_rollback_bootstrap() {
    # Copy the rollback interpreter from the known-good runtime before a
    # candidate checkout. A later rollback must never execute candidate code.
    local script_dir="$1" state_dir="$2" bootstrap_dir file source
    bootstrap_dir="${state_dir}/rollback-bin"
    [[ ! -e "$bootstrap_dir" && ! -L "$bootstrap_dir" ]] || {
        runtime_error "rollback-bin já existe no state-dir: $bootstrap_dir"
        return 1
    }
    mkdir -p -- "$bootstrap_dir" || {
        runtime_error "não foi possível criar rollback-bin: $bootstrap_dir"
        return 1
    }
    for file in "${runtime_rollback_bootstrap_files[@]}"; do
        source="$(runtime_resolve_existing "${script_dir}/${file}")" || return 1
        [[ -f "$source" && ! -L "$source" ]] || {
            runtime_error "bootstrap de rollback ausente ou não regular: $source"
            return 1
        }
        install -m 0755 -- "$source" "${bootstrap_dir}/${file}" || {
            runtime_error "não foi possível capturar bootstrap de rollback: $file"
            return 1
        }
    done
    (
        cd "$bootstrap_dir"
        sha256sum "${runtime_rollback_bootstrap_files[@]}" > "${state_dir}/rollback-bin.sha256"
    ) || {
        runtime_error "não foi possível manifestar bootstrap de rollback"
        return 1
    }
}

runtime_assert_rollback_bootstrap() {
    local state_dir="$1" invoked_script_dir="${2:-}" bootstrap_dir
    bootstrap_dir="${state_dir}/rollback-bin"
    [[ -d "$bootstrap_dir" && -f "${state_dir}/rollback-bin.sha256" ]] || {
        runtime_error "state-dir sem bootstrap de rollback: $state_dir"
        return 1
    }
    (
        cd "$bootstrap_dir"
        sha256sum --check --status "${state_dir}/rollback-bin.sha256"
    ) || {
        runtime_error "manifest do bootstrap de rollback diverge: $bootstrap_dir"
        return 1
    }
    if [[ -n "$invoked_script_dir" ]]; then
        invoked_script_dir="$(runtime_resolve_existing "$invoked_script_dir")" || return 1
        bootstrap_dir="$(runtime_resolve_existing "$bootstrap_dir")" || return 1
        [[ "$invoked_script_dir" == "$bootstrap_dir" ]] || {
            runtime_error "restore precisa executar o bootstrap capturado, não scripts do runtime candidato: $bootstrap_dir"
            return 1
        }
    fi
}

runtime_restore_materialized_units() {
    # The caller must have stopped every managed unit before entering this
    # function. Keep it in the already-loaded shell so an old checkout cannot
    # replace the rollback implementation mid-restore.
    local backup_dir="$1" unit_dir="$2" unit_name enabled_state
    runtime_assert_materialized_unit_backup "$backup_dir" || return 1

    mkdir -p -- "$unit_dir" || {
        runtime_error "não foi possível criar diretório de units: $unit_dir"
        return 1
    }
    for unit_name in "${runtime_all_units[@]}"; do
        install -m 0644 -- "${backup_dir}/${unit_name}" "${unit_dir}/${unit_name}" || {
            runtime_error "não foi possível restaurar unit: $unit_name"
            return 1
        }
    done
    systemctl --user daemon-reload || {
        runtime_error "systemd --user daemon-reload falhou durante rollback"
        return 1
    }

    while IFS=: read -r unit_name enabled_state; do
        [[ -n "$unit_name" && "$unit_name" != \#* ]] || continue
        case "$enabled_state" in
            enabled|enabled-runtime)
                systemctl --user enable "$unit_name" || {
                    runtime_error "não foi possível habilitar unit restaurada: $unit_name"
                    return 1
                }
                ;;
            disabled)
                systemctl --user disable "$unit_name" || {
                    runtime_error "não foi possível desabilitar unit restaurada: $unit_name"
                    return 1
                }
                ;;
            linked|linked-runtime|static|indirect|transient)
                # A linked unit was materialized as a regular rollback file.
                # Do not call enable for states that may lack an [Install]
                # section; the loaded fragment is restored by daemon-reload.
                ;;
            *)
                runtime_error "estado de enablement desconhecido no backup: $unit_name:$enabled_state"
                return 1
                ;;
        esac
    done < "${backup_dir}/enabled-states.txt"
}

runtime_service_units=(
    rastro-irado-api.service
    rastro-irado-collector.service
    rastro-irado-gex.service
    rastro-irado-win-ticks.service
    rastro-irado-p-dynamic-ledger.service
    rastro-irado-frontend.service
)

runtime_timer_units=(
    rastro-irado-gex.timer
    rastro-irado-p-dynamic-ledger.timer
)

runtime_all_units=("${runtime_service_units[@]}" "${runtime_timer_units[@]}")
