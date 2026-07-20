"""Identidade imutável do motor IRAI carregado por um processo da API."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Mapping


ENGINE_REVISION_FILES = (
    "backend/irai/engine.py",
    "backend/irai/kalman.py",
)
REVISION_FIELDS = ("git_commit", "engine_sha256", "kalman_sha256")


def build_engine_revision(root: Path | None = None) -> dict[str, str]:
    """Calcula a revisão do motor no instante em que o processo é iniciado."""
    root = root or Path(__file__).resolve().parents[2]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        git_commit = completed.stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("não foi possível identificar o commit do motor") from exc
    if len(git_commit) != 40 or any(char not in "0123456789abcdef" for char in git_commit):
        raise RuntimeError(f"commit Git inválido para o motor: {git_commit!r}")

    revision = {"git_commit": git_commit}
    for relative_path in ENGINE_REVISION_FILES:
        try:
            digest = hashlib.sha256((root / relative_path).read_bytes()).hexdigest()
        except OSError as exc:
            raise RuntimeError(
                f"não foi possível ler o código do motor: {relative_path}"
            ) from exc
        revision[Path(relative_path).stem + "_sha256"] = digest
    return revision


def validate_engine_revision(value: object) -> dict[str, str]:
    """Normaliza a identidade recebida da API ou de um manifesto, fail-closed."""
    if not isinstance(value, Mapping):
        raise ValueError("revisão verificável do motor ausente")
    normalized: dict[str, str] = {}
    for name in REVISION_FIELDS:
        item = value.get(name)
        expected_length = 40 if name == "git_commit" else 64
        if (
            not isinstance(item, str)
            or len(item) != expected_length
            or any(char not in "0123456789abcdef" for char in item)
        ):
            raise ValueError(f"revisão do motor inválida: {name}")
        normalized[name] = item
    return normalized
