"""Challenger estático do P Dinâmico do Miqueias para WIN$N.

Este módulo não participa do ``P_up`` operacional. Ele permite comparar uma
hipótese estática auditável com v1, v2 e a série pública do Miqueias.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Mapping


MIQUEIAS_STATIC_SCHEMA_VERSION = 1
RETURN_UNIT = "percent"
NORMALIZATION = "ret/(100*sigma*sqrt(t_frac))"
DEFAULT_CONFIG_PATH = (
    Path(__file__).with_name("config") / "miqueias_static_win_2026-06-23.json"
)


@dataclass(frozen=True)
class MiqueiasStaticFactor:
    weight: float
    sigma: float


@dataclass(frozen=True)
class MiqueiasStaticConfig:
    target: str
    effective_from: str
    alpha: float
    intercept: float
    factors: dict[str, MiqueiasStaticFactor]


def _finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} precisa ser número JSON")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field} precisa ser finito")
    return numeric


def load_miqueias_static_config(document: object) -> MiqueiasStaticConfig:
    """Valida a calibração sem inferir unidade, normalização ou parâmetros."""
    if not isinstance(document, Mapping):
        raise ValueError("configuração miqueias_static precisa ser um objeto JSON")
    schema_version = document.get("schema_version")
    if type(schema_version) is not int or schema_version != MIQUEIAS_STATIC_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version precisa ser {MIQUEIAS_STATIC_SCHEMA_VERSION} para miqueias_static"
        )
    if document.get("name") != "miqueias_static":
        raise ValueError("name precisa ser 'miqueias_static'")
    if document.get("return_unit") != RETURN_UNIT:
        raise ValueError(f"return_unit precisa ser {RETURN_UNIT!r}")
    if document.get("normalization") != NORMALIZATION:
        raise ValueError(f"normalization precisa ser {NORMALIZATION!r}")

    target = document.get("target")
    if not isinstance(target, str) or not target:
        raise ValueError("target da configuração miqueias_static é obrigatório")
    effective_from = document.get("effective_from")
    if not isinstance(effective_from, str):
        raise ValueError("effective_from da configuração miqueias_static é obrigatório")
    try:
        date.fromisoformat(effective_from)
    except ValueError as exc:
        raise ValueError("effective_from precisa ser ISO YYYY-MM-DD") from exc

    raw_factors = document.get("factors")
    if not isinstance(raw_factors, Mapping) or not raw_factors:
        raise ValueError("factors da configuração miqueias_static é obrigatório")
    factors: dict[str, MiqueiasStaticFactor] = {}
    for name, raw_factor in raw_factors.items():
        if not isinstance(name, str) or not name:
            raise ValueError("todo fator miqueias_static precisa de nome")
        if not isinstance(raw_factor, Mapping):
            raise ValueError(f"fator {name} precisa ser um objeto com weight e sigma")
        weight = _finite_float(raw_factor.get("weight"), field=f"weight de {name}")
        sigma = _finite_float(raw_factor.get("sigma"), field=f"sigma de {name}")
        if sigma <= 0:
            raise ValueError(f"sigma de {name} precisa ser maior que zero")
        factors[name] = MiqueiasStaticFactor(weight=weight, sigma=sigma)

    return MiqueiasStaticConfig(
        target=target,
        effective_from=effective_from,
        alpha=_finite_float(document.get("alpha"), field="alpha"),
        intercept=_finite_float(document.get("intercept"), field="intercept"),
        factors=factors,
    )


def load_default_miqueias_static_config() -> MiqueiasStaticConfig:
    with DEFAULT_CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        return load_miqueias_static_config(json.load(config_file))


def _expit(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def _timestamp_date(timestamp: object) -> date:
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise ValueError("barra sem timestamp ISO válido")
    raw = timestamp.strip()
    try:
        return datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw).date()
    except ValueError as exc:
        raise ValueError(f"timestamp ISO inválido: {raw!r}") from exc


def build_miqueias_static_rows(
    rows: Iterable[Mapping[str, object]], config: MiqueiasStaticConfig,
) -> list[dict]:
    """Calcula a hipótese estática usando retorno percentual e ``sqrt(t)``.

    ``factors[*].ret`` é serializado pela API em porcentagem (``1.0`` = 1%).
    O motor converte para fração antes do z-score; esta função preserva a mesma
    unidade e normalização temporal. Barras sintéticas viram whitespace.
    """
    challenger_rows: list[dict] = []
    effective_date = date.fromisoformat(config.effective_from)
    for row_number, row in enumerate(rows, start=1):
        timestamp = row.get("timestamp")
        if _timestamp_date(timestamp) < effective_date:
            raise ValueError(
                f"barra {row_number} anterior à vigência da configuração "
                f"({config.effective_from})"
            )
        is_ghost = bool(row.get("is_ghost", False))
        is_preview = bool(row.get("is_preview", False))
        if is_ghost or is_preview:
            challenger_rows.append({
                "timestamp": timestamp,
                "p_up": None,
                "is_ghost": is_ghost,
                "is_preview": is_preview,
            })
            continue

        row_factors = row.get("factors")
        if not isinstance(row_factors, Mapping):
            raise ValueError(f"barra {row_number} sem objeto factors")
        configured_factors = set(config.factors)
        source_factors = set(row_factors)
        if source_factors != configured_factors:
            unconfigured = sorted(source_factors - configured_factors)
            absent = sorted(configured_factors - source_factors)
            details = []
            if unconfigured:
                details.append(f"sem configuração para {', '.join(unconfigured)}")
            if absent:
                details.append(f"ausentes da barra: {', '.join(absent)}")
            raise ValueError(
                f"barra {row_number} tem conjunto de fatores diferente da configuração "
                f"({'; '.join(details)})"
            )
        t_frac = _finite_float(row.get("t_frac"), field=f"t_frac da barra {row_number}")
        if not 0 < t_frac <= 1:
            raise ValueError(f"t_frac da barra {row_number} precisa estar entre 0 e 1")

        score = 0.0
        sqrt_t = math.sqrt(t_frac)
        for name, factor in config.factors.items():
            factor_row = row_factors.get(name)
            if not isinstance(factor_row, Mapping):
                raise ValueError(f"barra {row_number} sem fator {name}")
            return_percent = _finite_float(
                factor_row.get("ret"), field=f"ret de {name} na barra {row_number}"
            )
            return_fraction = return_percent / 100.0
            score += factor.weight * return_fraction / (factor.sigma * sqrt_t)

        challenger_rows.append({
            "timestamp": timestamp,
            "p_up": 100.0 * _expit(config.alpha * score + config.intercept),
            "is_ghost": is_ghost,
            "is_preview": is_preview,
        })
    if not challenger_rows:
        raise ValueError("fonte do challenger miqueias_static não contém barras")
    return challenger_rows


def describe_miqueias_static_config(config: MiqueiasStaticConfig) -> dict:
    return {
        "name": "miqueias_static",
        "schema_version": MIQUEIAS_STATIC_SCHEMA_VERSION,
        "target": config.target,
        "effective_from": config.effective_from,
        "return_unit": RETURN_UNIT,
        "normalization": NORMALIZATION,
        "alpha": config.alpha,
        "intercept": config.intercept,
        "factors": {
            name: {"weight": factor.weight, "sigma": factor.sigma}
            for name, factor in sorted(config.factors.items())
        },
        "limitations": [
            "static_calibration_only",
            "no_kalman_state_or_qr",
            "not_a_claim_of_v2_parity",
        ],
    }
