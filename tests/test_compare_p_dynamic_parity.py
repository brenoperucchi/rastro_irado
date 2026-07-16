"""Regressões da comparação caixa-preta do P Dinâmico do WIN."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.compare_p_dynamic_parity import (
    build_parity_report,
    load_json_source,
    main,
    normalize_series,
)


def _bar(timestamp, p_up, **extra):
    return {"timestamp": timestamp, "p_up": p_up, **extra}


def test_publico_replica_prioridade_do_bundle_p_up_v1_depois_p_up():
    points = normalize_series(
        [
            _bar("2026-07-16T15:00:00Z", 41.0, p_up_v1=61.0),
            _bar("2026-07-16T15:05:00Z", 42.0, p_up_v1=None),
        ],
        value_fields=("p_up_v1", "p_up"),
    )

    assert [point.value for point in points] == [61.0, 42.0]
    assert [point.value_field for point in points] == ["p_up_v1", "p_up"]


def test_alinhamento_trata_z_e_offset_utc_como_o_mesmo_instante():
    public = normalize_series(
        [_bar("2026-07-16T15:00:00Z", 60.0)],
        value_fields=("p_up_v1", "p_up"),
    )
    local = normalize_series(
        [_bar("2026-07-16T15:00:00+00:00", 58.0)],
        value_fields=("p_up",),
    )

    report = build_parity_report(public, {"v2": local}, tolerance=1.0)

    assert report["candidates"]["v2"]["all_bars"]["common_rows"] == 1
    assert report["candidates"]["v2"]["all_bars"]["first_divergence"] == {
        "timestamp": "2026-07-16T15:00:00+00:00",
        "reference": 60.0,
        "candidate": 58.0,
        "difference": -2.0,
        "absolute_difference": 2.0,
    }


def test_relatorio_calcula_cobertura_metricas_regime_e_primeira_divergencia():
    public = normalize_series(
        [
            _bar("2026-07-16T15:00:00Z", 30.0),
            _bar("2026-07-16T15:05:00Z", 50.0),
            _bar("2026-07-16T15:10:00Z", 70.0),
            _bar("2026-07-16T15:15:00Z", 65.0),
        ],
        value_fields=("p_up",),
    )
    local = normalize_series(
        [
            _bar("2026-07-16T15:00:00+00:00", 32.0),
            _bar("2026-07-16T15:05:00+00:00", 49.0),
            _bar("2026-07-16T15:10:00+00:00", 65.0),
        ],
        value_fields=("p_up",),
    )

    metrics = build_parity_report(public, {"v2": local}, tolerance=2.0)[
        "candidates"
    ]["v2"]["all_bars"]

    assert metrics["reference_rows"] == 4
    assert metrics["candidate_rows"] == 3
    assert metrics["common_rows"] == 3
    assert metrics["reference_coverage_pct"] == 75.0
    assert metrics["candidate_coverage_pct"] == 100.0
    assert metrics["mae"] == pytest.approx(8 / 3)
    assert metrics["max_absolute_difference"] == 5.0
    assert metrics["regime_concordance_pct"] == 100.0
    assert metrics["first_divergence"]["timestamp"] == "2026-07-16T15:10:00+00:00"
    assert metrics["correlation"] == pytest.approx(0.999847)


def test_subconjunto_operacional_remove_ghost_e_preview_dos_dois_lados():
    public = normalize_series(
        [
            _bar("2026-07-16T14:55:00Z", 45.0, is_ghost=True, is_preview=True),
            _bar("2026-07-16T15:00:00Z", 61.0, is_ghost=False, is_preview=False),
            _bar("2026-07-16T15:05:00Z", 62.0, is_ghost=False, is_preview=False),
        ],
        value_fields=("p_up",),
    )
    local = normalize_series(
        [
            _bar("2026-07-16T14:55:00Z", 20.0, is_ghost=True, is_preview=True),
            _bar("2026-07-16T15:00:00Z", 59.0, is_ghost=False, is_preview=False),
            _bar("2026-07-16T15:05:00Z", 62.0, is_ghost=False, is_preview=False),
        ],
        value_fields=("p_up",),
    )

    candidate = build_parity_report(public, {"v2": local})["candidates"]["v2"]

    assert candidate["all_bars"]["common_rows"] == 3
    assert candidate["operational_bars"]["common_rows"] == 2
    assert candidate["operational_bars"]["regime_concordance_pct"] == 50.0


def test_ranking_prefere_menor_mae_operacional():
    public = normalize_series(
        [_bar("2026-07-16T15:00:00Z", 60.0)], value_fields=("p_up",)
    )
    v1 = normalize_series(
        [_bar("2026-07-16T15:00:00Z", 59.0)], value_fields=("p_up",)
    )
    v2 = normalize_series(
        [_bar("2026-07-16T15:00:00Z", 50.0)], value_fields=("p_up",)
    )

    report = build_parity_report(public, {"v1": v1, "v2": v2})

    assert report["ranking_by_operational_mae"] == ["v1", "v2"]


def test_loader_aceita_lista_e_envelope_de_api(tmp_path):
    direct = tmp_path / "direct.json"
    envelope = tmp_path / "envelope.json"
    direct.write_text(json.dumps([_bar("2026-07-16T15:00:00Z", 50)]), encoding="utf-8")
    envelope.write_text(
        json.dumps({"series": [_bar("2026-07-16T15:00:00Z", 51)]}),
        encoding="utf-8",
    )

    assert load_json_source(str(direct))[0]["p_up"] == 50
    assert load_json_source(str(envelope))[0]["p_up"] == 51


def test_timestamp_sem_fuso_nao_casa_silenciosamente_com_timestamp_utc():
    public = normalize_series(
        [_bar("2026-07-16T15:00:00Z", 50.0)], value_fields=("p_up",)
    )
    local = normalize_series(
        [_bar("2026-07-16T15:00:00", 50.0)], value_fields=("p_up",)
    )

    with pytest.raises(ValueError, match="timestamps com e sem fuso"):
        build_parity_report(public, {"v2": local})


def test_cli_nao_confunde_candidato_mais_proximo_com_vencedor_de_qualidade(tmp_path):
    public = tmp_path / "public.json"
    candidate = tmp_path / "v2.json"
    output = tmp_path / "report.json"
    rows = [_bar("2026-07-16T15:00:00Z", 60.0)]
    public.write_text(json.dumps(rows), encoding="utf-8")
    candidate.write_text(json.dumps(rows), encoding="utf-8")

    status = main(
        [
            "--public-source",
            str(public),
            "--skip-local-api",
            "--candidate",
            f"v2={candidate}",
            "--output-json",
            str(output),
        ]
    )
    conclusion = json.loads(output.read_text(encoding="utf-8"))["conclusion"]

    assert status == 0
    assert conclusion["scope"] == "parity_only"
    assert conclusion["closest_candidate"] == "v2"
    assert conclusion["quality_winner"] is None
    assert "OOS" in conclusion["promotion_warning"]
