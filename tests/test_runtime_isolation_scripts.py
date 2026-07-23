"""Regression coverage for the IRAI-25 staged runtime-isolation tooling."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = ROOT / "scripts" / "systemd"
INSTALLER = SYSTEMD_DIR / "install-runtime-units.sh"
PREFLIGHT = SYSTEMD_DIR / "runtime-preflight.sh"
CREATE_RUNTIME_CLONE = SYSTEMD_DIR / "create-runtime-clone.sh"
UPDATE_RUNTIME_REF = SYSTEMD_DIR / "update-runtime-ref.sh"
SNAPSHOT_RUNTIME_UNITS = SYSTEMD_DIR / "snapshot-runtime-units.sh"
COPY_RUNTIME_DATA = SYSTEMD_DIR / "copy-runtime-data.sh"
SNAPSHOT_RUNTIME_STATE = SYSTEMD_DIR / "snapshot-runtime-state.sh"
RESTORE_RUNTIME_STATE = SYSTEMD_DIR / "restore-runtime-state.sh"
RESTORE_RUNTIME_UNITS = SYSTEMD_DIR / "restore-runtime-units.sh"
PROVISION_RUNTIME_FRONTEND = SYSTEMD_DIR / "provision-runtime-frontend.sh"
SCRIPTS = (
    SYSTEMD_DIR / "runtime-common.sh",
    SYSTEMD_DIR / "runtime-data.sh",
    INSTALLER,
    SYSTEMD_DIR / "create-runtime-clone.sh",
    SYSTEMD_DIR / "copy-runtime-data.sh",
    SYSTEMD_DIR / "update-runtime-ref.sh",
    SNAPSHOT_RUNTIME_UNITS,
    SNAPSHOT_RUNTIME_STATE,
    RESTORE_RUNTIME_STATE,
    RESTORE_RUNTIME_UNITS,
    PROVISION_RUNTIME_FRONTEND,
    PREFLIGHT,
)
RUNTIME_TOOLING = (
    "scripts/systemd/runtime-common.sh",
    "scripts/systemd/runtime-data.sh",
    "scripts/systemd/create-runtime-clone.sh",
    "scripts/systemd/copy-runtime-data.sh",
    "scripts/systemd/install-runtime-units.sh",
    "scripts/systemd/provision-runtime-frontend.sh",
    "scripts/systemd/restore-runtime-state.sh",
    "scripts/systemd/restore-runtime-units.sh",
    "scripts/systemd/runtime-preflight.sh",
    "scripts/systemd/snapshot-runtime-state.sh",
    "scripts/systemd/snapshot-runtime-units.sh",
    "scripts/systemd/update-runtime-ref.sh",
)
SERVICE_UNITS = (
    "rastro-irado-api.service",
    "rastro-irado-collector.service",
    "rastro-irado-gex.service",
    "rastro-irado-win-ticks.service",
    "rastro-irado-p-dynamic-ledger.service",
    "rastro-irado-frontend.service",
)
TIMER_UNITS = (
    "rastro-irado-gex.timer",
    "rastro-irado-p-dynamic-ledger.timer",
)


def _run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ | (env or {})
    return subprocess.run(command, text=True, capture_output=True, env=merged_env, check=False)


def _fake_wslpath(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "wslpath"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"-w\" ]]; then\n"
        "  printf 'C:\\\\runtime\\\\%s\\n' \"${2##*/}\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


def _fake_systemctl(
    tmp_path: Path,
    unit_dir: Path,
    base_env: dict[str, str],
    *,
    transient_frontend: bool = False,
    frontend_fragment: Path | None = None,
) -> dict[str, str]:
    bin_dir = tmp_path / "systemctl-bin"
    bin_dir.mkdir()
    fake = bin_dir / "systemctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "if [[ \"${1:-}\" == \"--user\" ]]; then shift; fi\n"
        "case \"${1:-}\" in\n"
        "  daemon-reload)\n"
        "    [[ \"${FAKE_SYSTEMCTL_FAIL_COMMAND:-}\" != \"daemon-reload\" ]] || exit 42\n"
        "    exit 0 ;;\n"
        "  show)\n"
        "    unit=\"${!#}\"\n"
        "    if [[ \"$*\" == *DropInPaths* ]]; then\n"
        "      exit 0\n"
        "    elif [[ \"$unit\" == \"rastro-irado-frontend.service\" && -n \"${FAKE_FRONTEND_FRAGMENT:-}\" ]]; then\n"
        "      printf '%s\\n' \"$FAKE_FRONTEND_FRAGMENT\"\n"
        "    elif [[ \"$unit\" == \"rastro-irado-frontend.service\" && \"${FAKE_TRANSIENT_FRONTEND:-0}\" == \"1\" ]]; then\n"
        "      printf '%s\\n' '/run/user/1000/systemd/transient/rastro-irado-frontend.service'\n"
        "    else\n"
        "      printf '%s/%s\\n' \"$FAKE_UNIT_DIR\" \"$unit\"\n"
        "    fi\n"
        "    exit 0 ;;\n"
        "  is-active) exit 3 ;;\n"
        "  is-enabled) printf '%s\\n' enabled; exit 0 ;;\n"
        "  enable|disable)\n"
        "    [[ \"${FAKE_SYSTEMCTL_FAIL_COMMAND:-}\" != \"$1\" ]] || exit 42\n"
        "    if [[ -n \"${FAKE_SYSTEMCTL_LOG:-}\" ]]; then\n"
        "      printf '%s %s\\n' \"$1\" \"${2:-}\" >> \"$FAKE_SYSTEMCTL_LOG\"\n"
        "    fi\n"
        "    exit 0 ;;\n"
        "esac\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return {
        "PATH": f"{bin_dir}:{base_env['PATH']}",
        "FAKE_UNIT_DIR": str(unit_dir),
        "FAKE_TRANSIENT_FRONTEND": "1" if transient_frontend else "0",
        "FAKE_FRONTEND_FRAGMENT": str(frontend_fragment) if frontend_fragment else "",
    }


def _fake_npm(tmp_path: Path, base_env: dict[str, str], *, fail_ls: bool = False) -> dict[str, str]:
    bin_dir = tmp_path / "npm-bin"
    bin_dir.mkdir()
    fake = bin_dir / "npm"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "if [[ \"${1:-}\" != \"--prefix\" ]]; then exit 2; fi\n"
        "prefix=\"${2:?missing prefix}\"\n"
        "shift 2\n"
        "printf '%s %s\\n' \"$1\" \"$*\" >> \"$FAKE_NPM_LOG\"\n"
        "case \"${1:-}\" in\n"
        "  ci)\n"
        "    mkdir -p \"$prefix/node_modules/.bin\"\n"
        "    printf '#!/usr/bin/env sh\\nexit 0\\n' > \"$prefix/node_modules/.bin/vite\"\n"
        "    chmod 755 \"$prefix/node_modules/.bin/vite\"\n"
        "    ;;\n"
        "  ls)\n"
        "    [[ \"${FAKE_NPM_FAIL_LS:-0}\" != \"1\" ]] || exit 1\n"
        "    ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    log = tmp_path / "npm.log"
    return {
        "PATH": f"{bin_dir}:{base_env['PATH']}",
        "FAKE_NPM_LOG": str(log),
        "FAKE_NPM_FAIL_LS": "1" if fail_ls else "0",
    }


def _seed_runtime_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            """
            CREATE TABLE asset_models (
                target TEXT,
                slug TEXT,
                factors TEXT,
                factor_labels TEXT,
                session_start_h INTEGER,
                session_end_h INTEGER,
                data_proxy TEXT,
                active INTEGER
            );
            CREATE TABLE model_params (
                param_name TEXT,
                value REAL,
                effective_from TEXT
            );
            """
        )
        connection.execute(
            """
            INSERT INTO asset_models
                (target, slug, factors, factor_labels, session_start_h, session_end_h, data_proxy, active)
            VALUES ('WIN$N', 'win', '[]', '{}', 9, 18, NULL, 1)
            """
        )
        connection.executemany(
            "INSERT INTO model_params (param_name, value, effective_from) VALUES (?, ?, ?)",
            [
                ("win_alpha", 1.0, "2026-01-01T00:00:00Z"),
                ("win_intercept", 0.0, "2026-01-01T00:00:00Z"),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def _seed_loaded_units(unit_dir: Path) -> None:
    unit_dir.mkdir(parents=True, exist_ok=True)
    for unit_name in (*SERVICE_UNITS, *TIMER_UNITS):
        (unit_dir / unit_name).write_text(
            f"[Unit]\nDescription={unit_name}\n",
            encoding="utf-8",
        )


def _write_materialized_unit_backup(backup_dir: Path, states: dict[str, str] | None = None) -> None:
    _seed_loaded_units(backup_dir)
    states = states or {}
    contents = [
        "# IRAI runtime-unit snapshot; synthetic regression fixture.",
        "# Every unit below is a materialized regular file.",
    ]
    for unit_name in (*SERVICE_UNITS, *TIMER_UNITS):
        contents.append(f"{unit_name}:{states.get(unit_name, 'disabled')}")
    (backup_dir / "enabled-states.txt").write_text("\n".join(contents) + "\n", encoding="utf-8")


def _snapshot_update_state(
    tmp_path: Path,
    runtime_root: Path,
    env: dict[str, str],
) -> Path:
    loaded_units = tmp_path / "loaded-units"
    _seed_loaded_units(loaded_units)
    state_dir = tmp_path / "update-state"
    snapshot_units = _run(
        [
            str(SNAPSHOT_RUNTIME_UNITS),
            "--backup-dir",
            str(state_dir / "units"),
            "--apply",
        ],
        env=env | {"FAKE_UNIT_DIR": str(loaded_units)},
    )
    assert snapshot_units.returncode == 0, snapshot_units.stderr
    snapshot_state = _run(
        [
            str(runtime_root / "scripts" / "systemd" / "snapshot-runtime-state.sh"),
            "--runtime-root",
            str(runtime_root),
            "--state-dir",
            str(state_dir),
            "--apply",
        ],
        env=env,
    )
    assert snapshot_state.returncode == 0, snapshot_state.stderr
    return state_dir


def _runtime_clone(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "runtime"
    clone = _run(["git", "clone", "--no-hardlinks", str(ROOT), str(runtime_root)])
    assert clone.returncode == 0, clone.stderr
    lockfile = ROOT / "frontend" / "package-lock.json"
    assert lockfile.is_file(), "the IRAI-25 runtime requires a versionable frontend lockfile"
    destination_lockfile = runtime_root / "frontend" / "package-lock.json"
    destination_lockfile.write_bytes(lockfile.read_bytes())
    for relative_path in RUNTIME_TOOLING:
        source = ROOT / relative_path
        destination = runtime_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    shutil.copytree(
        ROOT / "scripts" / "systemd" / "runtime-units",
        runtime_root / "scripts" / "systemd" / "runtime-units",
    )
    for setting in (("user.email", "tests@example.invalid"), ("user.name", "IRAI tests")):
        configured = _run(["git", "-C", str(runtime_root), "config", *setting])
        assert configured.returncode == 0, configured.stderr
    added = _run(
        [
            "git",
            "-C",
            str(runtime_root),
            "add",
            "-f",
            "frontend/package-lock.json",
            *RUNTIME_TOOLING,
            "scripts/systemd/runtime-units",
        ]
    )
    assert added.returncode == 0, added.stderr
    committed = _run(["git", "-C", str(runtime_root), "commit", "-m", "test: pin frontend lockfile"])
    assert committed.returncode == 0, committed.stderr
    commit = _run(["git", "-C", str(runtime_root), "rev-parse", "HEAD"])
    assert commit.returncode == 0, commit.stderr
    detached = _run(["git", "-C", str(runtime_root), "checkout", "--detach", commit.stdout.strip()])
    assert detached.returncode == 0, detached.stderr
    origin = _run(["git", "-C", str(ROOT), "remote", "get-url", "origin"])
    assert origin.returncode == 0, origin.stderr
    set_origin = _run(["git", "-C", str(runtime_root), "remote", "set-url", "origin", origin.stdout.strip()])
    assert set_origin.returncode == 0, set_origin.stderr
    _seed_runtime_db(runtime_root / "data" / "irai.db")
    vite = runtime_root / "frontend" / "node_modules" / ".bin" / "vite"
    vite.parent.mkdir(parents=True)
    vite.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    vite.chmod(0o755)
    return runtime_root


class _RevisionHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/api/internal/p-dynamic-runtime-revision":
            self.send_error(404)
            return
        payload = json.dumps({"engine_revision": self.server.engine_revision}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_runtime_scripts_parse_as_bash() -> None:
    for script in SCRIPTS:
        result = _run(["bash", "-n", str(script)])
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_install_and_preflight_require_distinct_clean_runtime(tmp_path: Path) -> None:
    runtime_root = _runtime_clone(tmp_path)
    unit_dir = tmp_path / "units"
    env = _fake_wslpath(tmp_path)
    env |= _fake_systemctl(tmp_path, unit_dir, env)
    env |= _fake_npm(tmp_path, env)

    install = _run(
        [
            str(INSTALLER),
            "--runtime-root",
            str(runtime_root),
            "--unit-dir",
            str(unit_dir),
            "--apply",
            "--daemon-reload",
        ],
        env=env,
    )
    assert install.returncode == 0, install.stderr

    for unit_name in (*SERVICE_UNITS, *TIMER_UNITS):
        unit_path = unit_dir / unit_name
        assert unit_path.is_file()
        assert not unit_path.is_symlink()

    for unit_name in SERVICE_UNITS:
        content = (unit_dir / unit_name).read_text(encoding="utf-8")
        assert f"WorkingDirectory={runtime_root}" in content
        assert "/home/brenoperucchi/Devs/rastro_irado" not in content
    frontend_unit = (unit_dir / "rastro-irado-frontend.service").read_text(encoding="utf-8")
    assert "--host 0.0.0.0 --port 5175 --strictPort" in frontend_unit

    verify = _run(["systemd-analyze", "verify", *[str(unit_dir / name) for name in (*SERVICE_UNITS, *TIMER_UNITS)]])
    assert verify.returncode == 0, verify.stderr

    runtime_ref = _run(["git", "-C", str(runtime_root), "rev-parse", "HEAD"])
    assert runtime_ref.returncode == 0
    preflight = _run(
        [
            str(PREFLIGHT),
            "--runtime-root",
            str(runtime_root),
            "--development-root",
            str(ROOT),
            "--expected-ref",
            runtime_ref.stdout.strip(),
            "--unit-dir",
            str(unit_dir),
        ],
        env=env,
    )
    assert preflight.returncode == 0, preflight.stderr
    assert "runtime_engine_revision=" in preflight.stdout
    assert "sqlite_integrity_scope=quiescent-immutable" in preflight.stdout
    assert "verified frontend dependencies" in preflight.stdout

    revision = json.loads(
        next(line for line in preflight.stdout.splitlines() if line.startswith("runtime_engine_revision=")).split("=", 1)[1]
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RevisionHandler)
    server.engine_revision = revision
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with_api = _run(
            [
                str(PREFLIGHT),
                "--runtime-root",
                str(runtime_root),
                "--development-root",
                str(ROOT),
                "--expected-ref",
                runtime_ref.stdout.strip(),
                "--unit-dir",
                str(unit_dir),
                "--api-url",
                f"http://127.0.0.1:{server.server_port}",
            ],
            env=env,
        )
        assert with_api.returncode == 0, with_api.stderr
        assert "sqlite_integrity_scope=live-wal-aware" in with_api.stdout
        assert "api_runtime_revision=matches_disk" in with_api.stdout

        server.engine_revision = {"git_commit": "0" * 40}
        mismatch = _run(
            [
                str(PREFLIGHT),
                "--runtime-root",
                str(runtime_root),
                "--development-root",
                str(ROOT),
                "--expected-ref",
                runtime_ref.stdout.strip(),
                "--unit-dir",
                str(unit_dir),
                "--api-url",
                f"http://127.0.0.1:{server.server_port}",
            ],
            env=env,
        )
        assert mismatch.returncode != 0
        assert "não corresponde ao disco" in mismatch.stderr
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_preflight_rejects_runtime_aliasing_the_development_checkout(tmp_path: Path) -> None:
    env = _fake_wslpath(tmp_path)
    current_ref = _run(["git", "rev-parse", "HEAD"])
    assert current_ref.returncode == 0

    result = _run(
        [
            str(PREFLIGHT),
            "--runtime-root",
            str(ROOT),
            "--development-root",
            str(ROOT),
            "--expected-ref",
            current_ref.stdout.strip(),
            "--unit-dir",
            str(tmp_path / "units"),
        ],
        env=env,
    )
    assert result.returncode != 0
    assert "mesmo inode" in result.stderr


def test_create_runtime_clone_fails_closed_when_the_target_parent_is_missing(tmp_path: Path) -> None:
    env = _fake_wslpath(tmp_path)
    target = tmp_path / "missing-parent" / "runtime"
    current_ref = _run(["git", "rev-parse", "HEAD"])
    assert current_ref.returncode == 0

    result = _run(
        [
            str(CREATE_RUNTIME_CLONE),
            "--source-root",
            str(ROOT),
            "--runtime-root",
            str(target),
            "--ref",
            current_ref.stdout.strip(),
        ],
        env=env,
    )
    assert result.returncode != 0
    assert "caminho inexistente" in result.stderr
    assert "-> ." not in result.stderr
    assert not target.exists()


def test_installer_refuses_to_replace_a_loaded_transient_frontend(tmp_path: Path) -> None:
    runtime_root = _runtime_clone(tmp_path)
    unit_dir = tmp_path / "units"
    env = _fake_wslpath(tmp_path)
    env |= _fake_systemctl(tmp_path, unit_dir, env, transient_frontend=True)

    result = _run(
        [
            str(INSTALLER),
            "--runtime-root",
            str(runtime_root),
            "--unit-dir",
            str(unit_dir),
            "--apply",
            "--daemon-reload",
        ],
        env=env,
    )
    assert result.returncode != 0
    assert "frontend transient ainda carregado" in result.stderr
    assert not unit_dir.exists()


def test_installer_requires_daemon_reload_when_applying(tmp_path: Path) -> None:
    runtime_root = _runtime_clone(tmp_path)
    unit_dir = tmp_path / "units"
    env = _fake_wslpath(tmp_path)

    result = _run(
        [
            str(INSTALLER),
            "--runtime-root",
            str(runtime_root),
            "--unit-dir",
            str(unit_dir),
            "--apply",
        ],
        env=env,
    )
    assert result.returncode != 0
    assert "--apply exige --daemon-reload" in result.stderr
    assert not unit_dir.exists()


def test_update_runtime_ref_is_explicit_and_requires_apply_for_fetch(tmp_path: Path) -> None:
    runtime_root = _runtime_clone(tmp_path)
    unit_dir = tmp_path / "units"
    env = _fake_wslpath(tmp_path)
    env |= _fake_systemctl(tmp_path, unit_dir, env)
    state_dir = _snapshot_update_state(tmp_path, runtime_root, env)
    runtime_ref = _run(["git", "-C", str(runtime_root), "rev-parse", "HEAD"])
    assert runtime_ref.returncode == 0

    dry_run = _run(
        [
            str(UPDATE_RUNTIME_REF),
            "--runtime-root",
            str(runtime_root),
            "--state-dir",
            str(state_dir),
            "--ref",
            runtime_ref.stdout.strip(),
        ],
        env=env,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert "would check out detached" in dry_run.stdout

    fetch_without_apply = _run(
        [
            str(UPDATE_RUNTIME_REF),
            "--runtime-root",
            str(runtime_root),
            "--state-dir",
            str(state_dir),
            "--ref",
            runtime_ref.stdout.strip(),
            "--fetch",
        ],
        env=env,
    )
    assert fetch_without_apply.returncode != 0
    assert "--fetch altera o clone e exige --apply" in fetch_without_apply.stderr


def test_update_ref_requires_a_fresh_full_state_snapshot(tmp_path: Path) -> None:
    runtime_root = _runtime_clone(tmp_path)
    env = _fake_wslpath(tmp_path)
    env |= _fake_systemctl(tmp_path, tmp_path / "units", env)
    current_ref = _run(["git", "-C", str(runtime_root), "rev-parse", "HEAD"])
    assert current_ref.returncode == 0

    missing = _run(
        [
            str(UPDATE_RUNTIME_REF),
            "--runtime-root",
            str(runtime_root),
            "--state-dir",
            str(tmp_path / "missing-state"),
            "--ref",
            current_ref.stdout.strip(),
        ],
        env=env,
    )
    assert missing.returncode != 0
    assert "caminho inexistente" in missing.stderr

    state_dir = _snapshot_update_state(tmp_path, runtime_root, env)
    with sqlite3.connect(runtime_root / "data" / "irai.db") as connection:
        connection.execute("CREATE TABLE changed_after_snapshot (value INTEGER)")
    stale = _run(
        [
            str(UPDATE_RUNTIME_REF),
            "--runtime-root",
            str(runtime_root),
            "--state-dir",
            str(state_dir),
            "--ref",
            current_ref.stdout.strip(),
        ],
        env=env,
    )
    assert stale.returncode != 0
    assert "diverge do snapshot" in stale.stderr


def test_deployment_scripts_reject_symbolic_refs(tmp_path: Path) -> None:
    runtime_root = _runtime_clone(tmp_path)
    unit_dir = tmp_path / "units"
    env = _fake_wslpath(tmp_path)
    env |= _fake_systemctl(tmp_path, unit_dir, env)

    update = _run(
        [str(UPDATE_RUNTIME_REF), "--runtime-root", str(runtime_root), "--ref", "HEAD"],
        env=env,
    )
    assert update.returncode != 0
    assert "SHA Git completo" in update.stderr

    preflight = _run(
        [
            str(PREFLIGHT),
            "--runtime-root",
            str(runtime_root),
            "--development-root",
            str(ROOT),
            "--expected-ref",
            "main",
            "--unit-dir",
            str(unit_dir),
        ],
        env=env,
    )
    assert preflight.returncode != 0
    assert "SHA Git completo" in preflight.stderr


def test_snapshot_materializes_loaded_units_for_transient_safe_rollback(tmp_path: Path) -> None:
    source_units = tmp_path / "loaded-units"
    source_units.mkdir()
    for unit_name in (*SERVICE_UNITS, *TIMER_UNITS):
        (source_units / unit_name).write_text(
            f"[Unit]\nDescription={unit_name}\n",
            encoding="utf-8",
        )
    transient_frontend = tmp_path / "transient-frontend.service"
    transient_frontend.write_text(
        "[Unit]\nDescription=old transient frontend\n[Service]\n"
        "WorkingDirectory=/home/brenoperucchi/Devs/rastro_irado/frontend\n",
        encoding="utf-8",
    )

    backup_dir = tmp_path / "backup"
    env = _fake_systemctl(
        tmp_path,
        source_units,
        os.environ.copy(),
        frontend_fragment=transient_frontend,
    )
    snapshot = _run(
        [
            str(SNAPSHOT_RUNTIME_UNITS),
            "--backup-dir",
            str(backup_dir),
            "--apply",
        ],
        env=env,
    )
    assert snapshot.returncode == 0, snapshot.stderr
    for unit_name in (*SERVICE_UNITS, *TIMER_UNITS):
        copied = backup_dir / unit_name
        assert copied.is_file()
        assert not copied.is_symlink()
        expected = transient_frontend if unit_name == "rastro-irado-frontend.service" else source_units / unit_name
        assert copied.read_text(encoding="utf-8") == expected.read_text(encoding="utf-8")
    assert "rastro-irado-frontend.service:" in (backup_dir / "enabled-states.txt").read_text(encoding="utf-8")


def test_create_runtime_clone_uses_the_remote_origin_not_the_development_checkout(tmp_path: Path) -> None:
    bare_origin = tmp_path / "origin.git"
    initialized = _run(["git", "init", "--bare", str(bare_origin)])
    assert initialized.returncode == 0, initialized.stderr
    source_root = tmp_path / "development"
    cloned = _run(["git", "clone", str(bare_origin), str(source_root)])
    assert cloned.returncode == 0, cloned.stderr
    for setting in (("user.email", "tests@example.invalid"), ("user.name", "IRAI tests")):
        configured = _run(["git", "-C", str(source_root), "config", *setting])
        assert configured.returncode == 0, configured.stderr
    (source_root / "README.md").write_text("independent origin fixture\n", encoding="utf-8")
    committed = _run(["git", "-C", str(source_root), "add", "README.md"])
    assert committed.returncode == 0, committed.stderr
    committed = _run(["git", "-C", str(source_root), "commit", "-m", "seed runtime clone fixture"])
    assert committed.returncode == 0, committed.stderr
    pushed = _run(["git", "-C", str(source_root), "push", "origin", "HEAD:main"])
    assert pushed.returncode == 0, pushed.stderr
    ref = _run(["git", "-C", str(source_root), "rev-parse", "HEAD"])
    assert ref.returncode == 0

    runtime_root = tmp_path / "production" / "runtime"
    runtime_root.parent.mkdir()
    env = _fake_wslpath(tmp_path)
    created = _run(
        [
            str(CREATE_RUNTIME_CLONE),
            "--source-root",
            str(source_root),
            "--runtime-root",
            str(runtime_root),
            "--ref",
            ref.stdout.strip(),
            "--apply",
        ],
        env=env,
    )
    assert created.returncode == 0, created.stderr
    runtime_origin = _run(["git", "-C", str(runtime_root), "remote", "get-url", "origin"])
    assert runtime_origin.returncode == 0
    assert runtime_origin.stdout.strip() == str(bare_origin)
    assert _run(["git", "-C", str(runtime_root), "symbolic-ref", "-q", "HEAD"]).returncode != 0
    assert _run(["git", "-C", str(runtime_root), "rev-parse", "HEAD"]).stdout.strip() == ref.stdout.strip()
    source_common = _run(["git", "-C", str(source_root), "rev-parse", "--git-common-dir"])
    runtime_common = _run(["git", "-C", str(runtime_root), "rev-parse", "--git-common-dir"])
    assert source_common.returncode == runtime_common.returncode == 0
    assert (source_root / source_common.stdout.strip()).resolve() != (runtime_root / runtime_common.stdout.strip()).resolve()


def test_frontend_lockfile_is_eligible_and_provisioning_uses_the_pinned_blob(tmp_path: Path) -> None:
    assert (ROOT / "frontend" / "package-lock.json").is_file()
    assert _run(["git", "check-ignore", "-q", "frontend/package-lock.json"]).returncode != 0

    runtime_root = _runtime_clone(tmp_path)
    shutil.rmtree(runtime_root / "frontend" / "node_modules")
    env = _fake_wslpath(tmp_path)
    env |= _fake_npm(tmp_path, env)

    provisioned = _run(
        [str(PROVISION_RUNTIME_FRONTEND), "--runtime-root", str(runtime_root), "--apply"],
        env=env,
    )
    assert provisioned.returncode == 0, provisioned.stderr
    npm_log = Path(env["FAKE_NPM_LOG"]).read_text(encoding="utf-8")
    assert "ci ci --include=dev --ignore-scripts --no-audit --no-fund" in npm_log
    assert (runtime_root / "frontend" / "node_modules" / ".bin" / "vite").is_file()

    verified = _run(
        [str(PROVISION_RUNTIME_FRONTEND), "--runtime-root", str(runtime_root), "--verify"],
        env=env,
    )
    assert verified.returncode == 0, verified.stderr
    assert "ls ls --all --omit=optional" in Path(env["FAKE_NPM_LOG"]).read_text(encoding="utf-8")

    (runtime_root / "frontend" / "package-lock.json").write_text("tampered\n", encoding="utf-8")
    before = Path(env["FAKE_NPM_LOG"]).read_text(encoding="utf-8")
    rejected = _run(
        [str(PROVISION_RUNTIME_FRONTEND), "--runtime-root", str(runtime_root), "--apply"],
        env=env,
    )
    assert rejected.returncode != 0
    assert Path(env["FAKE_NPM_LOG"]).read_text(encoding="utf-8") == before


def test_preflight_rejects_unhealthy_frontend_tree_even_when_vite_exists(tmp_path: Path) -> None:
    runtime_root = _runtime_clone(tmp_path)
    unit_dir = tmp_path / "units"
    env = _fake_wslpath(tmp_path)
    env |= _fake_systemctl(tmp_path, unit_dir, env)
    env |= _fake_npm(tmp_path, env, fail_ls=True)
    install = _run(
        [
            str(INSTALLER),
            "--runtime-root",
            str(runtime_root),
            "--unit-dir",
            str(unit_dir),
            "--apply",
            "--daemon-reload",
        ],
        env=env,
    )
    assert install.returncode == 0, install.stderr
    runtime_ref = _run(["git", "-C", str(runtime_root), "rev-parse", "HEAD"])
    assert runtime_ref.returncode == 0
    preflight = _run(
        [
            str(PREFLIGHT),
            "--runtime-root",
            str(runtime_root),
            "--development-root",
            str(ROOT),
            "--expected-ref",
            runtime_ref.stdout.strip(),
            "--unit-dir",
            str(unit_dir),
        ],
        env=env,
    )
    assert preflight.returncode != 0
    assert Path(env["FAKE_NPM_LOG"]).read_text(encoding="utf-8").startswith("ls ")


def test_wal_aware_read_only_validation_sees_committed_live_wal_pages(tmp_path: Path) -> None:
    """immutable=1 is valid only after quiescence; it intentionally ignores WAL."""
    db_path = tmp_path / "live.db"
    writer = sqlite3.connect(db_path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("CREATE TABLE checkpointed (value INTEGER)")
        writer.commit()
        assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)

        writer.execute("CREATE TABLE wal_only (value INTEGER)")
        writer.commit()
        uri = db_path.resolve().as_uri()
        with sqlite3.connect(f"{uri}?mode=ro&immutable=1", uri=True) as immutable_reader:
            immutable_tables = immutable_reader.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
        with sqlite3.connect(f"{uri}?mode=ro", uri=True) as wal_reader:
            wal_tables = wal_reader.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
    finally:
        writer.close()

    assert immutable_tables == [("checkpointed",)]
    assert wal_tables == [("checkpointed",), ("wal_only",)]


def test_wal_aware_read_only_validation_rejects_a_corrupt_live_wal(tmp_path: Path) -> None:
    """A post-start validation must not silently fall back to the main DB."""
    db_path = tmp_path / "live.db"
    writer = sqlite3.connect(db_path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("CREATE TABLE checkpointed (value INTEGER)")
        writer.commit()
        assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
        writer.execute("CREATE TABLE wal_only (value INTEGER)")
        writer.commit()

        wal_path = Path(f"{db_path}-wal")
        wal = bytearray(wal_path.read_bytes())
        assert len(wal) > 4120
        wal[4120] ^= 0x01
        wal_path.write_bytes(wal)

        probe = _run(
            [
                sys.executable,
                "-c",
                (
                    "import sqlite3, sys; from pathlib import Path; "
                    "path = Path(sys.argv[1]).resolve(); "
                    "connection = sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True); "
                    "print(connection.execute('PRAGMA integrity_check').fetchone())"
                ),
                str(db_path),
            ]
        )
    finally:
        writer.close()

    assert probe.returncode != 0 or probe.stdout.strip() != "('ok',)"


def test_copy_runtime_data_rejects_busy_wal_before_touching_the_target(tmp_path: Path) -> None:
    source_root = _runtime_clone(tmp_path / "source")
    runtime_root = _runtime_clone(tmp_path / "runtime")
    (runtime_root / "data" / "irai.db").unlink()
    marker = runtime_root / "data" / "not-replaced.txt"
    marker.write_text("keep this target\n", encoding="utf-8")

    bin_dir = tmp_path / "sqlite-bin"
    bin_dir.mkdir()
    fake_sqlite = bin_dir / "sqlite3"
    fake_sqlite.write_text("#!/usr/bin/env sh\nprintf '1|4|3\\n'\n", encoding="utf-8")
    fake_sqlite.chmod(0o755)
    env = _fake_wslpath(tmp_path)
    env |= _fake_systemctl(tmp_path, tmp_path / "units", env)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    copied = _run(
        [
            str(COPY_RUNTIME_DATA),
            "--source-root",
            str(source_root),
            "--runtime-root",
            str(runtime_root),
            "--apply",
        ],
        env=env,
    )
    assert copied.returncode != 0
    assert "WAL não está quiescente" in copied.stderr
    assert marker.read_text(encoding="utf-8") == "keep this target\n"
    assert not (runtime_root / "data" / "irai.db").exists()


def test_copy_runtime_data_rejects_dirty_versioned_data_before_checkpoint_or_swap(tmp_path: Path) -> None:
    source_root = _runtime_clone(tmp_path / "source")
    runtime_root = _runtime_clone(tmp_path / "runtime")
    versioned_artifact = source_root / "data" / "api_overview.json"
    assert versioned_artifact.is_file()
    versioned_artifact.write_text("dirty versioned artifact\n", encoding="utf-8")
    (runtime_root / "data" / "irai.db").unlink()
    marker = runtime_root / "data" / "not-replaced.txt"
    marker.write_text("old runtime data\n", encoding="utf-8")
    env = _fake_wslpath(tmp_path)
    env |= _fake_systemctl(tmp_path, tmp_path / "units", env)

    copied = _run(
        [
            str(COPY_RUNTIME_DATA),
            "--source-root",
            str(source_root),
            "--runtime-root",
            str(runtime_root),
            "--apply",
        ],
        env=env,
    )
    assert copied.returncode != 0
    assert "source/data estão modificados" in copied.stderr
    assert marker.read_text(encoding="utf-8") == "old runtime data\n"
    assert not (runtime_root / "data" / "irai.db").exists()


def test_copy_runtime_data_stages_and_preserves_all_runtime_data(tmp_path: Path) -> None:
    source_root = _runtime_clone(tmp_path / "source")
    runtime_root = _runtime_clone(tmp_path / "runtime")
    source_extra = source_root / "data" / "ticks" / "win" / "sample.json"
    source_extra.parent.mkdir(parents=True, exist_ok=True)
    source_extra.write_text('{"tick": 1}\n', encoding="utf-8")
    ledger = source_root / "data" / "p_dynamic_parity" / "ledger.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text('{"session": "2026-07-23"}\n', encoding="utf-8")
    (runtime_root / "data" / "irai.db").unlink()
    (runtime_root / "data" / "old-runtime-file.txt").write_text("old\n", encoding="utf-8")
    env = _fake_wslpath(tmp_path)
    env |= _fake_systemctl(tmp_path, tmp_path / "units", env)

    copied = _run(
        [
            str(COPY_RUNTIME_DATA),
            "--source-root",
            str(source_root),
            "--runtime-root",
            str(runtime_root),
            "--apply",
        ],
        env=env,
    )
    assert copied.returncode == 0, copied.stderr
    assert (runtime_root / "data" / "irai.db").is_file()
    assert (runtime_root / "data" / "ticks" / "win" / "sample.json").read_text(encoding="utf-8") == '{"tick": 1}\n'
    assert (runtime_root / "data" / "p_dynamic_parity" / "ledger.json").read_text(encoding="utf-8") == '{"session": "2026-07-23"}\n'
    assert not (runtime_root / "data" / "old-runtime-file.txt").exists()


def test_copy_runtime_data_discards_staging_when_source_changes_mid_copy(tmp_path: Path) -> None:
    source_root = _runtime_clone(tmp_path / "source")
    runtime_root = _runtime_clone(tmp_path / "runtime")
    (runtime_root / "data" / "irai.db").unlink()
    marker = runtime_root / "data" / "not-replaced.txt"
    marker.write_text("old runtime data\n", encoding="utf-8")

    bin_dir = tmp_path / "tar-bin"
    bin_dir.mkdir()
    fake_tar = bin_dir / "tar"
    fake_tar.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "/usr/bin/tar \"$@\"\n"
        "if [[ \"$*\" == *'-cf - .'* ]]; then\n"
        "  printf 'changed during copy\\n' > \"${FAKE_SOURCE_DATA}/changed-during-copy.txt\"\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_tar.chmod(0o755)
    env = _fake_wslpath(tmp_path)
    env |= _fake_systemctl(tmp_path, tmp_path / "units", env)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_SOURCE_DATA"] = str(source_root / "data")

    copied = _run(
        [
            str(COPY_RUNTIME_DATA),
            "--source-root",
            str(source_root),
            "--runtime-root",
            str(runtime_root),
            "--apply",
        ],
        env=env,
    )
    assert copied.returncode != 0
    assert "origem mudou durante a cópia" in copied.stderr
    assert marker.read_text(encoding="utf-8") == "old runtime data\n"
    assert not (runtime_root / "data" / "irai.db").exists()


def test_update_rollback_restores_old_code_data_and_units_before_writers_resume(tmp_path: Path) -> None:
    runtime_root = _runtime_clone(tmp_path)
    env = _fake_wslpath(tmp_path)
    unit_dir = tmp_path / "installed-units"
    env |= _fake_systemctl(tmp_path, unit_dir, env)
    old_commit = _run(["git", "-C", str(runtime_root), "rev-parse", "HEAD"])
    assert old_commit.returncode == 0

    branch = _run(["git", "-C", str(runtime_root), "switch", "-c", "runtime-update-target"])
    assert branch.returncode == 0, branch.stderr
    (runtime_root / "README.md").write_text("updated runtime fixture\n", encoding="utf-8")
    # The candidate may remove its own restore entrypoint. The rollback must
    # still run from the known-good bootstrap captured in state_dir.
    (runtime_root / "scripts" / "systemd" / "restore-runtime-state.sh").unlink()
    staged = _run(
        [
            "git",
            "-C",
            str(runtime_root),
            "add",
            "README.md",
            "scripts/systemd/restore-runtime-state.sh",
        ]
    )
    assert staged.returncode == 0, staged.stderr
    committed = _run(["git", "-C", str(runtime_root), "commit", "-m", "test: update runtime fixture"])
    assert committed.returncode == 0, committed.stderr
    target_commit = _run(["git", "-C", str(runtime_root), "rev-parse", "HEAD"])
    assert target_commit.returncode == 0
    detached = _run(["git", "-C", str(runtime_root), "checkout", "--detach", old_commit.stdout.strip()])
    assert detached.returncode == 0, detached.stderr

    state_dir = _snapshot_update_state(tmp_path, runtime_root, env)
    updated = _run(
        [
            str(UPDATE_RUNTIME_REF),
            "--runtime-root",
            str(runtime_root),
            "--state-dir",
            str(state_dir),
            "--ref",
            target_commit.stdout.strip(),
            "--apply",
        ],
        env=env,
    )
    assert updated.returncode == 0, updated.stderr
    assert _run(["git", "-C", str(runtime_root), "rev-parse", "HEAD"]).stdout.strip() == target_commit.stdout.strip()
    assert not (runtime_root / "scripts" / "systemd" / "restore-runtime-state.sh").exists()

    with sqlite3.connect(runtime_root / "data" / "irai.db") as connection:
        connection.execute("CREATE TABLE simulated_new_api_migration (value INTEGER)")
    failed = _run(
        [
            str(state_dir / "rollback-bin" / "restore-runtime-state.sh"),
            "--runtime-root",
            str(runtime_root),
            "--state-dir",
            str(state_dir),
            "--unit-dir",
            str(unit_dir),
            "--apply",
        ],
        env=env,
    )
    assert failed.returncode == 0, failed.stderr
    assert _run(["git", "-C", str(runtime_root), "rev-parse", "HEAD"]).stdout.strip() == old_commit.stdout.strip()
    with sqlite3.connect(runtime_root / "data" / "irai.db") as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'simulated_new_api_migration'"
        ).fetchone() is None
    with sqlite3.connect(state_dir / "data-after-failed" / "irai.db") as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'simulated_new_api_migration'"
        ).fetchone() == ("simulated_new_api_migration",)
    assert (state_dir / "failed-commit").read_text(encoding="utf-8").strip() == target_commit.stdout.strip()
    assert (unit_dir / "rastro-irado-api.service").is_file()


def test_restore_state_refuses_an_entrypoint_outside_the_captured_bootstrap(tmp_path: Path) -> None:
    runtime_root = _runtime_clone(tmp_path)
    env = _fake_wslpath(tmp_path)
    env |= _fake_systemctl(tmp_path, tmp_path / "units", env)
    state_dir = _snapshot_update_state(tmp_path, runtime_root, env)

    rejected = _run(
        [
            str(RESTORE_RUNTIME_STATE),
            "--runtime-root",
            str(runtime_root),
            "--state-dir",
            str(state_dir),
            "--unit-dir",
            str(tmp_path / "restored-units"),
            "--apply",
        ],
        env=env,
    )
    assert rejected.returncode != 0
    assert "restore precisa executar o bootstrap capturado" in rejected.stderr


@pytest.mark.parametrize(
    ("failure", "states", "unit_dir", "expected_error"),
    [
        ("mkdir", {}, Path("/dev/null") / "cannot-create", "não foi possível criar diretório de units"),
        ("install", {}, Path("restored-units"), "não foi possível restaurar unit"),
        ("daemon-reload", {}, Path("restored-units"), "daemon-reload falhou durante rollback"),
        (
            "enable",
            {"rastro-irado-api.service": "enabled"},
            Path("restored-units"),
            "não foi possível habilitar unit restaurada",
        ),
        (
            "disable",
            {"rastro-irado-api.service": "disabled"},
            Path("restored-units"),
            "não foi possível desabilitar unit restaurada",
        ),
    ],
)
def test_restore_units_fails_closed_for_each_mutating_step(
    tmp_path: Path,
    failure: str,
    states: dict[str, str],
    unit_dir: Path,
    expected_error: str,
) -> None:
    backup_dir = tmp_path / "backup"
    _write_materialized_unit_backup(backup_dir, states)
    env = _fake_wslpath(tmp_path)
    env |= _fake_systemctl(tmp_path, tmp_path / "loaded-units", env)
    if failure in {"daemon-reload", "enable", "disable"}:
        env["FAKE_SYSTEMCTL_FAIL_COMMAND"] = failure
    if failure == "install":
        install_bin = tmp_path / "install-bin"
        install_bin.mkdir()
        fake_install = install_bin / "install"
        fake_install.write_text("#!/usr/bin/env sh\nexit 42\n", encoding="utf-8")
        fake_install.chmod(0o755)
        env["PATH"] = f"{install_bin}:{env['PATH']}"

    result = _run(
        [
            str(RESTORE_RUNTIME_UNITS),
            "--backup-dir",
            str(backup_dir),
            "--unit-dir",
            str(unit_dir if unit_dir.is_absolute() else tmp_path / unit_dir),
            "--apply",
        ],
        env=env,
    )
    assert result.returncode != 0
    assert expected_error in result.stderr


def test_restore_units_does_not_enable_materialized_linked_or_static_units(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    _write_materialized_unit_backup(
        backup_dir,
        {
            "rastro-irado-p-dynamic-ledger.service": "linked",
            "rastro-irado-gex.timer": "static",
        },
    )
    env = _fake_wslpath(tmp_path)
    env |= _fake_systemctl(tmp_path, tmp_path / "loaded-units", env)
    log_path = tmp_path / "systemctl.log"
    env["FAKE_SYSTEMCTL_LOG"] = str(log_path)

    restored = _run(
        [
            str(RESTORE_RUNTIME_UNITS),
            "--backup-dir",
            str(backup_dir),
            "--unit-dir",
            str(tmp_path / "restored-units"),
            "--apply",
        ],
        env=env,
    )
    assert restored.returncode == 0, restored.stderr
    actions = log_path.read_text(encoding="utf-8")
    assert "enable rastro-irado-p-dynamic-ledger.service" not in actions
    assert "enable rastro-irado-gex.timer" not in actions
