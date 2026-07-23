#!/usr/bin/env bash
# Install the frontend solely from the lockfile pinned in the runtime checkout.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-common.sh
source "${SCRIPT_DIR}/runtime-common.sh"

usage() {
    cat <<'EOF'
Usage: provision-runtime-frontend.sh --runtime-root PATH [--apply | --verify]

Validate a clean, detached DrvFs runtime checkout and the frontend lockfile
pinned in HEAD. The command is dry-run by default; --apply installs frontend
dependencies with npm ci. --verify checks the existing dependency tree without
running npm ci.
EOF
}

runtime_root=""
apply=0
verify=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --runtime-root)
            [[ -n "${2:-}" ]] || runtime_die "--runtime-root exige um caminho"
            runtime_root="$2"
            shift 2
            ;;
        --apply)
            apply=1
            shift
            ;;
        --verify)
            verify=1
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

[[ -n "$runtime_root" ]] || {
    usage >&2
    runtime_die "--runtime-root é obrigatório"
}
[[ "$apply" -eq 0 || "$verify" -eq 0 ]] || \
    runtime_die "--apply e --verify não podem ser usados juntos"

runtime_root="$(runtime_require_drvfs_root "$runtime_root")" || exit 1
runtime_require_command git || exit 1
runtime_require_command npm || exit 1

git_root="$(git -C "$runtime_root" rev-parse --show-toplevel 2>/dev/null)" || \
    runtime_die "runtime não é um checkout Git: $runtime_root"
git_root="$(runtime_resolve_existing "$git_root")" || exit 1
[[ "$git_root" == "$runtime_root" ]] || \
    runtime_die "--runtime-root precisa ser a raiz do checkout Git: $runtime_root"

runtime_assert_detached_head "$runtime_root" || exit 1
head_commit="$(git -C "$runtime_root" rev-parse --verify 'HEAD^{commit}' 2>/dev/null)" || \
    runtime_die "runtime não possui commit HEAD verificável"
runtime_require_full_commit_sha "$head_commit" >/dev/null || exit 1
runtime_assert_git_clean "$runtime_root" || exit 1

frontend_root="${runtime_root}/frontend"
lockfile="${frontend_root}/package-lock.json"
[[ -f "$lockfile" && ! -L "$lockfile" ]] || \
    runtime_die "frontend/package-lock.json ausente ou não é arquivo regular: $lockfile"

lock_type="$(git -C "$runtime_root" cat-file -t 'HEAD:frontend/package-lock.json' 2>/dev/null)" || \
    runtime_die "frontend/package-lock.json não está rastreado em HEAD"
[[ "$lock_type" == "blob" ]] || \
    runtime_die "frontend/package-lock.json em HEAD não é um blob"
git -C "$runtime_root" show 'HEAD:frontend/package-lock.json' | cmp -s - "$lockfile" || \
    runtime_die "frontend/package-lock.json no worktree diverge do blob pinado em HEAD"

verify_frontend_tree() {
    npm --prefix "$frontend_root" ls --all --omit=optional
    [[ -x "${frontend_root}/node_modules/.bin/vite" ]] || \
        runtime_die "dependências instaladas não contêm node_modules/.bin/vite executável"
}

if [[ "$verify" -eq 1 ]]; then
    verify_frontend_tree
    runtime_assert_git_clean "$runtime_root" || exit 1
    git -C "$runtime_root" show 'HEAD:frontend/package-lock.json' | cmp -s - "$lockfile" || \
        runtime_die "verificação alterou frontend/package-lock.json fora do blob pinado em HEAD"
    printf 'verified frontend dependencies from %s\n' "$head_commit"
    exit 0
fi

if [[ "$apply" -eq 0 ]]; then
    printf 'would run npm --prefix %s ci --include=dev --ignore-scripts --no-audit --no-fund\n' \
        "$frontend_root"
    exit 0
fi

npm --prefix "$frontend_root" ci --include=dev --ignore-scripts --no-audit --no-fund
verify_frontend_tree

# npm ci must leave the tracked inputs unchanged; node_modules remains ignored.
runtime_assert_git_clean "$runtime_root" || exit 1
git -C "$runtime_root" show 'HEAD:frontend/package-lock.json' | cmp -s - "$lockfile" || \
    runtime_die "npm ci alterou frontend/package-lock.json fora do blob pinado em HEAD"

printf 'provisioned frontend dependencies from %s\n' "$head_commit"
