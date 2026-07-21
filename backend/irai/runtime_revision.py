"""Identidade imutável do motor IRAI carregado por um processo da API."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Mapping


ENGINE_REVISION_FILES = (
    "backend/irai/engine.py",
    "backend/irai/kalman.py",
)
# Somente dependências que podem alterar o P_up v1/v2 capturado pelo ledger.
# Johansen e NWE são serializados no mesmo endpoint, mas hoje só alteram
# verdict/indicadores auxiliares; incluí-los faria uma mudança neles reiniciar
# indevidamente a amostra OOS.
RUNTIME_CODE_FILES = (
    "backend/db.py",
    "backend/irai/engine.py",
    "backend/irai/kalman.py",
    "backend/irai/market_geometry.py",
    "backend/irai/timezones.py",
    "backend/irai/zscore.py",
)
REVISION_FIELDS = (
    "git_commit",
    "engine_sha256",
    "kalman_sha256",
    "runtime_code_sha256",
    "model_config_sha256",
)
PREDICTION_REVISION_FIELDS = ("runtime_code_sha256", "model_config_sha256")
MODEL_CONFIG_READ_ATTEMPTS = 3
MODEL_CONFIG_READ_RETRY_SECONDS = 0.1
P_DYNAMIC_TARGET = "WIN$N"


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_column(value: object, *, table: str, column: str, target: str) -> object:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuntimeError(f"{table}.{column} inválido para {target}")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{table}.{column} não contém JSON válido para {target}") from exc


def _prediction_param(param_name: object, slug: str) -> bool:
    """Espelha os parâmetros que ``IRAIEngine._load_params`` usa em P_up."""
    if not isinstance(param_name, str):
        raise RuntimeError(f"model_params.param_name inválido para slug {slug!r}")
    prefix = f"{slug}_"
    clean = param_name[len(prefix):] if param_name.startswith(prefix) else param_name
    return clean in {"alpha", "intercept"} or clean.startswith(("w_", "sigma_"))


def _target_model_config(db_path: str | Path | None, *, target: str) -> str:
    """Hash da configuração efetivamente carregada pelo alvo do ledger.

    O torneio mede um alvo por vez. Incluir parâmetros de outro ativo ativo
    (por exemplo, recalibrar US500 durante um ledger de WIN) fragmentaria a
    amostra sem alterar nenhum P_up comparado. A mesma regra vale para
    parâmetros/indicadores auxiliares que o motor não consulta ao calcular a
    probabilidade. O motor não usa timestamps de calibração para prever,
    portanto o payload canônico registra o valor efetivo, não apenas a data
    em que ele foi gravado.
    """
    if db_path is None:
        from backend.db import DB_PATH

        db_path = DB_PATH
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"banco de configuração do motor inexistente: {path}")

    ledger_target = target
    last_error = None
    for attempt in range(MODEL_CONFIG_READ_ATTEMPTS):
        connection = None
        try:
            connection = sqlite3.connect(
                f"{path.as_uri()}?mode=ro", uri=True, timeout=5.0
            )
            rows = connection.execute(
                """
                SELECT target, slug, factors, factor_labels, session_start_h,
                       session_end_h, data_proxy
                FROM asset_models
                WHERE active = 1 AND target = ?
                """,
                (target,),
            ).fetchall()
            if len(rows) != 1:
                raise RuntimeError(
                    f"configuração ativa ausente ou ambígua para o alvo {target!r}"
                )
            models = []
            for (
                row_target,
                slug,
                factors,
                factor_labels,
                session_start_h,
                session_end_h,
                data_proxy,
            ) in rows:
                if not isinstance(row_target, str) or not isinstance(slug, str):
                    raise RuntimeError("asset_models possui target/slug inválido")
                params = connection.execute(
                    """
                    SELECT mp.param_name, mp.value
                    FROM model_params mp
                    INNER JOIN (
                        SELECT param_name, MAX(effective_from) AS max_effective_from
                        FROM model_params
                        WHERE param_name LIKE ?
                        GROUP BY param_name
                    ) latest ON mp.param_name = latest.param_name
                           AND mp.effective_from = latest.max_effective_from
                    WHERE mp.param_name LIKE ?
                    ORDER BY mp.param_name
                    """,
                    (f"{slug}_%", f"{slug}_%"),
                ).fetchall()
                models.append(
                    {
                        "target": row_target,
                        "slug": slug,
                        "factors": _json_column(
                            factors,
                            table="asset_models",
                            column="factors",
                            target=row_target,
                        ),
                        "factor_labels": _json_column(
                            factor_labels,
                            table="asset_models",
                            column="factor_labels",
                            target=row_target,
                        ),
                        "session_start_h": session_start_h or 0,
                        "session_end_h": session_end_h or 24,
                        "data_proxy": data_proxy,
                        "params": [
                            {"param_name": name, "value": value}
                            for name, value in params
                            if _prediction_param(name, slug)
                        ],
                    }
                )
            return _sha256_json({"target": ledger_target, "models": models})
        except (sqlite3.Error, OSError) as exc:
            last_error = exc
            if attempt + 1 == MODEL_CONFIG_READ_ATTEMPTS:
                break
            time.sleep(MODEL_CONFIG_READ_RETRY_SECONDS)
        finally:
            if connection is not None:
                connection.close()

    raise RuntimeError(
        f"não foi possível ler a configuração ativa do motor: {last_error}"
    ) from last_error


def _runtime_code_digest(root: Path) -> str:
    files = []
    for relative_path in RUNTIME_CODE_FILES:
        try:
            digest = hashlib.sha256((root / relative_path).read_bytes()).hexdigest()
        except OSError as exc:
            raise RuntimeError(
                f"não foi possível ler o código do motor: {relative_path}"
            ) from exc
        files.append({"path": relative_path, "sha256": digest})
    return _sha256_json(files)


def build_engine_revision(
    root: Path | None = None,
    *,
    db_path: str | Path | None = None,
    target: str = P_DYNAMIC_TARGET,
) -> dict[str, str]:
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
    revision["runtime_code_sha256"] = _runtime_code_digest(root)
    revision["model_config_sha256"] = _target_model_config(db_path, target=target)
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


def prediction_revision_fingerprint(value: object) -> str:
    """Identidade semântica da previsão, sem o commit meramente auditável."""
    revision = validate_engine_revision(value)
    return _sha256_json(
        {name: revision[name] for name in PREDICTION_REVISION_FIELDS}
    )
