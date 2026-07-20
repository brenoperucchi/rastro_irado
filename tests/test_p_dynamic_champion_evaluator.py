"""Regressões do ledger e torneio champion-challenger do P Dinâmico."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.compare_p_dynamic_parity import (  # noqa: E402
    METHODOLOGY_VERSION,
    capture_session_status,
    current_engine_revision,
    main as capture_main,
    normalize_series,
)
from scripts.evaluate_p_dynamic_champions import (  # noqa: E402
    LedgerSession,
    _with_causal_climatology,
    evaluate_champions,
    load_ledger_sessions,
)


def _row(timestamp, p_up, *, win_open=100.0, win_current=110.0, **extra):
    return {
        "timestamp": timestamp,
        "p_up": p_up,
        "win_open": win_open,
        "win_current": win_current,
        "is_ghost": False,
        "is_preview": False,
        **extra,
    }


def _session_grid(date_prefix, p_up=60.0, *, brt_offset_h=6, count=107, **extra):
    """Grade de 5min a partir de BRT 09:00 no eixo do provedor.

    Uma fonte só conta como fechada se abriu no pregão e cobriu a sessão --
    fixture de uma barra só representaria um feed degradado, que é exatamente
    o que o gate de cobertura passou a recusar.
    """
    rows = []
    for index in range(count):
        total = index * 5
        hour = 9 + brt_offset_h + total // 60
        rows.append(
            _row(f"{date_prefix}T{hour:02d}:{total % 60:02d}:00Z", p_up, **extra)
        )
    return rows


def _add_auditable_raw(bundle, manifest):
    """Acrescenta o cru verificável exigido dos bundles da metodologia 2."""
    raw_dir = bundle / "raw"
    raw_dir.mkdir(exist_ok=True)
    entries = {}
    for name in ("miqueias", "v1", "v2"):
        payload = (bundle / manifest["files"][name]).read_bytes()
        raw_path = raw_dir / f"{name}.json.gz"
        with gzip.open(raw_path, "wb") as handle:
            handle.write(payload)
        entries[name] = {
            "file": f"raw/{name}.json.gz",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
    manifest["session"]["raw_archive_complete"] = True
    manifest["raw"] = entries
    manifest.setdefault(
        "engine_revision",
        {
            "git_commit": "a" * 40,
            "engine_sha256": "b" * 64,
            "kalman_sha256": "c" * 64,
        },
    )
    return manifest


def test_status_exige_barra_real_ate_1750_brt():
    # Abre 09:00 mas encerra 14:00: cobertura não substitui fechamento.
    incomplete = normalize_series(
        _session_grid("2026-07-16", count=61), value_fields=("p_up",)
    )
    complete = normalize_series(_session_grid("2026-07-16"), value_fields=("p_up",))

    assert capture_session_status(incomplete, brt_offset_h=6)["closed"] is False
    status = capture_session_status(complete, brt_offset_h=6)
    assert status["closed"] is True
    assert status["last_operational_brt"] == "17:50"


def test_status_exige_abertura_e_cobertura_alem_do_horario_final():
    """Achado crítico do painel: encerrar tarde não é o mesmo que ter coberto a
    sessão. Uma barra única às 17:50 pontuaria quase como oráculo no Brier."""
    single_late = normalize_series(
        [_row("2026-07-16T23:50:00Z", 60.0)], value_fields=("p_up",)
    )
    afternoon_only = normalize_series(
        _session_grid("2026-07-16")[-30:], value_fields=("p_up",)
    )

    assert capture_session_status(single_late, brt_offset_h=6)["closed"] is False
    assert capture_session_status(afternoon_only, brt_offset_h=6)["closed"] is False
    assert (
        capture_session_status(
            normalize_series(_session_grid("2026-07-16"), value_fields=("p_up",)),
            brt_offset_h=6,
            min_operational_rows=200,
        )["closed"]
        is False
    )


def test_captura_usa_offset_sazonal_quando_documento_local_nao_informa_fuso(tmp_path):
    """Em janeiro, 22:55 no eixo Tickmill corresponde a 17:55 BRT (+5h)."""
    public = tmp_path / "public.json"
    v1 = tmp_path / "v1.json"
    candidate = tmp_path / "v2.json"
    output = tmp_path / "report.json"
    captures = tmp_path / "captures"
    rows = _session_grid("2026-01-15", brt_offset_h=5, count=108)
    public.write_text(json.dumps(rows), encoding="utf-8")
    v1.write_text(json.dumps(rows), encoding="utf-8")
    candidate.write_text(json.dumps(rows), encoding="utf-8")

    capture_main([
        "--public-source", str(public),
        "--skip-local-api",
        "--candidate", f"v1={v1}",
        "--candidate", f"v2={candidate}",
        "--session-date", "2026-01-15",
        "--capture-dir", str(captures),
        "--output-json", str(output),
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    with open(os.path.join(report["capture_bundle"], "manifest.json"), encoding="utf-8") as source:
        manifest = json.load(source)
    assert manifest["session"]["brt_offset_h"] == 5
    assert manifest["session"]["closed"] is True
    assert manifest["session"]["sources"]["v2"]["last_operational_brt"] == "17:55"


def test_captura_e_loader_rejeitam_outcome_local_sem_fechamento(tmp_path):
    public = tmp_path / "public.json"
    candidate = tmp_path / "v2.json"
    output = tmp_path / "report.json"
    captures = tmp_path / "captures"
    public.write_text(
        json.dumps(_session_grid("2026-07-16", count=108)), encoding="utf-8"
    )
    candidate.write_text(
        json.dumps({
            "brt_offset_h": 6,
            "series": _session_grid("2026-07-16", count=61),
        }),
        encoding="utf-8",
    )

    complete = tmp_path / "v1.json"
    complete.write_text(
        json.dumps({"brt_offset_h": 6, "series": _session_grid("2026-07-16", count=108)}),
        encoding="utf-8",
    )
    capture_main([
        "--public-source", str(public),
        "--skip-local-api",
        "--candidate", f"v1={complete}",
        "--candidate", f"v2={candidate}",
        "--session-date", "2026-07-16",
        "--capture-dir", str(captures),
        "--output-json", str(output),
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    with open(os.path.join(report["capture_bundle"], "manifest.json"), encoding="utf-8") as source:
        manifest = json.load(source)
    sessions, audit = load_ledger_sessions(captures)

    assert manifest["session"]["sources"]["miqueias"]["closed"] is True
    assert manifest["session"]["sources"]["v2"]["closed"] is False
    assert manifest["session"]["closed"] is False
    assert sessions == []
    assert audit["incomplete_bundles"] == 1

    # Defesa em profundidade: mesmo um manifesto antigo/corrompido que alegue
    # fechamento não pode fazer o loader aceitar o outcome parcial.
    manifest["session"]["closed"] = True
    with open(os.path.join(report["capture_bundle"], "manifest.json"), "w", encoding="utf-8") as destination:
        json.dump(manifest, destination)
    sessions, audit = load_ledger_sessions(captures)
    assert sessions == []
    assert audit["invalid_bundles"] == 1
    assert "fontes sem fechamento operacional: v2" in audit["invalid_reasons"][0]


def test_captura_cria_manifest_preserva_envelope_e_tolera_gex_ausente(tmp_path):
    public_path = tmp_path / "miqueias.json"
    v1_path = tmp_path / "v1.json"
    v2_path = tmp_path / "v2.json"
    capture_dir = tmp_path / "ledger"
    output_path = tmp_path / "latest.json"
    public_path.write_text(
        json.dumps(_session_grid("2026-07-16", 70.0)), encoding="utf-8"
    )
    for path, probability in ((v1_path, 65.0), (v2_path, 75.0)):
        path.write_text(
            json.dumps(
                {
                    "session_date": "2026-07-16",
                    "target": "WIN$N",
                    "is_b3": True,
                    "brt_offset_h": 6,
                    "series": _session_grid(
                        "2026-07-16",
                        probability,
                        win_bar_open=108.0,
                        win_high=112.0,
                        win_low=107.0,
                        pair_z=2.1,
                        pair_signal="sell",
                        nwe_center_price=109.0,
                        nwe_direction="up",
                    ),
                }
            ),
            encoding="utf-8",
        )

    status = capture_main(
        [
            "--public-source",
            str(public_path),
            "--skip-local-api",
            "--candidate",
            f"v1={v1_path}",
            "--candidate",
            f"v2={v2_path}",
            "--session-date",
            "2026-07-16",
            "--capture-dir",
            str(capture_dir),
            "--output-json",
            str(output_path),
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    bundle = tmp_path / report["capture_bundle"].split(str(tmp_path) + os.sep)[-1]
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    stored_v2 = json.loads((bundle / "v2.json").read_text(encoding="utf-8"))

    assert status == 0
    assert manifest["schema_version"] == 2
    assert manifest["methodology_version"] == METHODOLOGY_VERSION
    assert manifest["engine_revision"] == current_engine_revision()
    assert manifest["session"]["closed"] is True
    assert manifest["gex"]["status"] == "unavailable"
    assert stored_v2["brt_offset_h"] == 6
    assert stored_v2["series"][0]["pair_z"] == 2.1
    assert stored_v2["series"][0]["nwe_center_price"] == 109.0
    assert stored_v2["series"][0]["win_bar_open"] == 108.0
    assert stored_v2["series"][0]["win_high"] == 112.0
    assert stored_v2["series"][0]["win_low"] == 107.0


def test_captura_preserva_gex_mid_e_walls(tmp_path):
    public = tmp_path / "public.json"
    candidate = tmp_path / "v2.json"
    gex = tmp_path / "gex.json"
    output = tmp_path / "report.json"
    captures = tmp_path / "captures"
    row = _row("2026-07-16T23:50:00Z", 60.0)
    public.write_text(json.dumps([row]), encoding="utf-8")
    candidate.write_text(
        json.dumps({"brt_offset_h": 6, "series": [row]}), encoding="utf-8"
    )
    gex.write_text(
        json.dumps(
            {
                "active": True,
                "as_of": "2026-07-15",
                "gamma_flip": 100.0,
                "walls": [
                    {"type": "wall", "price": 99.0},
                    {"type": "mid_wall", "price": 100.0},
                ],
            }
        ),
        encoding="utf-8",
    )

    capture_main(
        [
            "--public-source",
            str(public),
            "--skip-local-api",
            "--candidate",
            f"v2={candidate}",
            "--gex-source",
            str(gex),
            "--session-date",
            "2026-07-16",
            "--capture-dir",
            str(captures),
            "--output-json",
            str(output),
        ]
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    manifest_path = os.path.join(report["capture_bundle"], "manifest.json")
    with open(manifest_path, encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)

    assert manifest["gex"]["status"] == "captured"
    assert manifest["gex"]["wall_count"] == 1
    assert manifest["gex"]["mid_wall_count"] == 1


def test_avaliador_bloqueia_vencedor_abaixo_do_gate():
    session = LedgerSession(
        session_date="2026-07-16",
        actual_up=True,
        forecasts={"miqueias": [0.7], "v1": [0.6], "v2": [0.8]},
    )

    report = evaluate_champions([session], min_sessions=20, bootstrap_iterations=100)

    assert report["status"] == "INCONCLUSIVE"
    assert report["quality_winner"] is None
    assert report["common_sessions"] == 1
    assert report["tactical_gate"]["status"] == "NOT_EVALUATED"


def test_baseline_climatologico_usa_somente_sessoes_anteriores():
    sessions = [
        LedgerSession("2026-07-15", True, {"v2": [0.7]}),
        LedgerSession("2026-07-16", False, {"v2": [0.3]}),
    ]

    augmented = _with_causal_climatology(sessions)

    assert augmented[0].forecasts["baseline_climatology"] == [0.5]
    assert augmented[1].forecasts["baseline_climatology"] == [2 / 3]


def test_avaliador_promove_apenas_vencedor_significante_no_bootstrap():
    sessions = []
    for index in range(60):
        actual_up = index % 2 == 0
        sessions.append(
            LedgerSession(
                session_date=f"s{index:02d}",
                actual_up=actual_up,
                forecasts={
                    "miqueias": [0.6 if actual_up else 0.4],
                    "v1": [0.55 if actual_up else 0.45],
                    "v2": [0.85 if actual_up else 0.15],
                },
            )
        )

    report = evaluate_champions(
        sessions,
        min_sessions=60,
        bootstrap_iterations=1_000,
        seed=7,
    )

    assert report["status"] == "WINNER"
    assert report["quality_winner"] == "v2"
    assert report["ranking_by_brier"][0] == "v2"
    assert all(comparison["ci_high"] < 0 for comparison in report["winner_tests"])


def test_loader_ignora_barras_de_sessao_estrangeira_no_bundle(tmp_path):
    """Defesa em profundidade: o bundle pode ser antigo (gravado antes do
    filtro por sessão BRT) ou ter barra estrangeira marcada como operacional.
    O avaliador não pode pontuar barra de outra sessão em Brier/log-loss nem
    deixá-la definir o outcome do WIN.

    Com brt_offset_h=6, UTC 2026-07-16T00:00 é BRT 18:00 de 15/07 -- sessão
    anterior. Se ela entrar, o outcome usaria win_open dela (100 -> alta) em
    vez do win_open real da sessão (110 -> baixa).
    """
    bundle = tmp_path / "2026-07-16" / "capture"
    bundle.mkdir(parents=True)
    foreign = _row("2026-07-16T00:00:00Z", 90.0, win_open=100.0, win_current=100.0)
    session = _session_grid("2026-07-16", 40.0, win_open=110.0, win_current=105.0)
    for name in ("miqueias", "v1", "v2"):
        (bundle / f"{name}.json").write_text(
            json.dumps({"brt_offset_h": 6, "series": [foreign, *session]}),
            encoding="utf-8",
        )
    manifest = _add_auditable_raw(
        bundle,
        {
            "schema_version": 2,
            "methodology_version": METHODOLOGY_VERSION,
            "session_date": "2026-07-16",
            "session": {"closed": True},
            "models": ["miqueias", "v1", "v2"],
            "files": {
                "miqueias": "miqueias.json",
                "v1": "v1.json",
                "v2": "v2.json",
            },
        },
    )
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    sessions, audit = load_ledger_sessions(tmp_path)

    assert audit["invalid_bundles"] == 0
    assert len(sessions) == 1
    assert len(sessions[0].forecasts["v2"]) == len(session)
    assert 0.9 not in sessions[0].forecasts["v2"]
    assert sessions[0].actual_up is False


def test_loader_isola_modelo_ruim_sem_descartar_a_sessao_inteira(tmp_path):
    """Um challenger esporádico (ex.: miqueias_static de uma rodada manual) sem
    barras da sessão não pode derrubar miqueias/v1/v2 perfeitamente válidos --
    raio de explosão maior que o necessário custaria sessões do gate de 60."""
    bundle = tmp_path / "2026-07-16" / "capture"
    bundle.mkdir(parents=True)
    session = _session_grid("2026-07-16", 40.0, win_open=110.0, win_current=105.0)
    for name in ("miqueias", "v1", "v2"):
        (bundle / f"{name}.json").write_text(
            json.dumps({"brt_offset_h": 6, "series": session}), encoding="utf-8"
        )
    # O challenger tem uma barra da sessão, mas cobertura insuficiente. Ele não
    # pode invalidar o trio oficial nem entrar no placar do torneio.
    (bundle / "extra.json").write_text(
        json.dumps({"brt_offset_h": 6, "series": [session[-1]]}),
        encoding="utf-8",
    )
    manifest = _add_auditable_raw(
        bundle,
        {
            "schema_version": 2,
            "methodology_version": METHODOLOGY_VERSION,
            "session_date": "2026-07-16",
            "session": {"closed": True},
            "models": ["miqueias", "v1", "v2", "extra"],
            "files": {
                "miqueias": "miqueias.json",
                "v1": "v1.json",
                "v2": "v2.json",
                "extra": "extra.json",
            },
        },
    )
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    sessions, audit = load_ledger_sessions(tmp_path)

    assert len(sessions) == 1
    assert sorted(sessions[0].forecasts) == ["miqueias", "v1", "v2"]
    assert audit["invalid_bundles"] == 0


def test_avaliador_nunca_consome_o_payload_cru(tmp_path):
    """O cru existe só para auditoria/reprocessamento quando a fonte rolling
    sumir. Se o avaliador o lesse, barras já descartadas voltariam ao Brier.
    O cru aqui diverge de propósito (p_up 99/1 e uma barra de outra sessão):
    se qualquer um desses números aparecer no forecast, houve vazamento."""
    bundle = tmp_path / "2026-07-16" / "capture"
    (bundle / "raw").mkdir(parents=True)
    session = _session_grid("2026-07-16", 40.0, win_open=110.0, win_current=105.0)
    poisoned = [
        _row("2026-07-10T15:00:00Z", 99.0, win_open=1.0, win_current=999.0),
        *session,
        _row("2026-07-16T23:55:00Z", 1.0, win_open=110.0, win_current=1.0),
    ]
    raw_entries = {}
    for name in ("miqueias", "v1", "v2"):
        (bundle / f"{name}.json").write_text(
            json.dumps({"brt_offset_h": 6, "series": session}), encoding="utf-8"
        )
        payload = json.dumps({"brt_offset_h": 6, "series": poisoned}).encode("utf-8")
        with gzip.open(bundle / "raw" / f"{name}.json.gz", "wb") as handle:
            handle.write(payload)
        raw_entries[name] = {
            "file": f"raw/{name}.json.gz",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "source": "http://example.invalid",
            "captured_at": "2026-07-16T20:56:00+00:00",
            "eligible": True,
        }
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "methodology_version": METHODOLOGY_VERSION,
                "session_date": "2026-07-16",
                "session": {"closed": True, "raw_archive_complete": True},
                "models": ["miqueias", "v1", "v2"],
                "files": {
                    "miqueias": "miqueias.json",
                    "v1": "v1.json",
                    "v2": "v2.json",
                },
                "raw": raw_entries,
                "engine_revision": {
                    "git_commit": "a" * 40,
                    "engine_sha256": "b" * 64,
                    "kalman_sha256": "c" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    sessions, audit = load_ledger_sessions(tmp_path)

    assert len(sessions) == 1
    assert len(sessions[0].forecasts["v2"]) == len(session)
    assert set(sessions[0].forecasts["v2"]) == {0.4}
    assert set(sessions[0].forecasts["miqueias"]) == {0.4}
    # win_current do cru (1.0 vs win_open 110) inverteria o outcome.
    assert sessions[0].actual_up is False


def test_outcome_ignora_after_market_da_propria_sessao(tmp_path):
    """O desfecho tem que usar a mesma janela de pregão da métrica.

    O collector coleta até 18:10 BRT com margem (backend/workers/collector.py),
    então ~844 sessões do banco de produção têm barra WIN depois das 18:00. Essas
    barras têm a mesma DATA BRT da sessão -- logo entram no bundle -- mas ficam
    fora da janela de pregão e por isso não são pontuadas. Se o outcome as ler,
    o rótulo de verdade é fixado por um preço que nenhuma barra pontuada viu:
    medido no banco real, isso inverte o desfecho em ~3,6% das sessões, e
    justamente nos dias de menor |close-open|, que são onde os modelos discordam.
    """
    bundle = tmp_path / "2026-07-16" / "capture"
    bundle.mkdir(parents=True)
    # Pregão regular fecha em BAIXA (abre 110, fecha 105).
    session = _session_grid("2026-07-16", 40.0, win_open=110.0, win_current=105.0)
    # After-market (BRT 18:00-18:20 = rótulo 00:00-00:20 de 17/07) sobe acima da
    # abertura: se contar, o desfecho vira ALTA.
    after = [
        _row(f"2026-07-17T00:{minute:02d}:00Z", 40.0, win_open=110.0, win_current=115.0)
        for minute in (0, 5, 10, 15, 20)
    ]
    for name in ("miqueias", "v1", "v2"):
        (bundle / f"{name}.json").write_text(
            json.dumps({"brt_offset_h": 6, "series": [*session, *after]}),
            encoding="utf-8",
        )
    manifest = _add_auditable_raw(
        bundle,
        {
            "schema_version": 2,
            "methodology_version": METHODOLOGY_VERSION,
            "session_date": "2026-07-16",
            "session": {"closed": True},
            "models": ["miqueias", "v1", "v2"],
            "files": {
                "miqueias": "miqueias.json",
                "v1": "v1.json",
                "v2": "v2.json",
            },
        },
    )
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    sessions, audit = load_ledger_sessions(tmp_path)

    assert len(sessions) == 1
    assert len(sessions[0].forecasts["v2"]) == len(session)
    assert sessions[0].actual_up is False


def test_intersecao_tem_piso_proprio_e_e_registrada(tmp_path):
    """Três fontes individualmente elegíveis podem produzir uma sessão de baixa
    informação: cada uma cobre 98+ barras, mas com gaps DISJUNTOS a interseção
    despenca. Sem piso próprio, essa sessão pesaria igual a uma íntegra no gate
    de 60. O manifesto tem de registrar o tamanho, o maior gap e os extremos
    efetivamente pontuados."""
    public = tmp_path / "public.json"
    v1 = tmp_path / "v1.json"
    v2 = tmp_path / "v2.json"
    captures = tmp_path / "captures"
    full = _session_grid("2026-07-16", 50.0, count=108)
    # Cada fonte perde 10 barras em faixas disjuntas: todas ficam com 98
    # (elegíveis), mas a interseção cai para 78.
    public.write_text(
        json.dumps(full[:20] + full[30:]), encoding="utf-8"
    )
    v1.write_text(
        json.dumps({"brt_offset_h": 6, "series": full[:40] + full[50:]}),
        encoding="utf-8",
    )
    v2.write_text(
        json.dumps({"brt_offset_h": 6, "series": full[:60] + full[70:]}),
        encoding="utf-8",
    )

    status = capture_main([
        "--public-source", str(public),
        "--skip-local-api",
        "--candidate", f"v1={v1}",
        "--candidate", f"v2={v2}",
        "--session-date", "2026-07-16",
        "--capture-dir", str(captures),
    ])

    manifest = json.loads(
        next(captures.glob("2026-07-16/*/manifest.json")).read_text(encoding="utf-8")
    )
    intersection = manifest["session"]["intersection"]

    # Cada fonte passa sozinha...
    assert all(
        source["operational_rows"] == 98
        for source in manifest["session"]["sources"].values()
    )
    # ...mas a sessão não, porque a interseção não sustenta a apuração.
    assert intersection["rows"] == 78
    assert intersection["min_rows"] == 98
    assert intersection["sufficient"] is False
    # 10 barras ausentes = 11 intervalos de 5min entre as barras que sobraram.
    assert intersection["max_gap_minutes"] == 55
    assert intersection["first_scored_brt"] == "09:00"
    assert intersection["last_scored_brt"] == "17:55"
    assert manifest["session"]["closed"] is False
    assert status != 0

    sessions, _ = load_ledger_sessions(captures)
    assert sessions == []


def test_outcome_nao_depende_de_qual_fonte_local_esta_mais_completa(tmp_path):
    """v2 era preferido incondicionalmente para formar o desfecho. Com v2
    fechando 17:50 e v1 fechando 17:55 -- ambos elegíveis -- a mesma sessão
    rendia rótulos diferentes conforme qual documento fosse escolhido: 8 de
    1253 sessões no banco de produção. O rótulo passa a sair da última barra
    COMUM às fontes locais."""
    bundle = tmp_path / "2026-07-16" / "capture"
    bundle.mkdir(parents=True)
    full = _session_grid("2026-07-16", 50.0, count=108, win_open=110.0, win_current=105.0)
    # A barra extra de v1 (17:55) sobe acima da abertura; a comum (17:50) não.
    v1_series = [*full[:-1], _row("2026-07-16T23:55:00Z", 50.0, win_open=110.0, win_current=120.0)]
    (bundle / "v1.json").write_text(
        json.dumps({"brt_offset_h": 6, "series": v1_series}), encoding="utf-8"
    )
    for name in ("miqueias", "v2"):
        (bundle / f"{name}.json").write_text(
            json.dumps({"brt_offset_h": 6, "series": full[:-1]}), encoding="utf-8"
        )
    manifest = _add_auditable_raw(
        bundle,
        {
            "schema_version": 2,
            "methodology_version": METHODOLOGY_VERSION,
            "session_date": "2026-07-16",
            "session": {"closed": True},
            "models": ["miqueias", "v1", "v2"],
            "files": {
                "miqueias": "miqueias.json",
                "v1": "v1.json",
                "v2": "v2.json",
            },
        },
    )
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    sessions, audit = load_ledger_sessions(tmp_path)

    assert len(sessions) == 1
    assert sessions[0].actual_up is False
    assert audit["outcome_timestamps"]["2026-07-16"].endswith("23:50:00+00:00")


def test_loader_recusa_bundle_de_metodologia_futura(tmp_path):
    """A guarda era unidirecional: bundle gravado sob régua mais NOVA era
    agregado como se fosse desta. Um rollback só do avaliador misturaria épocas."""
    for version, folder in ((1, "antigo"), (METHODOLOGY_VERSION + 1, "futuro")):
        bundle = tmp_path / folder / "capture"
        bundle.mkdir(parents=True)
        (bundle / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "methodology_version": version,
                    "session_date": "2026-07-16",
                    "session": {"closed": True},
                    "models": [],
                    "files": {},
                }
            ),
            encoding="utf-8",
        )

    sessions, audit = load_ledger_sessions(tmp_path)

    assert sessions == []
    assert audit["superseded_bundles"] == 1
    assert audit["foreign_version_bundles"] == 1


def test_cobertura_exige_slots_canonicos_e_nao_contagem_bruta(tmp_path):
    """Contar timestamps não basta: 98 barras publicadas de minuto em minuto no
    fim do pregão satisfazem o piso e ainda deixam um buraco de ~7h no meio da
    sessão. A cobertura tem de ser medida contra a grade M5 canônica."""
    import datetime as dt

    from scripts.compare_p_dynamic_parity import session_intersection_stats

    rows = [_row("2026-07-16T15:00:00Z", 50.0)]
    base = dt.datetime(2026, 7, 16, 23, 55)
    rows += [
        _row((base - dt.timedelta(minutes=97 - index)).strftime("%Y-%m-%dT%H:%M:00Z"), 50.0)
        for index in range(97)
    ]
    points = normalize_series(rows, value_fields=("p_up",))

    stats = session_intersection_stats(
        {"miqueias": points, "v1": points, "v2": points}, brt_offset_h=6
    )

    assert stats["rows"] == 98
    assert stats["canonical_slots_covered"] < 98
    assert stats["sufficient"] is False


def test_loader_exige_os_tres_participantes_do_torneio(tmp_path):
    """O capturador exige miqueias/v1/v2; o avaliador aceitava bundle sem v2 e
    contava como sessão válida do gate de 60."""
    bundle = tmp_path / "2026-07-16" / "capture"
    bundle.mkdir(parents=True)
    full = _session_grid("2026-07-16", 50.0, count=108, win_open=110.0, win_current=105.0)
    for name in ("miqueias", "v1"):
        (bundle / f"{name}.json").write_text(
            json.dumps({"brt_offset_h": 6, "series": full}), encoding="utf-8"
        )
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "methodology_version": METHODOLOGY_VERSION,
                "session_date": "2026-07-16",
                "session": {"closed": True},
                "models": ["miqueias", "v1"],
                "files": {"miqueias": "miqueias.json", "v1": "v1.json"},
            }
        ),
        encoding="utf-8",
    )

    sessions, audit = load_ledger_sessions(tmp_path)

    assert sessions == []
    assert audit["invalid_bundles"] == 1


def test_rotulo_de_mercado_nao_depende_de_previsao_nem_escolhe_fonte(tmp_path):
    """Duas invariantes do desfecho:

    (a) o rótulo é do MERCADO -- não pode mudar porque uma previsão está
        ausente numa barra cujo PREÇO está intacto;
    (b) se v1 e v2 discordarem do preço na mesma barra, isso é corrupção de
        dado e tem de falhar fechado, não escolher uma delas em silêncio.
    """
    from scripts.evaluate_p_dynamic_champions import _actual_outcome

    full = _session_grid("2026-07-16", 50.0, count=108, win_open=110.0, win_current=105.0)
    full[-1] = {**full[-1], "win_current": 120.0}
    document = lambda rows: {"brt_offset_h": 6, "series": rows}

    without_forecast = [dict(row) for row in full]
    without_forecast[-1] = {
        key: value for key, value in without_forecast[-1].items() if key != "p_up"
    }

    with_all, _ = _actual_outcome(
        {"v1": document(full), "v2": document(full)}, brt_offset_h=6
    )
    without, _ = _actual_outcome(
        {"v1": document(without_forecast), "v2": document(without_forecast)},
        brt_offset_h=6,
    )
    assert with_all is True
    assert without == with_all

    divergent = [dict(row) for row in full]
    divergent[-1] = {**divergent[-1], "win_current": 90.0}
    with pytest.raises(ValueError, match="divergem"):
        _actual_outcome(
            {"v1": document(full), "v2": document(divergent)}, brt_offset_h=6
        )


def test_guarda_de_metodologia_recusa_versao_nao_inteira(tmp_path):
    """int(2.5) == 2 fazia um bundle de régua fracionária passar como versão 2."""
    for index, version in enumerate((2.5, "2", None, True)):
        bundle = tmp_path / f"b{index}" / "capture"
        bundle.mkdir(parents=True)
        (bundle / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "methodology_version": version,
                    "session_date": "2026-07-16",
                    "session": {"closed": True},
                    "models": [],
                    "files": {},
                }
            ),
            encoding="utf-8",
        )

    sessions, audit = load_ledger_sessions(tmp_path)

    assert sessions == []
    assert audit["selected_sessions"] == 0
    assert audit["foreign_version_bundles"] + audit["invalid_bundles"] == 4


def _forge_bundle(
    tmp_path,
    series,
    *,
    session_extra=None,
    raw=None,
    capture_name="capture",
    session_date="2026-07-16",
):
    """Monta um bundle à mão, incluindo campos que o capturador nunca gravaria.

    O avaliador não pode confiar no manifesto: bundle antigo, corrompido ou
    forjado tem de ser revalidado a partir das séries.
    """
    bundle = tmp_path / session_date / capture_name
    bundle.mkdir(parents=True, exist_ok=True)
    for name in ("miqueias", "v1", "v2"):
        (bundle / f"{name}.json").write_text(
            json.dumps({"brt_offset_h": 6, "series": series}), encoding="utf-8"
        )
    manifest = {
        "schema_version": 2,
        "methodology_version": METHODOLOGY_VERSION,
        "session_date": session_date,
        "session": {"closed": True, **(session_extra or {})},
        "models": ["miqueias", "v1", "v2"],
        "files": {
            "miqueias": "miqueias.json",
            "v1": "v1.json",
            "v2": "v2.json",
        },
    }
    if raw is None:
        _add_auditable_raw(bundle, manifest)
    else:
        manifest["raw"] = raw
    manifest.setdefault(
        "engine_revision",
        {
            "git_commit": "a" * 40,
            "engine_sha256": "b" * 64,
            "kalman_sha256": "c" * 64,
        },
    )
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle


def test_ingestao_recusa_manifesto_forjado_com_intersecao_insuficiente(tmp_path):
    """Manifesto alega closed=true com 98 timestamps, mas só ~21 slots M5 e um
    buraco de 439 min. O avaliador tem de rederivar a suficiência da interseção,
    não aceitar a contagem bruta nem a alegação do manifesto."""
    import datetime as dt

    series = [_row("2026-07-16T15:00:00Z", 50.0, win_open=110.0, win_current=105.0)]
    base = dt.datetime(2026, 7, 16, 23, 55)
    series += [
        _row(
            (base - dt.timedelta(minutes=97 - index)).strftime("%Y-%m-%dT%H:%M:00Z"),
            50.0,
            win_open=110.0,
            win_current=105.0,
        )
        for index in range(97)
    ]
    _forge_bundle(tmp_path, series)

    sessions, audit = load_ledger_sessions(tmp_path)

    # Rejeitado pelo gate por fonte, que também mede slots canônicos -- a
    # contagem bruta de 98 não compra elegibilidade em nenhuma das camadas.
    assert len(series) == 98
    assert sessions == []
    assert audit["invalid_bundles"] == 1


def test_ingestao_recusa_intersecao_insuficiente_com_fontes_elegiveis(tmp_path):
    """Exercita o gate da INTERSEÇÃO, não o por fonte: cada fonte cobre 98 dos
    108 slots (elegível sozinha), mas as lacunas são disjuntas e a interseção
    cai para 78. O avaliador tem de exigir intersection['sufficient'], não
    apenas computá-lo."""
    bundle = tmp_path / "2026-07-16" / "capture"
    bundle.mkdir(parents=True)
    full = _session_grid("2026-07-16", 50.0, count=108, win_open=110.0, win_current=105.0)
    holes = {"miqueias": (20, 30), "v1": (40, 50), "v2": (60, 70)}
    for name, (start, end) in holes.items():
        (bundle / f"{name}.json").write_text(
            json.dumps({"brt_offset_h": 6, "series": full[:start] + full[end:]}),
            encoding="utf-8",
        )
    manifest = _add_auditable_raw(
        bundle,
        {
            "schema_version": 2,
            "methodology_version": METHODOLOGY_VERSION,
            "session_date": "2026-07-16",
            "session": {"closed": True},
            "models": ["miqueias", "v1", "v2"],
            "files": {
                "miqueias": "miqueias.json",
                "v1": "v1.json",
                "v2": "v2.json",
            },
        },
    )
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    sessions, audit = load_ledger_sessions(tmp_path)

    assert sessions == []
    assert audit["invalid_bundles"] == 1
    assert "interseção" in audit["invalid_reasons"][0]


def test_ingestao_recusa_manifesto_sem_cru_arquivado(tmp_path):
    """Sem o cru não há evidência reprodutível da fonte rolling. Um manifesto
    que declara raw_archive_complete=false -- ou cujas entradas de cru carregam
    erro -- não pode ser ingerido, ainda que alegue closed=true."""
    series = _session_grid("2026-07-16", 50.0, count=108, win_open=110.0, win_current=105.0)
    _forge_bundle(
        tmp_path,
        series,
        session_extra={"raw_archive_complete": False},
        raw={
            name: {"source": "http://x", "error": "OSError: No space left on device"}
            for name in ("miqueias", "v1", "v2")
        },
    )

    sessions, audit = load_ledger_sessions(tmp_path)

    assert sessions == []
    assert audit["invalid_bundles"] == 1
    assert "cru" in audit["invalid_reasons"][0]


def test_ingestao_recusa_manifesto_que_omite_o_cru_arquivado(tmp_path):
    """O contrato do cru é fail-closed: omitir o campo não pode ser mais
    permissivo que declarar explicitamente uma falha de arquivamento."""
    series = _session_grid("2026-07-16", 50.0, count=108, win_open=110.0, win_current=105.0)
    bundle = _forge_bundle(tmp_path, series)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["session"].pop("raw_archive_complete")
    manifest.pop("raw")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    sessions, audit = load_ledger_sessions(tmp_path)

    assert sessions == []
    assert audit["invalid_bundles"] == 1
    assert "cru" in audit["invalid_reasons"][0]


def test_ingestao_recusa_manifesto_sem_revisao_do_motor(tmp_path):
    """Sem commit+hashes não há como saber se a previsão foi produzida pelo
    mesmo motor que as demais sessões do torneio."""
    series = _session_grid(
        "2026-07-16", 50.0, count=108, win_open=110.0, win_current=105.0
    )
    bundle = _forge_bundle(tmp_path, series)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("engine_revision")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    sessions, audit = load_ledger_sessions(tmp_path)

    assert sessions == []
    assert audit["invalid_bundles"] == 1
    assert "revisão verificável" in audit["invalid_reasons"][0]


def test_ingestao_recusa_ledger_com_revisoes_do_motor_mistas(tmp_path):
    """A metodologia igual não autoriza misturar p_up gerados antes/depois de
    uma alteração do Kalman. O avaliador deve parar, não escolher um grupo."""
    series = _session_grid(
        "2026-07-16", 50.0, count=108, win_open=110.0, win_current=105.0
    )
    _forge_bundle(tmp_path, series, capture_name="revision-a")
    series_b = _session_grid(
        "2026-07-17", 50.0, count=108, win_open=110.0, win_current=105.0
    )
    bundle_b = _forge_bundle(
        tmp_path,
        series_b,
        capture_name="revision-b",
        session_date="2026-07-17",
    )
    manifest_path = bundle_b / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["engine_revision"]["engine_sha256"] = "d" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    sessions, audit = load_ledger_sessions(tmp_path)

    assert sessions == []
    assert audit["mixed_engine_revision_bundles"] == 2
    assert len(audit["engine_revision_groups"]) == 2
    assert "múltiplas revisões" in audit["invalid_reasons"][-1]


def test_ingestao_recusa_outcome_sem_preco_em_uma_fonte_local(tmp_path):
    """Uma fonte local sem preço operacional não pode desaparecer do
    desfecho: aceitar apenas v2 reintroduziria o rótulo dependente da fonte
    mais completa que a correção anterior removeu."""
    series = _session_grid("2026-07-16", 50.0, count=108, win_open=110.0, win_current=105.0)
    bundle = _forge_bundle(tmp_path, series)
    v1_path = bundle / "v1.json"
    v1_document = json.loads(v1_path.read_text(encoding="utf-8"))
    for row in v1_document["series"]:
        row["win_open"] = None
        row["win_current"] = None
    v1_path.write_text(json.dumps(v1_document), encoding="utf-8")

    # O arquivo cru continua sendo prova consistente desta entrada forjada;
    # o ponto do teste é a revalidação do outcome, não a integridade do arquivo.
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    _add_auditable_raw(bundle, manifest)
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    sessions, audit = load_ledger_sessions(tmp_path)

    assert sessions == []
    assert audit["invalid_bundles"] == 1
    assert "fontes locais sem preço operacional" in audit["invalid_reasons"][0]
    assert "v1" in audit["invalid_reasons"][0]


def test_cobertura_por_fonte_tambem_usa_slots_canonicos(tmp_path):
    """O gate por fonte usava contagem bruta: 98 barras fora da grade M5
    passavam. Capturador e avaliador têm de medir a mesma coisa."""
    import datetime as dt

    rows = [_row("2026-07-16T15:00:00Z", 50.0)]
    base = dt.datetime(2026, 7, 16, 23, 55)
    rows += [
        _row((base - dt.timedelta(minutes=97 - index)).strftime("%Y-%m-%dT%H:%M:00Z"), 50.0)
        for index in range(97)
    ]

    status = capture_session_status(
        normalize_series(rows, value_fields=("p_up",)),
        brt_offset_h=6,
        min_operational_rows=98,
    )

    assert status["operational_rows"] == 98
    assert status["canonical_slots_covered"] < 98
    assert status["closed"] is False


def test_loader_ignora_bundle_de_sessao_incompleta(tmp_path):
    bundle = tmp_path / "2026-07-16" / "capture"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "methodology_version": METHODOLOGY_VERSION,
                "session_date": "2026-07-16",
                "session": {"closed": False},
                "files": {},
            }
        ),
        encoding="utf-8",
    )

    sessions, audit = load_ledger_sessions(tmp_path)

    assert sessions == []
    assert audit["incomplete_bundles"] == 1
