#!/usr/bin/env python3
"""Compara o P Dinâmico público do Miqueias com IRAI v1/v2 para WIN.

A página pública entrega o valor já calculado no Firebase. O bundle do gráfico
seleciona ``p_up_v1`` quando o campo existe e, caso contrário, usa ``p_up``.
Este script replica essa escolha, busca as duas versões da API local, alinha
somente timestamps que representam exatamente o mesmo instante e mede a
paridade numérica e operacional (regimes venda/neutro/compra em 40/60).

O timestamp retornado pela API do IRAI está no eixo do servidor Tickmill. Para
não esconder problemas de relógio, este comparador não aplica deslocamentos
heurísticos: offsets ISO explícitos são normalizados para UTC, e uma série sem
fuso não pode ser comparada silenciosamente com outra que possua fuso.

``--session-date`` é obrigatório: é a sessão esperada, fornecida pelo
chamador (o timer diário), não derivada da série pública. Referência pública
e séries locais que não contêm nenhuma barra dessa sessão são rejeitadas
(main() falha com código não-zero, sem gravar captura) mesmo depois de
esgotar o retry de transporte.

Uso normal na máquina Windows onde a API IRAI está ativa::

    python -X utf8 scripts/compare_p_dynamic_parity.py \
      --local-api http://localhost:8888 \
      --session-date 2026-07-16 \
      --output-json p_dynamic_win.json

Também é possível comparar arquivos capturados::

    python -X utf8 scripts/compare_p_dynamic_parity.py \
      --skip-local-api --session-date 2026-07-16 \
      --candidate v1=win_v1.json --candidate v2=win_v2.json

Um challenger estático do Miqueias é opcional e só é construído a partir de
uma configuração completa e versionada. Ele usa os retornos dos fatores da
série de entrada, nunca os pesos ou z-scores dinâmicos do IRAI::

    python -X utf8 scripts/compare_p_dynamic_parity.py \
      --session-date 2026-07-16 \
      --miqueias-static-config /caminho/para/miqueias_static.json
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.client import IncompleteRead
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

JSON_DOWNLOAD_MAX_ATTEMPTS = 3
JSON_DOWNLOAD_RETRY_SECONDS = 5.0

# Códigos de saída: 0 ok (inclui "nenhuma barra operacional", isto é, feriado ou
# captura antes da abertura); 1 fonte obrigatória ausente/ilegível; 2 sessão
# fechada mas nenhum candidato compartilha timestamp com a referência; 3 sessão
# abriu e não fechou (anomalia -- o dia não entra no ledger).
EXIT_SESSION_NOT_CLOSED = 3
EXIT_NOT_COMPARABLE = 2

MANIFEST_SCHEMA_VERSION = 2
# Sobe quando a REGRA muda (elegibilidade ou métrica), não quando o formato muda.
# 2 (2026-07-19): janela de pregão explícita no eixo BRT, piso absoluto de
# cobertura e Brier/log-loss na interseção de timestamps. Bundles gravados sob a
# versão 1 não são comparáveis com estes e o avaliador os recusa -- sem isso, o
# corte de época dependeria só de mover diretórios, e qualquer restore de backup
# reinjetaria sessões apuradas por régua diferente.
# 3 (2026-07-20): identidade verificável de engine.py/kalman.py. A regra de
# elegibilidade passa a recusar bundles sem proveniência do motor e o avaliador
# recusa um ledger com mais de uma revisão -- misturar versões do Kalman também
# invalida uma comparação OOS, mesmo com a mesma métrica.
METHODOLOGY_VERSION = 3

# v1/v2 definem o outcome do WIN, então exigem a sessão inteira até 17:50 BRT.
# A referência pública é publicada por terceiro e fecha exatamente no limiar
# (17:50 nos bundles reais): sem folga, um atraso de uma barra perderia o dia
# sem reexecução possível. Tolera-se uma barra -- e só para ela.
CLOSE_NOT_BEFORE_BRT = "17:50"
CLOSE_NOT_BEFORE_BRT_PUBLIC = "17:45"
PUBLIC_MODEL = "miqueias"
TOURNAMENT_MODELS = (PUBLIC_MODEL, "v1", "v2")

# Janela real do pregão B3. Filtrar só por DATA BRT não basta: uma barra da
# madrugada (ex.: 02:00 BRT) cai na data certa e satisfazia o gate de abertura,
# deixando passar série de 2 barras que vencia o torneio por quase-oráculo.
SESSION_OPEN_BRT = "09:00"
SESSION_CLOSE_BRT = "18:00"

# Horário sozinho não distingue "atrasou uma barra" de "publicou uma barra só":
# a fonte também tem que ter aberto no pregão e coberto a sessão.
OPEN_NOT_AFTER_BRT = "09:10"
COVERAGE_MIN_RATIO = 0.9
# Grade de 5min entre 09:00 e 17:55 BRT. Piso ABSOLUTO, não relativo à fonte
# mais completa da captura: o relativo tem denominador endógeno (degradação
# correlacionada rebaixa o piso junto) e entregaria a régua a um terceiro --
# se o feed público inflasse a contagem, os locais íntegros seriam reprovados
# todo dia. Absoluto não custa nada em pregão encurtado porque esse dia já
# morre no limiar de fechamento, que também é absoluto.
SESSION_EXPECTED_OPERATIONAL_ROWS = 108

# Nomes que colidiriam com artefatos do bundle: um --candidate chamado
# "miqueias" sobrescreveria a série pública, seu cru e seu status.
RESERVED_CANDIDATE_NAMES = frozenset(
    {PUBLIC_MODEL, "gex", "report", "manifest", "miqueias_static"}
)

def close_not_before_for(model: str) -> str:
    """Limiar de fechamento por fonte. Capturador e avaliador precisam usar o
    MESMO limiar, senão um bundle gravado como fechado seria recusado depois."""
    return (
        CLOSE_NOT_BEFORE_BRT_PUBLIC if model == PUBLIC_MODEL else CLOSE_NOT_BEFORE_BRT
    )


def raw_archive_is_complete(raw_entries: Mapping[str, object]) -> bool:
    """Confirma que toda fonte oficial tem o payload cru arquivado.

    Challengers manuais são auxiliares da análise de paridade e não podem
    tornar a captura oficial inelegível. O trio do torneio, por outro lado,
    precisa ser auditável para que uma sessão possa entrar no ledger OOS.
    """
    return all(
        isinstance(raw_entries.get(name), dict)
        and "error" not in raw_entries[name]
        and raw_entries[name].get("file")
        and raw_entries[name].get("sha256")
        for name in TOURNAMENT_MODELS
    )

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.irai.runtime_revision import build_engine_revision, validate_engine_revision
from backend.irai.timezones import brt_to_tickmill_offset_hours
from backend.irai.miqueias_static import (
    MiqueiasStaticConfig,
    build_miqueias_static_rows,
    describe_miqueias_static_config,
    load_miqueias_static_config,
)


def current_engine_revision() -> dict[str, str]:
    """Usada somente em captura offline, sem API local para consultar."""
    return build_engine_revision()


DEFAULT_PUBLIC_SOURCE = (
    "https://rastromacro-default-rtdb.firebaseio.com/series/WIN_N.json"
)
DEFAULT_LOCAL_API = "http://localhost:8888"
DEFAULT_TARGET = "WIN$N"
PUBLIC_VALUE_FIELDS = ("p_up_v1", "p_up")
LOCAL_VALUE_FIELDS = ("p_up",)


@dataclass(frozen=True)
class SeriesPoint:
    timestamp: str
    moment: datetime
    aware: bool
    value: float
    value_field: str
    is_ghost: bool
    is_preview: bool

    @property
    def operational(self) -> bool:
        return not self.is_ghost and not self.is_preview


def _extract_rows(payload, *, target_key: str = "WIN_N") -> list[dict]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        series = payload.get("series")
        if isinstance(series, list):
            rows = series
        elif isinstance(series, dict) and isinstance(series.get(target_key), list):
            rows = series[target_key]
        else:
            detail = payload.get("detail") or payload.get("error")
            raise ValueError(f"JSON não contém uma série {target_key}: {detail or 'formato desconhecido'}")
    else:
        raise ValueError("Fonte JSON precisa ser uma lista ou um objeto com `series`")

    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Todas as entradas da série precisam ser objetos JSON")
    return rows


def load_json_document(
    source: str,
    *,
    timeout: float = 10.0,
    max_attempts: int = JSON_DOWNLOAD_MAX_ATTEMPTS,
    retry_delay: float = JSON_DOWNLOAD_RETRY_SECONDS,
):
    """Lê um documento JSON sem descartar o envelope ou metadados da fonte.

    rastro-irado-p-dynamic-ledger.timer roda uma vez por dia útil (17:56 BRT)
    e não reagenda para a mesma sessão -- sem retry aqui, uma falha
    transitória de rede ou um payload não-JSON isolado (manutenção pontual do
    Firebase/API) perde a captura do dia inteiro, atrasando o gate de 60
    sessões do avaliador champion-challenger (IRAI-18). Erro de contrato após
    um JSON válido (ex.: série sem os campos esperados) não é transitório e
    continua falhando fechado sem retry -- é tratado por quem chama esta
    função, fora do laço abaixo.

    Só transporte é retentado: OSError (rede/timeout/DNS), IncompleteRead
    (conexão truncada -- HTTPException, não OSError) e UnicodeDecodeError/
    JSONDecodeError (payload corrompido ou não-JSON isolado). HTTPError é
    subclasse de OSError, mas um 4xx é erro de contrato do cliente (URL
    errada, recurso inexistente) que retry nenhum corrige -- só 5xx (erro
    transitório do servidor) e demais OSError entram no retry genérico.
    """
    return load_json_document_with_bytes(
        source,
        timeout=timeout,
        max_attempts=max_attempts,
        retry_delay=retry_delay,
    )[0]


def load_json_document_with_bytes(
    source: str,
    *,
    timeout: float = 10.0,
    max_attempts: int = JSON_DOWNLOAD_MAX_ATTEMPTS,
    retry_delay: float = JSON_DOWNLOAD_RETRY_SECONDS,
) -> tuple[object, bytes]:
    """Igual a load_json_document, mas devolve também os bytes como vieram.

    Guardar os bytes do fio (e não uma re-serialização) é o que dá valor
    probatório ao arquivo cru: o sha256 passa a ser do que o terceiro serviu,
    não do que o parser local entendeu -- e chave duplicada, ordem e formatação
    numérica original sobrevivem para um eventual reprocessamento.
    """
    if not source.startswith(("http://", "https://")):
        payload = Path(source).read_bytes()
        return json.loads(payload.decode("utf-8")), payload

    request = Request(source, headers={"User-Agent": "IRAI-parity-audit/1.0"})
    for attempt in range(1, max_attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read()
            return json.loads(payload.decode("utf-8")), payload
        except HTTPError as exc:
            if 400 <= exc.code < 500:
                raise
            if attempt >= max_attempts:
                raise
        except (OSError, IncompleteRead, UnicodeDecodeError, json.JSONDecodeError):
            if attempt >= max_attempts:
                raise
        time.sleep(retry_delay)
    raise AssertionError("unreachable")  # pragma: no cover


def load_json_source(source: str, *, timeout: float = 10.0) -> list[dict]:
    """Lê lista direta, envelope da API ou payload Firebase completo."""
    return _extract_rows(load_json_document(source, timeout=timeout))


def _parse_timestamp(raw_timestamp: object) -> tuple[str, datetime, bool]:
    if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
        raise ValueError("barra sem timestamp ISO válido")
    raw = raw_timestamp.strip()
    try:
        moment = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError as exc:
        raise ValueError(f"timestamp ISO inválido: {raw!r}") from exc

    aware = moment.utcoffset() is not None
    if aware:
        moment = moment.astimezone(timezone.utc)
    canonical = moment.isoformat(timespec="seconds")
    return canonical, moment, aware


def normalize_series(
    rows: Iterable[Mapping[str, object]],
    *,
    value_fields: Sequence[str],
) -> list[SeriesPoint]:
    """Normaliza uma série sem inventar valores nem casar barras por proximidade."""
    points: list[SeriesPoint] = []
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=1):
        timestamp, moment, aware = _parse_timestamp(row.get("timestamp"))
        if timestamp in seen:
            raise ValueError(f"timestamp duplicado na série: {timestamp}")

        selected_field = None
        selected_value = None
        for field in value_fields:
            value = row.get(field)
            if value is not None:
                selected_field = field
                selected_value = value
                break
        if selected_field is None:
            continue
        try:
            numeric_value = float(selected_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"valor não numérico em {selected_field}, barra {row_number}: {selected_value!r}"
            ) from exc
        if not math.isfinite(numeric_value):
            raise ValueError(f"valor não finito em {selected_field}, barra {row_number}")

        points.append(
            SeriesPoint(
                timestamp=timestamp,
                moment=moment,
                aware=aware,
                value=numeric_value,
                value_field=selected_field,
                is_ghost=bool(row.get("is_ghost", False)),
                is_preview=bool(row.get("is_preview", False)),
            )
        )
        seen.add(timestamp)
    return sorted(points, key=lambda point: point.moment)


def _session_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    session_date: str,
    brt_offset_h: int,
    label: str,
) -> list[dict]:
    """Filtra linhas cruas pela data de sessão BRT e rejeita se nada sobrar.

    ATENÇÃO ao eixo: apesar do sufixo "+00:00", os timestamps da API e do
    Firebase são hora de parede do servidor Tickmill (EEST), NÃO UTC de
    verdade -- ver a seção "Timezones" do CLAUDE.md. É por isso que se subtrai
    brt_offset_h (o vão BRT->Tickmill, 6 no horário padrão) e não 3. Em UTC
    real a sessão B3 seria 12:00-21:00 e nunca cruzaria a meia-noite; no eixo
    Tickmill ela vai de 15:00 a 00:00 do dia seguinte, e é essa travessia que
    torna o filtro por data do rótulo errado nos dois sentidos.

    O que este filtro comprovadamente corrige: a cauda da sessão BRT anterior
    (rótulo 00:00-05:55) cai na data do rótulo do dia pedido; hoje ela é toda
    ghost/preview e não pontua, mas basta esse flag falhar para ela entrar no
    score -- e, se fosse operacional, sozinha já satisfaria o limiar das 17:50
    e produziria manifesto closed=true. Note que a barra de BRT 18:00 NÃO é
    recuperada por este filtro: a captura roda 17:56 BRT e a série do dia
    termina em 17:55; aquela barra só existe, ghost, no payload do dia
    seguinte.

    Filtra linha CRUA, não SeriesPoint, porque é a linha crua que vai para o
    bundle e que o avaliador relê -- filtrar só em memória deixaria barra
    estrangeira contaminar Brier/log-loss depois.
    """
    selected = []
    for row in rows:
        _, moment, _ = _parse_timestamp(row.get("timestamp"))
        if (moment - timedelta(hours=brt_offset_h)).date().isoformat() == session_date:
            selected.append(dict(row))
    if not selected:
        raise ValueError(
            f"{label} não contém nenhuma barra da sessão esperada {session_date}"
        )
    return selected


def _document_with_session_rows(
    document, rows: Sequence[dict], *, target_key: str = "WIN_N"
):
    """Troca só a série pelas linhas da sessão, preservando o envelope.

    O avaliador lê brt_offset_h/session_date/target do envelope, então
    reescrever o documento inteiro como lista perderia o contrato de fuso e
    faria capture_brt_offset_h cair no fallback sazonal.
    """
    if isinstance(document, dict):
        series = document.get("series")
        if isinstance(series, dict) and isinstance(series.get(target_key), list):
            return {**document, "series": {**series, target_key: list(rows)}}
        if isinstance(series, list):
            return {**document, "series": list(rows)}
    return list(rows)


def capture_session_status(
    points: Sequence[SeriesPoint],
    *,
    brt_offset_h: int,
    close_not_before: str = CLOSE_NOT_BEFORE_BRT,
    open_not_after: str = OPEN_NOT_AFTER_BRT,
    min_operational_rows: int = 0,
) -> dict:
    """Classifica a captura sem tratar pré-mercado ou sessão parcial como fechada.

    Horário de término sozinho NÃO basta e a diferença é crítica: como o Brier
    é média sobre as barras que o modelo tem, uma série com um único ponto às
    17:45 -- quando a sessão já está decidida -- pontua quase como oráculo e
    vence o torneio contra concorrentes avaliados sobre a sessão inteira. O
    viés é auto-favorável (quanto mais o feed degrada, melhor o score), então
    "fechada" exige também ter aberto no horário e ter cobertura comparável à
    da fonte mais completa da mesma captura.
    """
    def brt_time(point: SeriesPoint) -> str:
        return (point.moment - timedelta(hours=brt_offset_h)).strftime("%H:%M")

    operational = [
        point
        for point in points
        if point.operational and SESSION_OPEN_BRT <= brt_time(point) < SESSION_CLOSE_BRT
    ]
    if not operational:
        return {
            "closed": False,
            "operational_rows": 0,
            "canonical_slots_covered": 0,
            "first_operational_brt": None,
            "last_operational_brt": None,
            "close_not_before_brt": close_not_before,
            "open_not_after_brt": open_not_after,
            "min_operational_rows": min_operational_rows,
        }

    first_brt = brt_time(operational[0])
    last_brt = brt_time(operational[-1])
    canonical = canonical_session_slots(
        (point.timestamp for point in operational), brt_offset_h=brt_offset_h
    )
    return {
        "closed": (
            last_brt >= close_not_before
            and first_brt <= open_not_after
            and len(canonical) >= min_operational_rows
        ),
        "operational_rows": len(operational),
        "canonical_slots_covered": len(canonical),
        "first_operational_brt": first_brt,
        "last_operational_brt": last_brt,
        "close_not_before_brt": close_not_before,
        "open_not_after_brt": open_not_after,
        "min_operational_rows": min_operational_rows,
    }


def build_source_statuses(
    points_by_source: Mapping[str, Sequence[SeriesPoint]], *, brt_offset_h: int
) -> dict:
    """Status por fonte contra o piso ABSOLUTO da grade da sessão.

    Capturador e avaliador chamam esta função para não divergirem no veredito.
    """
    minimum = math.ceil(COVERAGE_MIN_RATIO * SESSION_EXPECTED_OPERATIONAL_ROWS)
    return {
        name: capture_session_status(
            points,
            brt_offset_h=brt_offset_h,
            close_not_before=close_not_before_for(name),
            min_operational_rows=minimum,
        )
        for name, points in sorted(points_by_source.items())
    }


def in_session_brt(raw_timestamp: object, *, brt_offset_h: int) -> bool:
    """A barra cai dentro do pregão B3, no eixo BRT?

    Filtrar por DATA não basta: o collector coleta até 18:10 BRT com margem
    (backend/workers/collector.py), então barra de after-market tem a mesma data
    BRT da sessão e entra no bundle. Se o desfecho do WIN a ler, o rótulo de
    verdade sai de um preço que nenhuma barra pontuada viu -- medido no banco de
    produção, isso inverte o desfecho em ~3,6% das sessões.
    """
    _, moment, _ = _parse_timestamp(raw_timestamp)
    brt = (moment - timedelta(hours=brt_offset_h)).strftime("%H:%M")
    return SESSION_OPEN_BRT <= brt < SESSION_CLOSE_BRT


def canonical_session_slots(timestamps: Iterable[object], *, brt_offset_h: int) -> set:
    """Slots da grade M5 canônica cobertos dentro do pregão.

    Cobertura tem de ser medida em SLOT, não em contagem de linhas: 98 barras
    publicadas de minuto em minuto no fim do pregão satisfaziam qualquer piso
    baseado em contagem e ainda deixavam ~7h sem cobertura. Exigindo 98 dos 108
    slots, o buraco máximo fica limitado a 10 slots (55min) por construção.

    Usada pelo status por fonte, pela interseção e pela revalidação no
    avaliador -- os três precisam medir exatamente a mesma coisa.
    """
    slots = set()
    for raw_timestamp in timestamps:
        _, moment, _ = _parse_timestamp(raw_timestamp)
        brt_moment = moment - timedelta(hours=brt_offset_h)
        label = brt_moment.strftime("%H:%M")
        if SESSION_OPEN_BRT <= label < SESSION_CLOSE_BRT and brt_moment.minute % 5 == 0:
            slots.add(label)
    return slots


def session_operational_points(
    points: Sequence[SeriesPoint], *, brt_offset_h: int
) -> list[SeriesPoint]:
    """Barras operacionais dentro do pregão, base única para pontuação."""
    return [
        point
        for point in points
        if point.operational
        and in_session_brt(point.timestamp, brt_offset_h=brt_offset_h)
    ]


def session_intersection_stats(
    points_by_source: Mapping[str, Sequence[SeriesPoint]], *, brt_offset_h: int
) -> dict:
    """Mede a base efetivamente pontuável: a interseção entre todas as fontes.

    Elegibilidade por fonte não basta. Três fontes com 98 barras cada, mas com
    gaps DISJUNTOS, são todas elegíveis e ainda assim deixam uma interseção
    muito menor -- e essa sessão de baixa informação pesaria igual a uma íntegra
    no gate de 60, porque o torneio agrega por sessão. O piso é o mesmo da
    cobertura por fonte: o que se apura tem de cobrir a sessão, não só o que se
    coleta.
    """
    minimum = math.ceil(COVERAGE_MIN_RATIO * SESSION_EXPECTED_OPERATIONAL_ROWS)
    per_source = {
        name: {
            point.timestamp: point
            for point in session_operational_points(points, brt_offset_h=brt_offset_h)
        }
        for name, points in points_by_source.items()
    }
    common = (
        sorted(set.intersection(*(set(series) for series in per_source.values())))
        if per_source
        else []
    )

    def brt(timestamp: str) -> str:
        _, moment, _ = _parse_timestamp(timestamp)
        return (moment - timedelta(hours=brt_offset_h)).strftime("%H:%M")

    moments = [_parse_timestamp(timestamp)[1] for timestamp in common]
    max_gap = max(
        (
            int((later - earlier).total_seconds() // 60)
            for earlier, later in zip(moments, moments[1:])
        ),
        default=0,
    )
    # Contagem bruta não basta: 98 barras publicadas de minuto em minuto no fim
    # do pregão satisfaziam o piso deixando ~7h sem cobertura no meio. Só conta
    # slot da grade M5 canônica, e aí o piso limita o buraco a 10 slots (55min).
    canonical = canonical_session_slots(common, brt_offset_h=brt_offset_h)
    return {
        "rows": len(common),
        "canonical_slots_covered": len(canonical),
        "canonical_slots_expected": SESSION_EXPECTED_OPERATIONAL_ROWS,
        "min_rows": minimum,
        "sufficient": len(canonical) >= minimum,
        "max_gap_minutes": max_gap,
        "first_scored_brt": brt(common[0]) if common else None,
        "last_scored_brt": brt(common[-1]) if common else None,
        "first_scored_timestamp": common[0] if common else None,
        "last_scored_timestamp": common[-1] if common else None,
    }


def capture_brt_offset_h(session_date: str, documents: Mapping[str, object]) -> int:
    """Resolve BRT→Tickmill pelo contrato local ou pela regra sazonal causal."""
    for preferred in ("v2", "v1"):
        document = documents.get(preferred)
        if isinstance(document, dict) and document.get("brt_offset_h") is not None:
            return int(document["brt_offset_h"])
    return brt_to_tickmill_offset_hours(datetime.fromisoformat(session_date))


def _regime(value: float, *, buy_threshold: float, sell_threshold: float) -> str:
    if value >= buy_threshold:
        return "buy"
    if value <= sell_threshold:
        return "sell"
    return "neutral"


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _point_detail(reference: SeriesPoint, candidate: SeriesPoint) -> dict:
    difference = candidate.value - reference.value
    return {
        "timestamp": reference.timestamp,
        "reference": reference.value,
        "candidate": candidate.value,
        "difference": _rounded(difference),
        "absolute_difference": _rounded(abs(difference)),
    }


def _compare_subset(
    reference: Sequence[SeriesPoint],
    candidate: Sequence[SeriesPoint],
    *,
    tolerance: float,
    buy_threshold: float,
    sell_threshold: float,
) -> dict:
    if reference and candidate and reference[0].aware != candidate[0].aware:
        raise ValueError(
            "não é seguro alinhar timestamps com e sem fuso; corrija a fonte antes da comparação"
        )
    if any(point.aware != reference[0].aware for point in reference[1:]):
        raise ValueError("a série de referência mistura timestamps com e sem fuso")
    if any(point.aware != candidate[0].aware for point in candidate[1:]):
        raise ValueError("a série candidata mistura timestamps com e sem fuso")

    reference_by_time = {point.timestamp: point for point in reference}
    candidate_by_time = {point.timestamp: point for point in candidate}
    common_timestamps = sorted(set(reference_by_time) & set(candidate_by_time))
    pairs = [
        (reference_by_time[timestamp], candidate_by_time[timestamp])
        for timestamp in common_timestamps
    ]
    differences = [candidate_point.value - reference_point.value for reference_point, candidate_point in pairs]
    absolute_differences = [abs(value) for value in differences]

    correlation = None
    if len(pairs) >= 2:
        reference_values = [pair[0].value for pair in pairs]
        candidate_values = [pair[1].value for pair in pairs]
        if statistics.pstdev(reference_values) > 0 and statistics.pstdev(candidate_values) > 0:
            correlation = statistics.correlation(reference_values, candidate_values)

    confusion: dict[str, Counter] = defaultdict(Counter)
    concordant = 0
    for reference_point, candidate_point in pairs:
        reference_regime = _regime(
            reference_point.value,
            buy_threshold=buy_threshold,
            sell_threshold=sell_threshold,
        )
        candidate_regime = _regime(
            candidate_point.value,
            buy_threshold=buy_threshold,
            sell_threshold=sell_threshold,
        )
        confusion[reference_regime][candidate_regime] += 1
        concordant += reference_regime == candidate_regime

    first_divergence = next(
        (
            _point_detail(reference_point, candidate_point)
            for reference_point, candidate_point in pairs
            if abs(candidate_point.value - reference_point.value) > tolerance
        ),
        None,
    )
    maximum_detail = None
    if pairs:
        maximum_pair = max(
            pairs,
            key=lambda pair: abs(pair[1].value - pair[0].value),
        )
        maximum_detail = _point_detail(*maximum_pair)

    return {
        "reference_rows": len(reference),
        "candidate_rows": len(candidate),
        "common_rows": len(pairs),
        "reference_coverage_pct": _rounded(100 * len(pairs) / len(reference)) if reference else None,
        "candidate_coverage_pct": _rounded(100 * len(pairs) / len(candidate)) if candidate else None,
        "correlation": _rounded(correlation),
        "mae": _rounded(statistics.fmean(absolute_differences)) if pairs else None,
        "rmse": _rounded(math.sqrt(statistics.fmean(value * value for value in differences))) if pairs else None,
        "mean_difference": _rounded(statistics.fmean(differences)) if pairs else None,
        "max_absolute_difference": _rounded(max(absolute_differences)) if pairs else None,
        "max_difference_point": maximum_detail,
        "regime_concordance_pct": _rounded(100 * concordant / len(pairs)) if pairs else None,
        "regime_confusion": {
            reference_regime: dict(sorted(counts.items()))
            for reference_regime, counts in sorted(confusion.items())
        },
        "first_divergence": first_divergence,
    }


def describe_series(points: Sequence[SeriesPoint]) -> dict:
    return {
        "rows": len(points),
        "operational_rows": sum(point.operational for point in points),
        "first_timestamp": points[0].timestamp if points else None,
        "last_timestamp": points[-1].timestamp if points else None,
        "value_fields": dict(sorted(Counter(point.value_field for point in points).items())),
        "timezone_contract": (
            "explicit_offset_normalized_to_utc"
            if points and points[0].aware
            else "naive_provider_axis"
        ),
    }


def build_parity_report(
    reference: Sequence[SeriesPoint],
    candidates: Mapping[str, Sequence[SeriesPoint]],
    *,
    tolerance: float = 0.5,
    buy_threshold: float = 60.0,
    sell_threshold: float = 40.0,
) -> dict:
    if sell_threshold >= buy_threshold:
        raise ValueError("sell_threshold precisa ser menor que buy_threshold")
    if tolerance < 0:
        raise ValueError("tolerance não pode ser negativa")

    candidate_reports = {}
    for name, points in candidates.items():
        candidate_reports[name] = {
            "series": describe_series(points),
            "all_bars": _compare_subset(
                reference,
                points,
                tolerance=tolerance,
                buy_threshold=buy_threshold,
                sell_threshold=sell_threshold,
            ),
            "operational_bars": _compare_subset(
                [point for point in reference if point.operational],
                [point for point in points if point.operational],
                tolerance=tolerance,
                buy_threshold=buy_threshold,
                sell_threshold=sell_threshold,
            ),
        }

    ranking_basis = "operational_bars.mae"
    rankable = [
        name
        for name, report in candidate_reports.items()
        if report["operational_bars"]["mae"] is not None
    ]
    if not rankable:
        ranking_basis = "all_bars.mae"
        rankable = [
            name for name, report in candidate_reports.items() if report["all_bars"]["mae"] is not None
        ]
    ranking = sorted(
        rankable,
        key=lambda name: candidate_reports[name][ranking_basis.split(".")[0]]["mae"],
    )

    return {
        "thresholds": {
            "sell": sell_threshold,
            "buy": buy_threshold,
            "divergence_tolerance_points": tolerance,
        },
        "reference": describe_series(reference),
        "candidates": candidate_reports,
        "ranking_basis": ranking_basis,
        "ranking_by_operational_mae": ranking,
    }


def _session_date_arg(raw: str) -> str:
    """Valida no argparse: o unit monta esta data por substituição de shell,
    então formato inválido tem que apontar a causa real em vez de virar
    traceback dentro de capture_brt_offset_h ou, pior, a mensagem enganosa
    'não contém nenhuma barra da sessão esperada 16/07/2026'."""
    try:
        return date.fromisoformat(raw.strip()).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--session-date precisa ser YYYY-MM-DD: {raw!r}"
        ) from exc


def _parse_named_source(raw: str) -> tuple[str, str]:
    name, separator, source = raw.partition("=")
    if not separator or not name.strip() or not source.strip():
        raise argparse.ArgumentTypeError("use NOME=ARQUIVO_OU_URL")
    return name.strip(), source.strip()


def _local_series_url(base_url: str, *, session_date: str, target: str, version: str) -> str:
    query = urlencode({"session_date": session_date, "target": target, "version": version})
    return f"{base_url.rstrip('/')}/api/irai/series?{query}"


def _local_runtime_revision_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/internal/p-dynamic-runtime-revision"


def _load_local_runtime_revision(base_url: str, *, timeout: float) -> dict[str, str]:
    """Lê a revisão congelada pelo MESMO processo que serve v1/v2."""
    payload = load_json_document(_local_runtime_revision_url(base_url), timeout=timeout)
    if not isinstance(payload, dict):
        raise ValueError("endpoint de revisão do motor retornou JSON inválido")
    return validate_engine_revision(payload.get("engine_revision"))


def _local_gex_url(base_url: str, *, target: str) -> str:
    return f"{base_url.rstrip('/')}/api/irai/gex?{urlencode({'target': target})}"


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _archive_raw_payload(base: Path, name: str, payload: bytes, source: str) -> dict:
    """Arquiva os bytes crus comprimidos e devolve sua procedência.

    A fonte pública é rolling: uma vez sobrescrita, o payload original é
    irrecuperável. Grava-se ANTES de qualquer validação -- se o filtro de sessão
    rejeitar tudo ou v1/v2 sumirem, main() aborta, e é exatamente aí que se
    quer ter o cru para entender o que a fonte mandou. Serve a auditoria, nunca
    a apuração: o avaliador lê exclusivamente manifest["files"].
    """
    relative = f"raw/{name}.json.gz"
    path = base / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    # mtime=0 mantém o .gz byte-estável entre execuções; o `with` no fileobj
    # garante flush/close determinístico antes do replace e do stat.
    with temporary.open("wb") as raw_file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as handle:
            handle.write(payload)
    temporary.replace(path)
    return {
        "file": relative,
        "source": source,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "compressed_bytes": path.stat().st_size,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-source", default=DEFAULT_PUBLIC_SOURCE)
    parser.add_argument("--local-api", default=DEFAULT_LOCAL_API)
    parser.add_argument("--skip-local-api", action="store_true")
    parser.add_argument(
        "--gex-source",
        default=None,
        help="Arquivo/URL de GEX; por padrão usa /api/irai/gex da API local.",
    )
    parser.add_argument(
        "--candidate",
        action="append",
        default=[],
        type=_parse_named_source,
        metavar="NOME=FONTE",
        help="Adiciona candidato de arquivo/URL; pode ser repetido.",
    )
    parser.add_argument(
        "--miqueias-static-config",
        default=None,
        help=(
            "Configuração JSON completa do challenger estático do Miqueias. "
            "Exige alpha, intercept, peso e sigma de cada fator."
        ),
    )
    parser.add_argument(
        "--miqueias-static-source",
        default=None,
        help=(
            "Série com o objeto factors para o challenger estático; por padrão usa "
            "a resposta local v2 carregada nesta execução."
        ),
    )
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument(
        "--session-date",
        required=True,
        type=_session_date_arg,
        help=(
            "Sessão esperada (YYYY-MM-DD), fornecida pelo chamador (o timer "
            "diário). Referência pública e séries locais que não contêm "
            "nenhuma barra dessa sessão são rejeitadas -- mesmo depois de "
            "esgotar o retry de transporte -- em vez de main() adotar "
            "silenciosamente a sessão errada."
        ),
    )
    parser.add_argument("--sell-threshold", type=float, default=40.0)
    parser.add_argument("--buy-threshold", type=float, default=60.0)
    parser.add_argument("--tolerance", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--capture-dir", default=None)
    parser.add_argument("--output-json", default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    session_date = args.session_date
    stamp = generated_at.replace(":", "").replace("+0000", "Z").replace("+00:00", "Z")
    capture_base = (
        Path(args.capture_dir) / session_date / stamp if args.capture_dir else None
    )
    if capture_base is not None:
        try:
            engine_revision = (
                current_engine_revision()
                if args.skip_local_api
                else _load_local_runtime_revision(args.local_api, timeout=args.timeout)
            )
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            print(f"Erro: captura sem revisão verificável do motor: {exc}", file=sys.stderr)
            return 1
    else:
        engine_revision = None
    raw_manifest: dict[str, dict] = {}

    def archive(name: str, payload: bytes, source: str) -> None:
        """Arquiva o cru sem perder o diagnóstico da captura.

        A falha é registrada e o manifesto ainda é gravado para forense, mas
        ``raw_archive_is_complete`` torna a sessão inelegível para o ledger.
        """
        if capture_base is None:
            return
        try:
            raw_manifest[name] = {
                "captured_at": generated_at,
                **_archive_raw_payload(capture_base, name, payload, source),
            }
        except Exception as exc:
            raw_manifest[name] = {
                "source": source,
                "captured_at": generated_at,
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(f"Aviso: falha ao arquivar cru de {name}: {exc}", file=sys.stderr)

    try:
        public_document, public_payload = load_json_document_with_bytes(
            args.public_source, timeout=args.timeout
        )
        archive(PUBLIC_MODEL, public_payload, args.public_source)
        public_rows = _extract_rows(public_document)
    except Exception as exc:
        print(f"Erro ao carregar referência pública: {exc}", file=sys.stderr)
        return 1

    candidate_rows: dict[str, list[dict]] = {}
    candidate_documents: dict[str, object] = {}
    candidate_sources: dict[str, str] = {}
    source_errors: dict[str, str] = {}
    static_challengers: dict[str, dict] = {}

    if not args.skip_local_api:
        for version in ("v1", "v2"):
            source = _local_series_url(
                args.local_api,
                session_date=session_date,
                target=args.target,
                version=version,
            )
            try:
                document, payload = load_json_document_with_bytes(
                    source, timeout=args.timeout
                )
                archive(version, payload, source)
                candidate_rows[version] = _extract_rows(document)
                candidate_documents[version] = document
                candidate_sources[version] = source
            except Exception as exc:
                source_errors[version] = f"{type(exc).__name__}: {exc}"

        # O checkout pode ser editado e a API pode reiniciar durante a captura.
        # A mesma revisão antes e depois das duas séries prova que v1/v2 vieram
        # de um único processo, não de duas versões do Kalman misturadas.
        if capture_base is not None:
            try:
                final_engine_revision = _load_local_runtime_revision(
                    args.local_api, timeout=args.timeout
                )
            except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
                print(
                    f"Erro: API sem revisão verificável após capturar v1/v2: {exc}",
                    file=sys.stderr,
                )
                return 1
            if final_engine_revision != engine_revision:
                print(
                    "Erro: a revisão do motor mudou durante a captura de v1/v2",
                    file=sys.stderr,
                )
                return 1

    for name, source in args.candidate:
        if name in RESERVED_CANDIDATE_NAMES:
            print(f"Erro: nome reservado de candidato: {name}", file=sys.stderr)
            return 1
        if name in candidate_rows:
            print(f"Erro: candidato duplicado: {name}", file=sys.stderr)
            return 1
        try:
            document, payload = load_json_document_with_bytes(
                source, timeout=args.timeout
            )
            archive(name, payload, source)
            candidate_rows[name] = _extract_rows(document)
            candidate_documents[name] = document
            candidate_sources[name] = source
        except Exception as exc:
            source_errors[name] = f"{type(exc).__name__}: {exc}"

    if args.miqueias_static_config:
        if "miqueias_static" in candidate_rows or "miqueias_static" in source_errors:
            print("Erro: nome reservado de candidato: miqueias_static", file=sys.stderr)
            return 1
        try:
            config_document = load_json_document(
                args.miqueias_static_config, timeout=args.timeout
            )
            static_config = load_miqueias_static_config(config_document)
            if static_config.target != args.target:
                raise ValueError(
                    f"configuração para {static_config.target} não pode ser usada no target {args.target}"
                )
            if args.miqueias_static_source:
                static_source = args.miqueias_static_source
                static_document = load_json_document(static_source, timeout=args.timeout)
            else:
                static_source = candidate_sources.get("v2")
                static_document = candidate_documents.get("v2")
                if static_document is None or static_source is None:
                    raise ValueError(
                        "miqueias_static requer --miqueias-static-source quando a série local v2 não está disponível"
                    )
            static_rows = build_miqueias_static_rows(
                _extract_rows(static_document), static_config
            )
            candidate_rows["miqueias_static"] = static_rows
            candidate_documents["miqueias_static"] = {
                "series": static_rows,
                "static_config": describe_miqueias_static_config(static_config),
            }
            candidate_sources["miqueias_static"] = (
                f"derived:{static_source} config:{args.miqueias_static_config}"
            )
            static_challengers["miqueias_static"] = {
                "input_source": static_source,
                "config_source": args.miqueias_static_config,
                "config": describe_miqueias_static_config(static_config),
            }
        except Exception as exc:
            print(f"Erro ao construir challenger miqueias_static: {exc}", file=sys.stderr)
            return 1

    # O offset sai do envelope já carregado (ou da regra sazonal) e precisa
    # existir ANTES de qualquer filtro: sem ele não há como saber a que dia
    # BRT uma barra pertence.
    brt_offset_h = capture_brt_offset_h(session_date, candidate_documents)

    try:
        public_rows = _session_rows(
            public_rows,
            session_date=session_date,
            brt_offset_h=brt_offset_h,
            label="referência pública (miqueias)",
        )
        reference = normalize_series(public_rows, value_fields=PUBLIC_VALUE_FIELDS)
        if not reference:
            raise ValueError("a referência pública não contém valores de P Dinâmico")
    except Exception as exc:
        print(f"Erro ao carregar referência pública: {exc}", file=sys.stderr)
        return 1
    public_document = _document_with_session_rows(public_document, public_rows)

    candidate_points: dict[str, list[SeriesPoint]] = {}
    for name in list(candidate_rows):
        try:
            rows = _session_rows(
                candidate_rows[name],
                session_date=session_date,
                brt_offset_h=brt_offset_h,
                label=f"série {name}",
            )
            points = normalize_series(rows, value_fields=LOCAL_VALUE_FIELDS)
            if not points:
                raise ValueError(f"série {name} não contém valores de P Dinâmico")
            candidate_points[name] = points
            candidate_documents[name] = _document_with_session_rows(
                candidate_documents[name], rows
            )
        except Exception as exc:
            source_errors[name] = f"{type(exc).__name__}: {exc}"
            candidate_documents.pop(name, None)
            candidate_sources.pop(name, None)
            static_challengers.pop(name, None)

    if not args.skip_local_api:
        missing_required = [
            version for version in ("v1", "v2") if version not in candidate_points
        ]
        if missing_required:
            for version in missing_required:
                detail = source_errors.get(version, "sem detalhe")
                print(
                    f"Erro: série local obrigatória ausente ({version}): {detail}",
                    file=sys.stderr,
                )
            return 1

    gex_source = args.gex_source
    if gex_source is None and not args.skip_local_api:
        gex_source = _local_gex_url(args.local_api, target=args.target)
    gex_document = None
    gex_error = None
    if gex_source:
        try:
            gex_document = load_json_document(gex_source, timeout=args.timeout)
        except Exception as exc:
            gex_error = f"{type(exc).__name__}: {exc}"

    try:
        comparison = build_parity_report(
            reference,
            candidate_points,
            tolerance=args.tolerance,
            buy_threshold=args.buy_threshold,
            sell_threshold=args.sell_threshold,
        )
    except ValueError as exc:
        print(f"Erro de contrato na comparação: {exc}", file=sys.stderr)
        return 1

    report = {
        "schema_version": 1,
        "generated_at": generated_at,
        "target": args.target,
        "session_date": session_date,
        "reference_source": args.public_source,
        "candidate_sources": candidate_sources,
        "source_errors": source_errors,
        "static_challengers": static_challengers,
        **comparison,
    }
    ranked = comparison["ranking_by_operational_mae"]
    metric_scope = comparison["ranking_basis"].split(".")[0]
    closest_candidates: list[str] = []
    if ranked:
        minimum_mae = comparison["candidates"][ranked[0]][metric_scope]["mae"]
        closest_candidates = [
            name
            for name in ranked
            if math.isclose(
                comparison["candidates"][name][metric_scope]["mae"],
                minimum_mae,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ]
    operational_comparable = any(
        candidate["operational_bars"]["common_rows"] > 0
        for candidate in comparison["candidates"].values()
    )
    report["conclusion"] = {
        "scope": "parity_only",
        "comparable": bool(ranked),
        "operational_comparable": operational_comparable,
        "closest_candidate": closest_candidates[0] if len(closest_candidates) == 1 else None,
        "closest_candidates": closest_candidates,
        "parity_tie": len(closest_candidates) > 1,
        "quality_winner": None,
        "reason": (
            f"menor MAE segundo {comparison['ranking_basis']}"
            if ranked
            else "nenhuma série candidata teve timestamps comuns com a referência"
        ),
        "promotion_warning": (
            "Proximidade com a curva do Miqueias não mede qualidade preditiva. "
            "A versão vencedora deve ser escolhida por avaliação OOS contra outcomes do WIN."
        ),
    }

    session_closed = None
    session_had_activity = None
    if capture_base is not None:
        capture_paths = {"miqueias": str(capture_base / "miqueias.json")}
        _write_json(Path(capture_paths["miqueias"]), public_document)
        for name, document in sorted(candidate_documents.items()):
            capture_paths[name] = str(capture_base / f"{name}.json")
            _write_json(Path(capture_paths[name]), document)
        capture_paths["gex"] = str(capture_base / "gex.json")
        stored_gex = gex_document if gex_document is not None else {
            "available": False,
            "reason": gex_error or "fonte GEX não configurada",
        }
        _write_json(Path(capture_paths["gex"]), stored_gex)
        official_points = {
            name: candidate_points[name]
            for name in TOURNAMENT_MODELS
            if name != PUBLIC_MODEL and name in candidate_points
        }
        source_session_status = build_source_statuses(
            {PUBLIC_MODEL: reference, **official_points},
            brt_offset_h=brt_offset_h,
        )
        local_statuses = [
            status for name, status in source_session_status.items()
            if name != PUBLIC_MODEL
        ]
        # Sem espalhar o status da pública no topo: publicar o
        # last_operational_brt dela ao lado do limiar local produzia manifesto
        # auto-contraditório. O detalhe por fonte vive em `sources`.
        intersection_stats = session_intersection_stats(
            {PUBLIC_MODEL: reference, **official_points},
            brt_offset_h=brt_offset_h,
        )
        raw_archive_complete = raw_archive_is_complete(raw_manifest)
        session_status = {
            "closed": bool(local_statuses)
            and all(status["closed"] for status in source_session_status.values())
            and intersection_stats["sufficient"]
            and raw_archive_complete,
            "raw_archive_complete": raw_archive_complete,
            "close_not_before_brt": CLOSE_NOT_BEFORE_BRT,
            "close_not_before_brt_public": CLOSE_NOT_BEFORE_BRT_PUBLIC,
            "open_not_after_brt": OPEN_NOT_AFTER_BRT,
            "coverage_min_ratio": COVERAGE_MIN_RATIO,
            "session_window_brt": f"{SESSION_OPEN_BRT}-{SESSION_CLOSE_BRT}",
            "session_expected_operational_rows": SESSION_EXPECTED_OPERATIONAL_ROWS,
            "min_operational_rows": math.ceil(
                COVERAGE_MIN_RATIO * SESSION_EXPECTED_OPERATIONAL_ROWS
            ),
            "intersection": intersection_stats,
            "closed_requirement": (
                "o cru de todas as fontes precisa estar arquivado (sem ele a "
                "sessão não é reprodutível); a condição VINCULANTE de cobertura "
                "é a interseção pontuável, que precisa ter "
                "ao menos "
                f"{math.ceil(COVERAGE_MIN_RATIO * SESSION_EXPECTED_OPERATIONAL_ROWS)} "
                "barras; e cada fonte precisa abrir até "
                f"{OPEN_NOT_AFTER_BRT}, cobrir ao menos "
                f"{math.ceil(COVERAGE_MIN_RATIO * SESSION_EXPECTED_OPERATIONAL_ROWS)} "
                f"das {SESSION_EXPECTED_OPERATIONAL_ROWS} barras da grade "
                f"{SESSION_OPEN_BRT}-{SESSION_CLOSE_BRT} BRT e encerrar até "
                f"{CLOSE_NOT_BEFORE_BRT} BRT ({CLOSE_NOT_BEFORE_BRT_PUBLIC} para a "
                "referência pública, que tolera uma barra de atraso); e ao menos "
                "uma fonte local"
            ),
            "sources": source_session_status,
        }
        session_closed = session_status["closed"]
        session_had_activity = any(
            status["operational_rows"] > 0 for status in source_session_status.values()
        )
        walls = gex_document.get("walls", []) if isinstance(gex_document, dict) else []
        gex_status = {
            "status": "captured" if gex_document is not None else "unavailable",
            "source": gex_source,
            "error": gex_error,
            "active": gex_document.get("active") if isinstance(gex_document, dict) else None,
            "as_of": gex_document.get("as_of") if isinstance(gex_document, dict) else None,
            "wall_count": sum(wall.get("type") == "wall" for wall in walls),
            "mid_wall_count": sum(wall.get("type") == "mid_wall" for wall in walls),
        }
        # A elegibilidade só é conhecida agora; o arquivo já foi gravado lá atrás.
        for name, entry in raw_manifest.items():
            entry["eligible"] = (
                session_closed
                and "error" not in entry
                and bool(source_session_status.get(name, {}).get("closed", False))
            )
        capture_paths["report"] = str(capture_base / "report.json")
        capture_paths["manifest"] = str(capture_base / "manifest.json")
        report["capture_paths"] = capture_paths
        report["capture_bundle"] = str(capture_base)
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "methodology_version": METHODOLOGY_VERSION,
            "engine_revision": engine_revision,
            "captured_at": generated_at,
            "session_date": session_date,
            "target": args.target,
            "objective": {
                "primary": (
                    "probabilidade de o último print operacional em sessão "
                    "(<= 17:55 BRT) fechar acima da abertura; a captura roda 17:56 "
                    "e por construção não observa o leilão de fechamento"
                ),
                "tactical_gate": "avaliado separadamente após regra econômica determinística",
            },
            "session": {**session_status, "brt_offset_h": brt_offset_h},
            "models": ["miqueias", *sorted(candidate_documents)],
            "sources": {
                "miqueias": args.public_source,
                **candidate_sources,
            },
            "source_errors": source_errors,
            "gex": gex_status,
            "files": {name: Path(path).name for name, path in capture_paths.items()},
            "raw": raw_manifest,
            "raw_contract": (
                "payload cru pré-filtro, só para auditoria e reprocessamento; "
                "a apuração consome exclusivamente files"
            ),
        }
        _write_json(Path(capture_paths["report"]), report)
        _write_json(Path(capture_paths["manifest"]), manifest)
    if args.output_json:
        _write_json(Path(args.output_json), report)

    print(f"Referência Miqueias: {len(reference)} barras ({session_date})")
    for name in sorted(candidate_points):
        result = comparison["candidates"][name]
        operational = result["operational_bars"]
        all_bars = result["all_bars"]
        chosen = operational if operational["common_rows"] else all_bars
        scope = "operacionais" if operational["common_rows"] else "todas"
        print(
            f"{name}: {chosen['common_rows']} barras {scope}, "
            f"corr={chosen['correlation']}, MAE={chosen['mae']}, "
            f"regime={chosen['regime_concordance_pct']}%"
        )
    for name, error in sorted(source_errors.items()):
        print(f"{name}: indisponível — {error}")
    if ranked:
        if len(closest_candidates) > 1:
            print(
                f"Empate de paridade: {', '.join(closest_candidates)} "
                f"({comparison['ranking_basis']})"
            )
        else:
            print(f"Mais próximo: {closest_candidates[0]} ({comparison['ranking_basis']})")
    else:
        print("Paridade ainda não calculável: nenhuma série candidata comparável.")

    # Sessão PARCIAL (abriu e não fechou) é anomalia e precisa alertar: sair 0
    # faria o systemd marcar sucesso e ninguém perceberia o dia perdido. Já a
    # ausência TOTAL de barra operacional não é anomalia -- é feriado B3 (que
    # cai dentro do Mon..Fri do timer) ou captura antes da
    # abertura (o timer roda com Persistent=false justamente para não disparar
    # captura fora de hora). Alertar aqui produziria falha recorrente e ensinaria o
    # operador a ignorar justamente o alerta que este código existe para dar.
    if session_closed is False and session_had_activity:
        if not ranked:
            print(
                "Paridade não calculável e sessão incompleta no mesmo dia.",
                file=sys.stderr,
            )
        print(
            f"Erro: sessão {session_date} abriu mas não fechou operacionalmente; "
            "bundle gravado mas inelegível para o ledger",
            file=sys.stderr,
        )
        return EXIT_SESSION_NOT_CLOSED
    if session_closed is False:
        print(
            f"Aviso: nenhuma barra operacional em {session_date} "
            "(feriado ou captura antes da abertura); bundle gravado, sem sessão a apurar"
        )
    return 0 if ranked else EXIT_NOT_COMPARABLE


if __name__ == "__main__":
    raise SystemExit(main())
