#!/usr/bin/env bash
# Post-cutover durability gate for the IRAI runtime timers. It asserts the
# cutover left each timer DURABLY enabled: persistent enable (not enable
# --runtime), active, its timers.target.wants symlink resolving into the runtime
# unit dir (not a stale dev checkout), a real next fire scheduled, and — with
# --require-linger — user linger on so a running/headless distro still fires the
# timer with no interactive session. These are the cutover-induced ways the
# ledger could silently stop accumulating.
#
# It does NOT (and cannot) cover a fire missed while WSL is fully shut down: the
# p-dynamic-ledger timer is Persistent=false ON PURPOSE — its 17:56 capture is
# wall-clock-sensitive, so a late catch-up would write a mistimed row — so a
# machine-down-at-17:56 day is an accepted one-session gap, not something linger
# or this gate can recover. Run this AFTER reenable+start, as the final cutover
# gate. It never mutates state — it only asserts and fails closed.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-common.sh
source "${SCRIPT_DIR}/runtime-common.sh"

usage() {
    cat <<'EOF'
Usage: verify-runtime-units.sh [--unit-dir PATH] [--require-linger]

Assert the IRAI runtime timers are durably enabled after a cutover. Read-only.

Options:
  --unit-dir PATH   User-unit directory (default: ~/.config/systemd/user)
  --require-linger  Also require loginctl user linger, so the timers still fire
                    on a running/headless distro with no interactive session.
                    (Linger does not recover a fire missed while WSL is fully
                    shut down — that is bounded by Persistent=, not linger.)
  -h, --help        Show this help.
EOF
}

unit_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
require_linger=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --unit-dir)
            unit_dir="${2:-}"
            shift 2
            ;;
        --require-linger)
            require_linger=1
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

runtime_require_command systemctl || exit 1

verify_timer() {
    local timer="$1" enabled wants target expected next_elapse
    enabled="$(systemctl --user is-enabled "$timer" 2>/dev/null || true)"
    # enabled-runtime/linked/static/disabled/masked all fail: only a persistent
    # enable survives the WSL user-manager teardown that loses missed fires.
    [[ "$enabled" == "enabled" ]] || {
        runtime_error "timer não persistente (esperado enable, não enable --runtime/linked/static): $timer -> '${enabled:-vazio}'"
        return 1
    }
    systemctl --user is-active --quiet "$timer" || {
        runtime_error "timer inativo: $timer"
        return 1
    }
    wants="${unit_dir}/timers.target.wants/${timer}"
    [[ -L "$wants" ]] || {
        runtime_error "sem symlink de enablement em timers.target.wants: $timer"
        return 1
    }
    target="$(realpath -e -- "$wants" 2>/dev/null || true)"
    expected="$(realpath -e -- "${unit_dir}/${timer}" 2>/dev/null || true)"
    [[ -n "$expected" && "$target" == "$expected" ]] || {
        runtime_error "enablement aponta fora do runtime (checkout de dev movido?): $timer -> '${target:-quebrado}', esperado '$expected'"
        return 1
    }
    # list-timers --all imprime o nome do timer mesmo com NEXT n/a, então um
    # `list-timers | grep` só confirma que ele existe, não que há disparo
    # agendado. Consulte a propriedade direta: NextElapseUSecRealtime é 0/vazio
    # quando o timer OnCalendar não tem próximo disparo.
    next_elapse="$(systemctl --user show "$timer" -p NextElapseUSecRealtime --value 2>/dev/null || true)"
    case "$next_elapse" in
        ""|0|n/a|infinity)
            runtime_error "timer sem próximo disparo agendado (NextElapseUSecRealtime='${next_elapse:-vazio}'): $timer"
            return 1
            ;;
    esac
    return 0
}

failures=0
for timer in "${runtime_timer_units[@]}"; do
    verify_timer "$timer" || failures=$((failures + 1))
done

if [[ "$require_linger" -eq 1 ]]; then
    runtime_require_command loginctl || exit 1
    linger="$(loginctl show-user "${USER:-$(id -un)}" -p Linger --value 2>/dev/null || true)"
    [[ "$linger" == "yes" ]] || {
        runtime_error "linger desativado: timers não disparam em distro headless sem sessão interativa (use loginctl enable-linger; não recupera disparo perdido com WSL desligado)"
        failures=$((failures + 1))
    }
fi

[[ "$failures" -eq 0 ]] || runtime_die "durabilidade dos timers falhou: $failures problema(s)"
printf 'timers de runtime verificados (persistentes, ativos, wants no runtime): %s\n' "${runtime_timer_units[*]}"
