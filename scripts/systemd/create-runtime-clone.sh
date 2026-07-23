#!/usr/bin/env bash
# Create the code half of a distinct IRAI runtime checkout. Data is copied by
# copy-runtime-data.sh only after all writers are intentionally stopped.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-common.sh
source "${SCRIPT_DIR}/runtime-common.sh"

usage() {
    cat <<'EOF'
Usage: create-runtime-clone.sh --source-root PATH --runtime-root PATH --ref REF [--apply]

Create a clean, independent clone at --runtime-root and check out REF detached.
The command is dry-run by default. It never copies data/.
EOF
}

source_root=""
runtime_root=""
ref=""
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
        --ref)
            ref="${2:-}"
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

[[ -n "$source_root" && -n "$runtime_root" && -n "$ref" ]] || {
    usage >&2
    runtime_die "--source-root, --runtime-root e --ref são obrigatórios"
}

source_root="$(runtime_require_drvfs_root "$source_root")" || exit 1
runtime_parent="$(runtime_require_drvfs_root "$(dirname -- "$runtime_root")")" || exit 1
runtime_root="${runtime_parent}/$(basename -- "$runtime_root")"
[[ ! -e "$runtime_root" && ! -L "$runtime_root" ]] || \
    runtime_die "destino de runtime já existe: $runtime_root"

runtime_require_command git
ref="$(runtime_require_full_commit_sha "$ref")" || exit 1
origin_url="$(runtime_origin_url "$source_root")" || exit 1

if [[ "$apply" -eq 0 ]]; then
    printf 'would clone origin %s to %s and check out detached %s\n' "$origin_url" "$runtime_root" "$ref"
    exit 0
fi

git clone --no-local --no-checkout -- "$origin_url" "$runtime_root"
commit="$(git -C "$runtime_root" rev-parse --verify "${ref}^{commit}")" || {
    rm -rf -- "$runtime_root"
    runtime_die "SHA aprovada não existe no origin do runtime: $ref"
}
git -C "$runtime_root" checkout --detach "$commit"
runtime_assert_independent_checkout "$runtime_root" "$source_root" || exit 1
runtime_assert_git_clean "$runtime_root" || exit 1
runtime_assert_detached_head "$runtime_root" || exit 1
[[ "$(runtime_origin_url "$runtime_root")" == "$origin_url" ]] || \
    runtime_die "origin do clone runtime diverge do origin aprovado"

printf 'created runtime clone %s at %s\n' "$commit" "$runtime_root"
