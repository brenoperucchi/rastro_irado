"""Regressões da comparação caixa-preta do P Dinâmico do WIN."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import os
import subprocess
import sys
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.compare_p_dynamic_parity as compare_p_dynamic_parity
from scripts.compare_p_dynamic_parity import (
    build_miqueias_static_rows,
    build_parity_report,
    load_json_document,
    load_miqueias_static_config,
    load_json_source,
    main,
    normalize_series,
)


class _FakeUrlopen:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, _FailingRead):
            return item
        return io.BytesIO(item)


class _FailingRead:
    """Simula uma resposta HTTP cujo .read() falha a meio da transferência
    (ex.: IncompleteRead por conexão truncada), em vez de falhar já na
    chamada de urlopen()."""

    def __init__(self, exc):
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        raise self._exc


class _RoutedFakeUrlopen:
    """Roteia por substring da URL -- usado para simular main() buscando
    v1 e v2 da API local com desfechos diferentes por versão."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.calls.append(url)
        for key, payload in self.routes.items():
            if key in url:
                if isinstance(payload, list):
                    payload = payload.pop(0)
                if isinstance(payload, Exception):
                    raise payload
                return io.BytesIO(payload)
        if "p-dynamic-runtime-revision" in url:
            return io.BytesIO(
                json.dumps(
                    {
                        "engine_revision": {
                            "git_commit": "a" * 40,
                            "engine_sha256": "b" * 64,
                            "kalman_sha256": "c" * 64,
                        }
                    }
                ).encode("utf-8")
            )
        raise AssertionError(f"URL inesperada no fake: {url}")


def _bar(timestamp, p_up, **extra):
    return {"timestamp": timestamp, "p_up": p_up, **extra}


def _miqueias_static_config(**overrides):
    config = {
        "schema_version": 1,
        "name": "miqueias_static",
        "target": "WIN$N",
        "effective_from": "2026-06-23",
        "return_unit": "percent",
        "normalization": "ret/(100*sigma*sqrt(t_frac))",
        "alpha": 2.0,
        "intercept": -0.2,
        "factors": {
            "wdo": {"weight": 0.5, "sigma": 0.1},
            "di1": {"weight": -0.2, "sigma": 0.2},
        },
    }
    config.update(overrides)
    return config


def test_challenger_estatico_usa_retorno_sigmas_e_parametros_declarados():
    """O challenger não pode reutilizar peso/z-score dinâmico do payload local."""
    config = load_miqueias_static_config(_miqueias_static_config())
    rows = [{
        "timestamp": "2026-07-16T15:00:00Z",
        "is_ghost": False,
        "is_preview": False,
        "factors": {
            "wdo": {"ret": 10.0, "weight": 999.0, "z_score": 999.0},
            "di1": {"ret": -20.0, "weight": 999.0, "z_score": 999.0},
        },
        "t_frac": 0.25,
    }]

    challenger = build_miqueias_static_rows(rows, config)

    expected_score = 0.5 * ((10.0 / 100) / (0.1 * 0.5)) - 0.2 * ((-20.0 / 100) / (0.2 * 0.5))
    expected_p_up = 100.0 / (1.0 + math.exp(-(2.0 * expected_score - 0.2)))
    assert challenger == [{
        "timestamp": "2026-07-16T15:00:00Z",
        "p_up": pytest.approx(expected_p_up),
        "is_ghost": False,
        "is_preview": False,
    }]


def test_challenger_estatico_falha_fechado_sem_sigma_ou_fator_da_barra():
    incomplete = _miqueias_static_config(
        factors={"wdo": {"weight": 0.5}},
    )
    with pytest.raises(ValueError, match="sigma"):
        load_miqueias_static_config(incomplete)

    config = load_miqueias_static_config(_miqueias_static_config())
    with pytest.raises(ValueError, match="di1"):
        build_miqueias_static_rows([{
            "timestamp": "2026-07-16T15:00:00Z",
            "factors": {"wdo": {"ret": 0.0}},
            "t_frac": 0.25,
        }], config)

    partial = load_miqueias_static_config({
        **_miqueias_static_config(),
        "factors": {"wdo": {"weight": 0.5, "sigma": 0.1}},
    })
    with pytest.raises(ValueError, match="sem configuração para di1"):
        build_miqueias_static_rows([{
            "timestamp": "2026-07-16T15:00:00Z",
            "factors": {"wdo": {"ret": 0.0}, "di1": {"ret": 0.0}},
            "t_frac": 0.25,
        }], partial)


def test_challenger_estatico_respeita_vigencia_e_recusa_fonte_vazia():
    config = load_miqueias_static_config(_miqueias_static_config())
    with pytest.raises(ValueError, match="anterior à vigência"):
        build_miqueias_static_rows([{
            "timestamp": "2026-06-20T15:00:00Z",
            "factors": {"wdo": {"ret": 0.0}, "di1": {"ret": 0.0}},
            "t_frac": 0.25,
        }], config)
    with pytest.raises(ValueError, match="não contém barras"):
        build_miqueias_static_rows([], config)


def test_challenger_estatico_recusa_strings_e_booleanos_em_configuracao_e_retorno():
    with pytest.raises(ValueError, match="schema_version"):
        load_miqueias_static_config(_miqueias_static_config(schema_version=True))
    with pytest.raises(ValueError, match="schema_version"):
        load_miqueias_static_config(_miqueias_static_config(schema_version=1.0))
    with pytest.raises(ValueError, match="número JSON"):
        load_miqueias_static_config(_miqueias_static_config(alpha=True))
    with pytest.raises(ValueError, match="número JSON"):
        load_miqueias_static_config(_miqueias_static_config(
            factors={
                "wdo": {"weight": "0.5", "sigma": 0.1},
                "di1": {"weight": -0.2, "sigma": 0.2},
            },
        ))

    config = load_miqueias_static_config(_miqueias_static_config())
    with pytest.raises(ValueError, match="número JSON"):
        build_miqueias_static_rows([{
            "timestamp": "2026-07-16T15:00:00Z",
            "factors": {"wdo": {"ret": True}, "di1": {"ret": 0.0}},
            "t_frac": 0.25,
        }], config)


def test_cli_importa_backend_quando_executado_fora_da_raiz(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "compare_p_dynamic_parity.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


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


def test_load_json_document_retenta_apos_falha_transitoria_de_rede():
    """rastro-irado-p-dynamic-ledger.timer roda 1x por dia útil (17:56 BRT)
    sem reagendamento para a mesma sessão -- antes desta correção, uma falha
    de rede isolada ao buscar a referência pública do Miqueias (main(),
    linha ~486) abortava main() sem gravar nenhum bundle para o dia,
    atrasando ainda mais o gate de 60 sessões do avaliador champion-challenger
    (IRAI-18). Só transporte é retentado; contrato/formato persistente
    continua falhando fechado (ver teste de esgotamento abaixo)."""
    fake = _FakeUrlopen([
        URLError("erro temporário de rede"),
        json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)]).encode("utf-8"),
    ])
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        document = load_json_document(
            "http://example.invalid/series.json", retry_delay=0,
        )
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    assert document[0]["p_up"] == 60.0
    assert fake.calls == 2


def test_load_json_document_retenta_payload_nao_json_transitorio():
    fake = _FakeUrlopen([
        b"<html>erro temporario</html>",
        json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)]).encode("utf-8"),
    ])
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        document = load_json_document(
            "http://example.invalid/series.json", retry_delay=0,
        )
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    assert document[0]["p_up"] == 60.0
    assert fake.calls == 2


def test_load_json_document_retenta_incomplete_read_transitorio():
    """IncompleteRead (conexão truncada a meio da leitura, ex.: manutenção
    pontual do Firebase) é OSError? Não -- é http.client.HTTPException, uma
    hierarquia separada. Antes desta correção, o except (OSError,
    JSONDecodeError) da versão anterior não capturava IncompleteRead, então
    esse erro transitório específico propagava sem retry."""
    fake = _FakeUrlopen([
        _FailingRead(IncompleteRead(b"parcial", expected=10)),
        json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)]).encode("utf-8"),
    ])
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        document = load_json_document(
            "http://example.invalid/series.json", retry_delay=0,
        )
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    assert document[0]["p_up"] == 60.0
    assert fake.calls == 2


def test_load_json_document_retenta_erro_de_decodificacao_transitorio():
    """Payload truncado/corrompido em bytes inválidos de UTF-8 levanta
    UnicodeDecodeError (subclasse de ValueError, não de OSError) -- também
    não era coberto pelo except (OSError, JSONDecodeError) anterior."""
    fake = _FakeUrlopen([
        b"\xff\xfe\x00\x01 payload corrompido",
        json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)]).encode("utf-8"),
    ])
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        document = load_json_document(
            "http://example.invalid/series.json", retry_delay=0,
        )
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    assert document[0]["p_up"] == 60.0
    assert fake.calls == 2


def test_load_json_document_nao_retenta_http_error_4xx():
    """Um HTTPError 4xx (ex.: 404 -- URL/endpoint errado) é erro de
    contrato do cliente, não falha transitória de transporte: retentar não
    vai corrigir a URL. Antes desta correção, HTTPError é subclasse de
    OSError e caía no retry genérico, desperdiçando ~10s (2 esperas de
    retry_delay) num erro que nunca teria sucesso."""
    fake = _FakeUrlopen([
        HTTPError("http://example.invalid/series.json", 404, "Not Found", {}, None),
        json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)]).encode("utf-8"),
    ])
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        with pytest.raises(HTTPError) as excinfo:
            load_json_document(
                "http://example.invalid/series.json",
                max_attempts=3, retry_delay=0,
            )
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    assert excinfo.value.code == 404
    assert fake.calls == 1


def test_load_json_document_retenta_http_error_5xx():
    """HTTPError 5xx (erro do servidor) é tratado como transitório -- ao
    contrário de 4xx, um retry pode ter sucesso se o servidor se recuperar."""
    fake = _FakeUrlopen([
        HTTPError("http://example.invalid/series.json", 503, "Service Unavailable", {}, None),
        json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)]).encode("utf-8"),
    ])
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        document = load_json_document(
            "http://example.invalid/series.json", retry_delay=0,
        )
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    assert document[0]["p_up"] == 60.0
    assert fake.calls == 2


def test_load_json_document_falha_fechado_apos_esgotar_tentativas():
    fake = _FakeUrlopen([
        URLError("erro 1"),
        URLError("erro 2"),
    ])
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        with pytest.raises(URLError):
            load_json_document(
                "http://example.invalid/series.json",
                max_attempts=2, retry_delay=0,
            )
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    assert fake.calls == 2


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


def test_cli_exige_session_date_explicito(tmp_path):
    """--session-date virou obrigatório: sem ele, o script derivava a sessão
    de reference[0].timestamp -- a barra mais ANTIGA da série pública
    ordenada, não "hoje". Se o Firebase devolvesse histórico multi-dia, isso
    adotaria silenciosamente a primeira sessão do histórico como se fosse a
    sessão do timer."""
    public = tmp_path / "public.json"
    public.write_text(
        json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)]), encoding="utf-8"
    )

    with pytest.raises(SystemExit) as excinfo:
        main([
            "--public-source", str(public),
            "--skip-local-api",
        ])

    assert excinfo.value.code == 2


def test_cli_rejeita_referencia_publica_de_sessao_diferente_da_esperada(tmp_path):
    """O timer fornece a sessão esperada; se o Firebase ainda não publicou a
    sessão do dia (cache stale, atraso de publicação) e só devolve barras de
    ontem, main() deve rejeitar em vez de silenciosamente adotar 'ontem'
    como se fosse a sessão sendo capturada -- e não pode gravar nenhum
    bundle parcial em --capture-dir."""
    public = tmp_path / "public.json"
    captures = tmp_path / "captures"
    public.write_text(
        json.dumps([_bar("2026-07-15T15:00:00Z", 60.0)]), encoding="utf-8"
    )

    status = main([
        "--public-source", str(public),
        "--skip-local-api",
        "--session-date", "2026-07-16",
        "--capture-dir", str(captures),
    ])

    assert status == 1
    assert not list(captures.glob('**/manifest.json'))


def test_cli_rejeita_serie_local_de_sessao_diferente_e_recusa_capturar(tmp_path):
    """Mesma rejeição para a série LOCAL (v1/v2): a API respondeu, o JSON é
    válido, mas os timestamps pertencem a outra sessão -- sintoma de cache
    stale na API local. Como v1 obrigatório fica ausente após o filtro de
    sessão, main() deve recusar em vez de prosseguir só com v2."""
    public = tmp_path / "public.json"
    captures = tmp_path / "captures"
    public.write_text(
        json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)]), encoding="utf-8"
    )
    fake = _RoutedFakeUrlopen({
        "version=v1": json.dumps(
            [_bar("2026-07-15T15:00:00Z", 59.0)]
        ).encode("utf-8"),
        "version=v2": json.dumps(
            [_bar("2026-07-16T15:00:00Z", 58.0)]
        ).encode("utf-8"),
    })
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        status = main([
            "--public-source", str(public),
            "--local-api", "http://example.invalid",
            "--session-date", "2026-07-16",
            "--capture-dir", str(captures),
        ])
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    assert status == 1
    assert not list(captures.glob('**/manifest.json'))


def test_cli_recusa_quando_v1_ausente_mesmo_com_v2_disponivel(tmp_path, monkeypatch):
    """Cenário citado como bug atual: v1 indisponível (erro de rede
    persistente) com v2 disponível não pode terminar em sucesso nem gravar
    closed=true -- v1 e v2 são ambos obrigatórios quando a API local não é
    pulada."""
    public = tmp_path / "public.json"
    captures = tmp_path / "captures"
    public.write_text(
        json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)]), encoding="utf-8"
    )
    monkeypatch.setattr(compare_p_dynamic_parity.time, "sleep", lambda *_: None)
    fake = _RoutedFakeUrlopen({
        "version=v1": URLError("erro persistente de rede"),
        "version=v2": json.dumps(
            [_bar("2026-07-16T15:00:00Z", 58.0)]
        ).encode("utf-8"),
    })
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        status = main([
            "--public-source", str(public),
            "--local-api", "http://example.invalid",
            "--session-date", "2026-07-16",
            "--capture-dir", str(captures),
        ])
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    assert status == 1
    assert not list(captures.glob('**/manifest.json'))


def test_cli_recusa_quando_referencia_publica_indisponivel_mesmo_com_v1_v2_ok(
    tmp_path, monkeypatch,
):
    """Miqueias (pública) é tão obrigatória quanto v1/v2 -- reforça o
    contrato para os três, e confirma que nenhum bundle parcial é gravado
    quando só a referência pública falha."""
    captures = tmp_path / "captures"
    monkeypatch.setattr(compare_p_dynamic_parity.time, "sleep", lambda *_: None)
    fake = _RoutedFakeUrlopen({
        "series.json": URLError("erro persistente de rede"),
    })
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        status = main([
            "--public-source", "http://example.invalid/series.json",
            "--skip-local-api",
            "--session-date", "2026-07-16",
            "--capture-dir", str(captures),
        ])
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    assert status == 1
    assert not list(captures.glob('**/manifest.json'))


def test_cli_rejeita_cauda_da_sessao_brt_anterior_em_vez_de_declarar_fechada(tmp_path):
    """A sessão é um dia BRT, não um dia UTC. Com brt_offset_h=6 a sessão B3
    de 09:00-18:00 BRT ocupa UTC 15:00 até 00:00 do dia UTC seguinte -- logo
    barras em UTC 00:00-00:30 pertencem à sessão BRT ANTERIOR, mas caem na
    data UTC da sessão pedida. Filtrando por data UTC elas passam e, sozinhas,
    rendem last_operational_brt=18:30 >= 17:50, gravando manifesto
    closed=true feito inteiramente de barras estrangeiras."""
    public = tmp_path / "public.json"
    captures = tmp_path / "captures"
    # BRT 18:00-18:30 de 2026-07-15 == UTC 00:00-00:30 de 2026-07-16.
    previous_tail = [
        _bar(f"2026-07-16T00:{minute:02d}:00Z", 60.0) for minute in (0, 10, 20, 30)
    ]
    public.write_text(json.dumps(previous_tail), encoding="utf-8")
    fake = _RoutedFakeUrlopen({
        "version=v1": json.dumps(previous_tail).encode("utf-8"),
        "version=v2": json.dumps(previous_tail).encode("utf-8"),
    })
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        status = main([
            "--public-source", str(public),
            "--local-api", "http://example.invalid",
            "--session-date", "2026-07-16",
            "--capture-dir", str(captures),
        ])
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    assert status != 0
    assert not list(captures.glob('**/manifest.json'))


def test_cli_persiste_apenas_barras_da_sessao_preservando_envelope(tmp_path):
    """O filtro de sessão não pode viver só na comparação em memória: o
    avaliador (evaluate_p_dynamic_champions) relê os JSONs persistidos sem
    filtrar (normalize_series(_extract_rows(document))), então barra
    estrangeira gravada no bundle entra em Brier/log-loss. Também confirma
    que a barra legítima de BRT 18:00 (UTC 00:00 do dia seguinte) é mantida
    e que o envelope (brt_offset_h) sobrevive."""
    public = tmp_path / "public.json"
    captures = tmp_path / "captures"
    foreign = [_bar("2026-07-16T00:00:00Z", 99.0)]          # BRT 18:00 de 15/07
    session = [
        *_grid_until("2026-07-16T23:55:00Z", 60.0),          # BRT 09:00-17:55
        _bar("2026-07-17T00:00:00Z", 62.0),                  # BRT 18:00 de 16/07
    ]
    public.write_text(json.dumps(foreign + session), encoding="utf-8")
    local_payload = json.dumps(
        {"brt_offset_h": 6, "session_date": "2026-07-16", "series": foreign + session}
    ).encode("utf-8")
    fake = _RoutedFakeUrlopen({
        "version=v1": local_payload,
        "version=v2": local_payload,
    })
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        status = main([
            "--public-source", str(public),
            "--local-api", "http://example.invalid",
            "--session-date", "2026-07-16",
            "--capture-dir", str(captures),
        ])
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    assert status == 0
    bundle = next(captures.glob("2026-07-16/*/"))
    stored_v2 = json.loads((bundle / "v2.json").read_text(encoding="utf-8"))
    stored_public = json.loads((bundle / "miqueias.json").read_text(encoding="utf-8"))
    persisted = [row["timestamp"] for row in stored_v2["series"]]

    assert stored_v2["brt_offset_h"] == 6
    assert persisted[0] == "2026-07-16T15:00:00Z"
    assert persisted[-1] == "2026-07-17T00:00:00Z"
    assert "2026-07-16T00:00:00Z" not in persisted
    assert all(row["timestamp"] != "2026-07-16T00:00:00Z" for row in stored_public)


def test_cli_nao_retorna_sucesso_quando_a_sessao_nao_fecha(tmp_path):
    """Fonte que responde mas termina antes de 17:50 BRT grava bundle com
    closed=false e, hoje, retorna 0 -- o systemd marca sucesso e ninguém é
    alertado de que o dia se perdeu. O bundle deve continuar gravado (é útil
    para forense), mas o código de saída precisa denunciar o dia incompleto."""
    public = tmp_path / "public.json"
    captures = tmp_path / "captures"
    # Última barra em BRT 14:00 -- sessão claramente não fechada.
    partial = [_bar("2026-07-16T15:00:00Z", 60.0), _bar("2026-07-16T20:00:00Z", 61.0)]
    public.write_text(json.dumps(partial), encoding="utf-8")
    local_payload = json.dumps({"brt_offset_h": 6, "series": partial}).encode("utf-8")
    fake = _RoutedFakeUrlopen({
        "version=v1": local_payload,
        "version=v2": local_payload,
    })
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        status = main([
            "--public-source", str(public),
            "--local-api", "http://example.invalid",
            "--session-date", "2026-07-16",
            "--capture-dir", str(captures),
        ])
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    bundle = next(captures.glob("2026-07-16/*/"))
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["session"]["closed"] is False
    assert status != 0


def test_cli_nao_alarma_quando_nao_havia_sessao_para_capturar(tmp_path):
    """Feriado B3 (cai em dia útil, dentro do Mon..Fri do timer) e catch-up de
    Persistent=true antes da abertura produzem zero barra operacional. Isso não
    é anomalia -- é ausência de pregão. Sair não-zero aqui manda o unit para
    `failed` todo feriado e ensina o operador a ignorar exatamente o alerta que
    o código 3 existe para dar."""
    public = tmp_path / "public.json"
    captures = tmp_path / "captures"
    ghosts = [
        _bar("2026-07-16T15:00:00Z", 50.0, is_ghost=True),
        _bar("2026-07-16T23:55:00Z", 50.0, is_ghost=True),
    ]
    public.write_text(json.dumps(ghosts), encoding="utf-8")
    payload = json.dumps({"brt_offset_h": 6, "series": ghosts}).encode("utf-8")
    fake = _RoutedFakeUrlopen({"version=v1": payload, "version=v2": payload})
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        status = main([
            "--public-source", str(public),
            "--local-api", "http://example.invalid",
            "--session-date", "2026-07-16",
            "--capture-dir", str(captures),
        ])
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    bundle = next(captures.glob("2026-07-16/*/"))
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["session"]["closed"] is False
    assert all(
        source["operational_rows"] == 0
        for source in manifest["session"]["sources"].values()
    )
    assert status == 0


def test_cli_valida_formato_de_session_date_antes_de_executar(tmp_path):
    """--session-date virou entrada obrigatória vinda de substituição de shell
    no unit; formato inválido deve falhar no argparse, não virar traceback cru
    dentro de capture_brt_offset_h."""
    public = tmp_path / "public.json"
    public.write_text(json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)]), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main([
            "--public-source", str(public),
            "--skip-local-api",
            "--session-date", "16/07/2026",
        ])

    assert excinfo.value.code == 2


def _grid_until(last_label, p_up=60.0, **extra):
    """Grade de 5min de BRT 09:00 (rótulo 15:00) até last_label, inclusive.

    O gate passou a exigir cobertura da sessão inteira, então fixture de duas
    barras não representa mais captura válida -- representa feed degradado.
    """
    hour, minute = int(last_label[11:13]), int(last_label[14:16])
    count = ((hour - 15) * 60 + minute) // 5 + 1
    rows = []
    for index in range(count):
        total = index * 5
        rows.append(
            _bar(
                f"{last_label[:11]}{15 + total // 60:02d}:{total % 60:02d}:00Z",
                p_up,
                **extra,
            )
        )
    return rows


def _routed_session(tmp_path, *, public_last, local_last, captures_name="captures"):
    """Sessão B3 completa dos dois lados, variando só o último ponto operacional."""
    public = tmp_path / "public.json"
    captures = tmp_path / captures_name
    public.write_text(json.dumps(_grid_until(public_last, 61.0)), encoding="utf-8")
    payload = json.dumps(
        {"brt_offset_h": 6, "series": _grid_until(local_last, 59.0)}
    ).encode("utf-8")
    return public, captures, _RoutedFakeUrlopen(
        {"version=v1": payload, "version=v2": payload}
    )


def _run_routed(public, captures, fake, extra=()):
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        return main([
            "--public-source", str(public),
            "--local-api", "http://example.invalid",
            "--session-date", "2026-07-16",
            "--capture-dir", str(captures),
            *extra,
        ])
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen


def test_captura_registra_revisao_congelada_da_api_nao_do_checkout(tmp_path, monkeypatch):
    """A API pode estar viva com código antigo enquanto o checkout já mudou.
    O manifesto precisa registrar quem efetivamente serviu v1/v2."""
    public, captures, fake = _routed_session(
        tmp_path, public_last="2026-07-16T23:50:00Z", local_last="2026-07-16T23:55:00Z"
    )
    runtime_revision = {
        "git_commit": "a" * 40,
        "engine_sha256": "b" * 64,
        "kalman_sha256": "c" * 64,
    }
    monkeypatch.setattr(
        compare_p_dynamic_parity,
        "current_engine_revision",
        lambda: (_ for _ in ()).throw(AssertionError("não deve ler o checkout")),
    )

    status = _run_routed(public, captures, fake)
    manifest = json.loads(
        next(captures.glob("2026-07-16/*/manifest.json")).read_text(encoding="utf-8")
    )

    assert status == 0
    assert manifest["engine_revision"] == runtime_revision


def test_captura_recusa_api_que_reiniciou_entre_v1_e_v2(tmp_path):
    """Se o processo muda durante a captura, uma sessão não pode receber uma
    identidade arbitrária e entrar no ledger como se fosse homogênea."""
    public, captures, fake = _routed_session(
        tmp_path, public_last="2026-07-16T23:50:00Z", local_last="2026-07-16T23:55:00Z"
    )
    fake.routes["p-dynamic-runtime-revision"] = [
        json.dumps(
            {
                "engine_revision": {
                    "git_commit": "a" * 40,
                    "engine_sha256": "b" * 64,
                    "kalman_sha256": "c" * 64,
                }
            }
        ).encode("utf-8"),
        json.dumps(
            {
                "engine_revision": {
                    "git_commit": "d" * 40,
                    "engine_sha256": "e" * 64,
                    "kalman_sha256": "f" * 64,
                }
            }
        ).encode("utf-8"),
    ]

    status = _run_routed(public, captures, fake)

    assert status == 1
    assert not list(captures.glob("**/manifest.json"))


def test_manifesto_aceita_referencia_publica_atrasada_uma_barra(tmp_path):
    """O publicador externo do Miqueias fecha exatamente no limiar (17:50 nos
    dois bundles reais), então um atraso de uma barra perderia o dia sem
    reexecução possível. Ele tolera 17:45; a barra ausente NÃO é preenchida --
    last_operational_brt registra o que de fato chegou."""
    public, captures, fake = _routed_session(
        tmp_path, public_last="2026-07-16T23:45:00Z", local_last="2026-07-16T23:55:00Z"
    )

    status = _run_routed(public, captures, fake)

    bundle = next(captures.glob("2026-07-16/*/"))
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    sources = manifest["session"]["sources"]

    assert status == 0
    assert manifest["session"]["closed"] is True
    assert sources["miqueias"]["closed"] is True
    assert sources["miqueias"]["last_operational_brt"] == "17:45"
    assert sources["miqueias"]["close_not_before_brt"] == "17:45"
    assert sources["v1"]["close_not_before_brt"] == "17:50"
    # A barra ausente não pode ser inventada para "fechar" a série: a pública
    # tem uma barra a menos que os locais, e assim permanece.
    assert sources["miqueias"]["operational_rows"] == 106
    assert sources["v1"]["operational_rows"] == 108


def test_manifesto_rejeita_serie_local_atrasada_uma_barra(tmp_path):
    """v1/v2 definem o outcome do WIN, então continuam exigindo 17:50 -- a
    tolerância de uma barra vale só para a referência pública."""
    public, captures, fake = _routed_session(
        tmp_path, public_last="2026-07-16T23:50:00Z", local_last="2026-07-16T23:45:00Z"
    )

    status = _run_routed(public, captures, fake)

    bundle = next(captures.glob("2026-07-16/*/"))
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    sources = manifest["session"]["sources"]

    assert sources["miqueias"]["closed"] is True
    assert sources["v1"]["closed"] is False
    assert sources["v1"]["last_operational_brt"] == "17:45"
    assert manifest["session"]["closed"] is False
    assert status == compare_p_dynamic_parity.EXIT_SESSION_NOT_CLOSED


def test_captura_grava_payload_cru_comprimido_com_procedencia(tmp_path):
    """A fonte pública é rolling: uma vez sobrescrita, o payload original some.
    Guarda-se o cru comprimido com sha256/URL/horário/elegibilidade, separado do
    canônico filtrado -- e fora de manifest["files"], que é o que o avaliador lê."""
    public, captures, fake = _routed_session(
        tmp_path, public_last="2026-07-16T23:50:00Z", local_last="2026-07-16T23:55:00Z"
    )

    status = _run_routed(public, captures, fake)

    bundle = next(captures.glob("2026-07-16/*/"))
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    raw = manifest["raw"]

    assert status == 0
    assert set(raw) == {"miqueias", "v1", "v2"}
    for name, entry in raw.items():
        stored = bundle / entry["file"]
        assert stored.suffix == ".gz" and stored.exists()
        payload = gzip.decompress(stored.read_bytes())
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
        assert entry["source"] and entry["captured_at"] == manifest["captured_at"]
        assert entry["eligible"] is True
        # O cru precisa preservar o que o canônico descarta.
        assert isinstance(json.loads(payload.decode("utf-8")), (list, dict))
    # O avaliador só enxerga manifest["files"]; o cru não pode aparecer lá.
    assert not any(str(name).endswith(".gz") for name in manifest["files"].values())
    assert "raw" not in manifest["files"]


def _full_session_rows(p_up, **extra):
    """Grade de 5min da sessão B3 no eixo Tickmill: BRT 09:00-17:55."""
    rows = []
    for index in range(108):
        minute = index * 5
        hour, minute = 15 + minute // 60, minute % 60
        rows.append(
            _bar(f"2026-07-16T{hour:02d}:{minute:02d}:00Z", p_up, **extra)
        )
    return rows


def test_gate_rejeita_fonte_publica_esparsa_ainda_que_tardia(tmp_path):
    """Achado crítico do painel, reproduzido: o limiar de horário sozinho aceita
    uma pública com UMA barra às 17:45 e o avaliador a coroa campeã com Brier
    ~0.0004 contra 0.20 dos locais -- um único palpite quando a sessão já está
    decidida é quase-oráculo. Quanto mais o feed degrada, melhor o Brier dele.
    Tolerar UMA BARRA DE ATRASO não pode significar aceitar UMA BARRA SÓ."""
    public = tmp_path / "public.json"
    captures = tmp_path / "captures"
    # Tudo ghost menos a penúltima barra (BRT 17:45), que é operacional.
    sparse = [
        _bar(row["timestamp"], 50.0, is_ghost=row["timestamp"] != "2026-07-16T23:45:00Z")
        for row in _full_session_rows(50.0)
    ]
    public.write_text(json.dumps(sparse), encoding="utf-8")
    payload = json.dumps(
        {"brt_offset_h": 6, "series": _full_session_rows(45.0)}
    ).encode("utf-8")
    fake = _RoutedFakeUrlopen({"version=v1": payload, "version=v2": payload})

    status = _run_routed(public, captures, fake)

    bundle = next(captures.glob("2026-07-16/*/"))
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    sources = manifest["session"]["sources"]

    assert sources["miqueias"]["operational_rows"] == 1
    assert sources["miqueias"]["closed"] is False
    assert sources["v1"]["closed"] is True
    assert manifest["session"]["closed"] is False
    assert status == compare_p_dynamic_parity.EXIT_SESSION_NOT_CLOSED


def test_gate_rejeita_fonte_que_perdeu_a_abertura(tmp_path):
    """Série que só começa às 14:00 BRT também não é sessão completa: perder as
    barras da manhã (p~50, Brier~0.25) MELHORA a média do modelo incompleto."""
    public = tmp_path / "public.json"
    captures = tmp_path / "captures"
    full = _full_session_rows(50.0)
    public.write_text(json.dumps(full[-30:]), encoding="utf-8")
    payload = json.dumps({"brt_offset_h": 6, "series": full}).encode("utf-8")
    fake = _RoutedFakeUrlopen({"version=v1": payload, "version=v2": payload})

    status = _run_routed(public, captures, fake)

    bundle = next(captures.glob("2026-07-16/*/"))
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["session"]["sources"]["miqueias"]["closed"] is False
    assert status == compare_p_dynamic_parity.EXIT_SESSION_NOT_CLOSED


def test_gate_aceita_atraso_de_uma_barra_em_serie_completa(tmp_path):
    """O caso que a Decisão 1 existe para salvar continua salvo: série íntegra
    (abre 09:00, 107 barras) que só atrasou o último ponto para 17:45."""
    public = tmp_path / "public.json"
    captures = tmp_path / "captures"
    full = _full_session_rows(50.0)
    public.write_text(json.dumps(full[:-1]), encoding="utf-8")
    payload = json.dumps({"brt_offset_h": 6, "series": full}).encode("utf-8")
    fake = _RoutedFakeUrlopen({"version=v1": payload, "version=v2": payload})

    status = _run_routed(public, captures, fake)

    bundle = next(captures.glob("2026-07-16/*/"))
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    sources = manifest["session"]["sources"]

    assert sources["miqueias"]["last_operational_brt"] == "17:50"
    assert sources["miqueias"]["operational_rows"] == 107
    assert sources["miqueias"]["closed"] is True
    assert manifest["session"]["closed"] is True
    assert status == 0


def test_cru_preserva_os_bytes_da_fonte_e_sobrevive_a_aborto(tmp_path):
    """Duas exigências do painel: (a) o cru tem que ser os BYTES do fio, senão o
    sha256 prova só o que o parser local entendeu; (b) tem que ser gravado ANTES
    das validações, senão falta exatamente quando o filtro erra e a fonte rolling
    já girou. Aqui v1 falha, main() aborta -- e o cru da pública tem que existir."""
    captures = tmp_path / "captures"
    # Formatação deliberadamente não-canônica: chaves fora de ordem e espaçamento
    # próprio. Se o cru for re-serializado, estes bytes não sobrevivem.
    wire = (
        b'{"zulu":1,"series":[{"p_up":60.0,"timestamp":"2026-07-16T15:00:00Z"}],'
        b'  "brt_offset_h":6}'
    )
    fake = _RoutedFakeUrlopen({
        "public.json": wire,
        "version=v1": URLError("indisponivel"),
        "version=v2": wire,
    })
    original_urlopen = compare_p_dynamic_parity.urlopen
    compare_p_dynamic_parity.urlopen = fake
    try:
        status = main([
            "--public-source", "http://example.invalid/public.json",
            "--local-api", "http://example.invalid",
            "--session-date", "2026-07-16",
            "--capture-dir", str(captures),
            "--timeout", "0.01",
        ])
    finally:
        compare_p_dynamic_parity.urlopen = original_urlopen

    stored = sorted(captures.glob("2026-07-16/*/raw/*.json.gz"))

    assert status == 1
    assert [path.name for path in stored] == ["miqueias.json.gz", "v2.json.gz"]
    assert gzip.decompress(stored[0].read_bytes()) == wire


def test_cli_recusa_candidato_com_nome_reservado(tmp_path):
    """--candidate miqueias=... sobrescrevia miqueias.json, raw/miqueias.json.gz e
    o status da fonte pública, aplicando ainda o limiar público a uma série local."""
    public = tmp_path / "public.json"
    other = tmp_path / "other.json"
    rows = json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)])
    public.write_text(rows, encoding="utf-8")
    other.write_text(rows, encoding="utf-8")

    for reserved in ("miqueias", "manifest", "report", "gex"):
        status = main([
            "--public-source", str(public),
            "--skip-local-api",
            "--candidate", f"{reserved}={other}",
            "--session-date", "2026-07-16",
        ])
        assert status == 1, reserved


def test_falha_ao_arquivar_o_cru_impede_sessao_elegivel(tmp_path, monkeypatch):
    """O cru é a única evidência reprodutível de uma fonte rolling. Sem ele a
    sessão não é auditável, então não pode entrar no ledger como fechada --
    antes o erro ia só para stderr, com exit 0, closed=true e, pior,
    eligible=true numa entrada que registrava `error` e não tinha arquivo."""
    public, captures, fake = _routed_session(
        tmp_path, public_last="2026-07-16T23:50:00Z", local_last="2026-07-16T23:55:00Z"
    )

    def out_of_space(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(
        compare_p_dynamic_parity, "_archive_raw_payload", out_of_space
    )
    status = _run_routed(public, captures, fake)

    manifest = json.loads(
        next(captures.glob("2026-07-16/*/manifest.json")).read_text(encoding="utf-8")
    )

    assert manifest["session"]["raw_archive_complete"] is False
    assert manifest["session"]["closed"] is False
    assert all(entry["eligible"] is False for entry in manifest["raw"].values())
    assert all("error" in entry for entry in manifest["raw"].values())
    assert status != 0


def test_cli_nao_confunde_candidato_mais_proximo_com_vencedor_de_qualidade(tmp_path):
    public = tmp_path / "public.json"
    v1 = tmp_path / "v1.json"
    candidate = tmp_path / "v2.json"
    output = tmp_path / "report.json"
    captures = tmp_path / "captures"
    # A sessão precisa fechar (cobertura + abertura + horário), senão o CLI sai
    # com EXIT_SESSION_NOT_CLOSED e este teste deixaria de exercitar o ranking.
    rows = _grid_until("2026-07-16T23:55:00Z", 60.0)
    public.write_text(json.dumps(rows), encoding="utf-8")
    v1.write_text(
        json.dumps(_grid_until("2026-07-16T23:55:00Z", 61.0)), encoding="utf-8"
    )
    candidate.write_text(json.dumps(rows), encoding="utf-8")

    status = main(
        [
            "--public-source",
            str(public),
            "--skip-local-api",
            "--candidate",
            f"v1={v1}",
            "--candidate",
            f"v2={candidate}",
            "--session-date",
            "2026-07-16",
            "--output-json",
            str(output),
            "--capture-dir",
            str(captures),
        ]
    )
    conclusion = json.loads(output.read_text(encoding="utf-8"))["conclusion"]

    assert status == 0
    assert conclusion["scope"] == "parity_only"
    assert conclusion["closest_candidate"] == "v2"
    assert conclusion["quality_winner"] is None
    assert "OOS" in conclusion["promotion_warning"]
    report = json.loads(output.read_text(encoding="utf-8"))
    assert set(report["capture_paths"]) == {
        "miqueias", "v1", "v2", "gex", "report", "manifest",
    }
    assert (tmp_path / "captures" / "2026-07-16").is_dir()


def test_cli_preserva_empate_de_paridade_sem_escolher_v1_arbitrariamente(tmp_path):
    source = tmp_path / "series.json"
    output = tmp_path / "report.json"
    source.write_text(
        json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)]), encoding="utf-8"
    )

    status = main(
        [
            "--public-source",
            str(source),
            "--skip-local-api",
            "--candidate",
            f"v1={source}",
            "--candidate",
            f"v2={source}",
            "--session-date",
            "2026-07-16",
            "--output-json",
            str(output),
        ]
    )
    conclusion = json.loads(output.read_text(encoding="utf-8"))["conclusion"]

    assert status == 0
    assert conclusion["parity_tie"] is True
    assert conclusion["closest_candidate"] is None
    assert conclusion["closest_candidates"] == ["v1", "v2"]


def test_cli_adiciona_challenger_estatico_configurado_sem_mudar_v1_v2(tmp_path):
    public = tmp_path / "public.json"
    source = tmp_path / "factors.json"
    config = tmp_path / "miqueias_static.json"
    output = tmp_path / "report.json"
    public.write_text(
        json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)]), encoding="utf-8"
    )
    source.write_text(json.dumps({"series": [{
        "timestamp": "2026-07-16T15:00:00Z",
        "t_frac": 0.25,
        "factors": {
            "wdo": {"ret": 0.1},
            "di1": {"ret": -0.2},
        },
    }]}), encoding="utf-8")
    config.write_text(
        json.dumps(_miqueias_static_config()), encoding="utf-8"
    )

    status = main([
        "--public-source", str(public),
        "--skip-local-api",
        "--miqueias-static-config", str(config),
        "--miqueias-static-source", str(source),
        "--session-date", "2026-07-16",
        "--output-json", str(output),
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    assert status == 0
    assert set(report["candidates"]) == {"miqueias_static"}
    assert report["static_challengers"]["miqueias_static"]["config"]["alpha"] == 2.0
    assert report["static_challengers"]["miqueias_static"]["config"]["limitations"] == [
        "static_calibration_only",
        "no_kalman_state_or_qr",
        "not_a_claim_of_v2_parity",
    ]


def test_cli_recusa_configuracao_estatica_incompleta(tmp_path):
    public = tmp_path / "public.json"
    source = tmp_path / "factors.json"
    config = tmp_path / "miqueias_static.json"
    public.write_text(
        json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)]), encoding="utf-8"
    )
    source.write_text(json.dumps({"series": []}), encoding="utf-8")
    config.write_text(
        json.dumps(_miqueias_static_config(factors={"wdo": {"weight": 0.5}})),
        encoding="utf-8",
    )

    status = main([
        "--public-source", str(public),
        "--skip-local-api",
        "--miqueias-static-config", str(config),
        "--miqueias-static-source", str(source),
        "--session-date", "2026-07-16",
    ])

    assert status == 1


def test_cli_recusa_fonte_vazia_para_challenger_estatico(tmp_path):
    public = tmp_path / "public.json"
    source = tmp_path / "factors.json"
    config = tmp_path / "miqueias_static.json"
    public.write_text(
        json.dumps([_bar("2026-07-16T15:00:00Z", 60.0)]), encoding="utf-8"
    )
    source.write_text(json.dumps({"series": []}), encoding="utf-8")
    config.write_text(json.dumps(_miqueias_static_config()), encoding="utf-8")

    status = main([
        "--public-source", str(public),
        "--skip-local-api",
        "--miqueias-static-config", str(config),
        "--miqueias-static-source", str(source),
        "--session-date", "2026-07-16",
    ])

    assert status == 1
