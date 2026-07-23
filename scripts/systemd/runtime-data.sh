#!/usr/bin/env bash
# Shared data-snapshot primitives for the IRAI runtime cutover. Callers source
# this after runtime-common.sh; all functions return nonzero rather than
# deleting an existing runtime copy on a failed validation.

runtime_require_all_units_inactive() {
    runtime_require_command systemctl || return 1
    local unit
    for unit in "${runtime_service_units[@]}" "${runtime_timer_units[@]}"; do
        if systemctl --user is-active --quiet "$unit"; then
            runtime_error "unit ainda ativa: $unit; snapshot de data exige todos writers/timers parados"
            return 1
        fi
    done
}

runtime_checkpoint_wal() {
    local db_path="$1" checkpoint
    [[ -f "$db_path" ]] || {
        runtime_error "banco SQLite ausente: $db_path"
        return 1
    }
    runtime_require_command sqlite3 || return 1

    checkpoint="$(
        sqlite3 -batch -noheader -cmd '.timeout 10000' "$db_path" 'PRAGMA wal_checkpoint(TRUNCATE);'
    )" || {
        runtime_error "wal_checkpoint(TRUNCATE) não executou: $db_path"
        return 1
    }
    if [[ "$checkpoint" != '0|0|0' ]]; then
        runtime_error "WAL não está quiescente para snapshot: $db_path -> $checkpoint"
        return 1
    fi
    [[ ! -s "${db_path}-wal" ]] || {
        runtime_error "WAL não truncado após checkpoint: ${db_path}-wal"
        return 1
    }
}

runtime_verify_sqlite_integrity() {
    local db_path="$1"
    python3 - "$db_path" <<'PY'
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1]).resolve()
# Every caller has already required a successful WAL checkpoint. immutable=1
# avoids creating -wal/-shm sidecars merely by validating a staged snapshot.
connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True, timeout=10)
try:
    row = connection.execute("PRAGMA integrity_check").fetchone()
finally:
    connection.close()
if row != ("ok",):
    raise SystemExit(f"integrity_check falhou para {path}: {row!r}")
PY
}

runtime_assert_tracked_data_matches_runtime_ref() {
    # data/ contains a small set of versioned historical artifacts alongside
    # live state. Copying a dirty/version-mismatched tracked artifact would
    # turn the pinned runtime checkout dirty. Live writers only use ignored
    # paths, so reject the mismatch rather than weakening the code-clean gate.
    local source_root="$1" runtime_root="$2" source_paths runtime_paths path
    runtime_require_command git || return 1
    git -C "$source_root" diff --quiet -- data || {
        runtime_error "arquivos rastreados em source/data estão modificados; não copie estado sobre ref pinada"
        return 1
    }
    git -C "$source_root" diff --cached --quiet -- data || {
        runtime_error "arquivos rastreados em source/data estão staged; não copie estado sobre ref pinada"
        return 1
    }
    if [[ -n "$(git -C "$source_root" ls-files --others --exclude-standard -- data)" ]]; then
        runtime_error "source/data contém arquivo não rastreado e não ignorado; trate-o antes do corte"
        return 1
    fi

    source_paths="$(mktemp)" || return 1
    runtime_paths="$(mktemp)" || {
        rm -f -- "$source_paths"
        return 1
    }
    git -C "$source_root" ls-files -- data | LC_ALL=C sort > "$source_paths"
    git -C "$runtime_root" ls-files -- data | LC_ALL=C sort > "$runtime_paths"
    if ! cmp -s "$source_paths" "$runtime_paths"; then
        rm -f -- "$source_paths" "$runtime_paths"
        runtime_error "conjunto de arquivos rastreados em data/ diverge entre source e runtime pinado"
        return 1
    fi
    while IFS= read -r path; do
        [[ -z "$path" ]] && continue
        if [[ ! -f "${source_root}/${path}" || -L "${source_root}/${path}" || ! -f "${runtime_root}/${path}" || -L "${runtime_root}/${path}" ]]; then
            rm -f -- "$source_paths" "$runtime_paths"
            runtime_error "arquivo rastreado de data/ ausente ou não regular: $path"
            return 1
        fi
        if ! cmp -s "${source_root}/${path}" "${runtime_root}/${path}"; then
            rm -f -- "$source_paths" "$runtime_paths"
            runtime_error "arquivo rastreado de data/ diverge da ref pinada: $path"
            return 1
        fi
    done < "$source_paths"
    rm -f -- "$source_paths" "$runtime_paths"
}

runtime_write_data_manifest() {
    local data_root="$1" output="$2"
    (
        cd "$data_root"
        find . -type f -print0 | sort -z | xargs -0 -r sha256sum
        find . -type l -print0 | sort -z | while IFS= read -r -d '' path; do
            printf 'symlink %s -> %s\n' "$path" "$(readlink -- "$path")"
        done
    ) > "$output"
}

runtime_stage_verified_data_copy() {
    # Prints the new staging directory. The caller owns cleanup or the final
    # same-filesystem rename; the source is checked before and after copying.
    local source_data="$1" stage_parent="$2" stage_dir
    [[ -d "$source_data" ]] || {
        runtime_error "data de origem ausente: $source_data"
        return 1
    }
    [[ -d "$stage_parent" ]] || {
        runtime_error "diretório pai de staging ausente: $stage_parent"
        return 1
    }
    runtime_require_command tar || return 1

    stage_dir="$(mktemp -d "${stage_parent}/.rastro-irado-data-stage.XXXXXX")" || {
        runtime_error "não foi possível criar staging em $stage_parent"
        return 1
    }
    mkdir -p -- "${stage_dir}/data"
    runtime_write_data_manifest "$source_data" "${stage_dir}/source-before.sha256" || {
        rm -rf -- "$stage_dir"
        return 1
    }
    if ! tar -C "$source_data" -cf - . | tar -C "${stage_dir}/data" -xf -; then
        rm -rf -- "$stage_dir"
        runtime_error "cópia de data/ para staging falhou"
        return 1
    fi
    runtime_write_data_manifest "$source_data" "${stage_dir}/source-after.sha256" || {
        rm -rf -- "$stage_dir"
        return 1
    }
    runtime_write_data_manifest "${stage_dir}/data" "${stage_dir}/target.sha256" || {
        rm -rf -- "$stage_dir"
        return 1
    }
    if ! cmp -s "${stage_dir}/source-before.sha256" "${stage_dir}/source-after.sha256"; then
        rm -rf -- "$stage_dir"
        runtime_error "data/ de origem mudou durante a cópia; staging descartado"
        return 1
    fi
    if ! cmp -s "${stage_dir}/source-before.sha256" "${stage_dir}/target.sha256"; then
        rm -rf -- "$stage_dir"
        runtime_error "manifest de data/ diverge após cópia; staging descartado"
        return 1
    fi
    runtime_verify_sqlite_integrity "${stage_dir}/data/irai.db" || {
        rm -rf -- "$stage_dir"
        return 1
    }
    printf '%s\n' "$stage_dir"
}

runtime_swap_staged_data() {
    # Atomically installs STAGE_DIR/data where possible while keeping the
    # previous directory until the replacement has completed. Caller chooses
    # where the retained previous copy belongs.
    local stage_dir="$1" destination_data="$2" retained_previous="$3"
    [[ -d "${stage_dir}/data" ]] || {
        runtime_error "staging sem data/: $stage_dir"
        return 1
    }
    [[ -d "$destination_data" ]] || {
        runtime_error "data de destino ausente: $destination_data"
        return 1
    }
    [[ ! -e "$retained_previous" && ! -L "$retained_previous" ]] || {
        runtime_error "destino de data anterior já existe: $retained_previous"
        return 1
    }
    mv -- "$destination_data" "$retained_previous" || return 1
    if ! mv -- "${stage_dir}/data" "$destination_data"; then
        mv -- "$retained_previous" "$destination_data" || true
        runtime_error "não foi possível promover staging; data anterior restaurada"
        return 1
    fi
}
