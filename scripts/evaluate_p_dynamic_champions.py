#!/usr/bin/env python3
"""Avalia Miqueias, IRAI v1/v2 e challengers no objetivo diário do P Dinâmico.

O torneio usa somente bundles fechados produzidos por
``scripts/compare_p_dynamic_parity.py --capture-dir ...``. Cada sessão tem o
mesmo peso: Brier e log-loss são calculados por barra operacional e primeiro
agregados dentro da sessão, evitando que dias com mais prints dominem o placar.

Um primeiro lugar no ranking não basta para promoção. O default exige pelo
menos 60 sessões comuns e DOIS testes sequenciais anytime-valid, cada um
respondendo uma pergunta estatística diferente sobre ``delta Brier =
brier[candidato] - brier[oponente]`` contra TODOS os concorrentes (teste de
interseção-união, Berger 1982, com alpha=0,05 reservado por candidato-em-
espera -- ``alpha/K`` entre os K modelos, já que qual modelo vira
"candidato" é escolhido pelo próprio ranking observado, ver
``evaluate_champions``). Os dois testes rodam sobre a MESMA série de
``delta`` -- não são estatisticamente independentes como variáveis aleatórias
(compartilham os dados, e tendem a concordar quando o efeito é forte). O que
é de fato independente/não-compartilhado é a RESERVA DE ALPHA: cada teste tem
seu próprio orçamento alpha=0,05/K, nenhum divide o do outro -- essa
independência de alocação (não de dados) é o que licencia o union bound
usado no ``combined_family_wise_alpha_bound`` do relatório (ver mais abaixo),
que não depende de os testes serem independentes entre si:

- ``sequential_winner`` -- betting/e-process empirical-Bernstein (Waudby-
  Smith & Ramdas, "Estimating means of bounded random variables by betting",
  JRSS-B, publicado online em 2023). Rejeita, sessão a sessão, ``H0: E[delta_i
  | sessões anteriores] >= 0``, o nulo CONDICIONAL. Evidência sequencial sob o
  nulo condicional, útil para detectar vantagem sustentada no regime
  observado.
- ``long_run_winner`` -- boundary polynomial-stitching (Howard, Ramdas,
  McAuliffe & Sekhon, "Time-uniform, nonparametric, nonasymptotic confidence
  sequences", Annals of Statistics 49(2), 2021). Rejeita ``H0: mu_t >= 0``
  onde ``mu_t = t^-1 * soma_{i=1}^{t} E[delta_i | F_{i-1}]`` -- a MÉDIA DAS
  EXPECTATIVAS CONDICIONAIS ao longo do tempo (uma sequência PREDITÍVEL, não
  a média marginal/populacional clássica de uma amostra i.i.d.; as duas só
  coincidem de forma EXATA sob uma hipótese mais forte que estacionariedade
  pura -- ex.: expectativa condicional constante -- e coincidem de forma
  ASSINTÓTICA (``t -> infinito``, não para ``t`` finito) sob estacionariedade
  E ERGODICIDADE, pelo teorema ergódico; estacionariedade sozinha garante
  apenas que a distribuição MARGINAL de cada ``delta_i`` não muda com ``i``,
  não que ``mu_t`` convirja para essa média). Chamamos essa quantidade de
  "longo prazo"/"acumulada" no resto deste módulo por brevidade -- é a leitura
  aproximada correta em regime estacionário e ergódico, quando ``t`` é grande,
  e o motivo prático de tratá-la como o baseline estratégico -- mas o
  Theorem 4 do paper cobre ``mu_t`` exatamente como definido acima, SEM
  exigir estacionariedade nem ergodicidade.

Os dois protegem contra o "peeking" diário do agendador systemd que reagrega
este ledger toda sessão (ambos são anytime-valid, cobertura desde t=1), mas
NÃO respondem a mesma pergunta -- ver os dois parágrafos abaixo.

RESTRIÇÃO VINCULANTE (decisão de produto, IRAI-18): ``sequential_winner`` é
evidência TÁTICA, nunca autoridade de promoção. Ele pode ser exibido, mas não
deve responder sozinho "qual versão usar em produção" -- essa pergunta é do
``long_run_winner``. Motivo: a garantia de Ville do WSR cobre o nulo
CONDICIONAL sessão a sessão, não uma alegação sobre a média marginal/
histórica acumulada. Um regime de mercado que alterne fortemente o sinal do
delta ao longo do tempo pode cruzar o limiar do WSR mesmo com média acumulada
verdadeira exatamente zero -- verificado numericamente (180 sessões de -0,10,
30 alternando -0,80/+0,80, 20 de +0,90; média exata 0; log-capital final 5,83
> limiar log(1/alpha_candidato)=4,38, cruzando já em t=61; ver
``tests/test_empirical_bernstein_sequential_test.py::test_regime_alternante_pode_cruzar_o_limiar_com_media_acumulada_exatamente_zero``).
Isso não é um bug do WSR: é a natureza de um teste sobre o nulo condicional,
não sobre a média histórica -- ``sequential_winner`` significa "nenhum trecho
da evidência causal pareceu convincentemente ruim", não "a média de longo
prazo é positiva".

``long_run_winner``/HRMS não é um substituto de menor poder para o WSR: ele
testa NATIVAMENTE a pergunta de longo prazo (perda média acumulada), e a
mesma sequência adversarial acima NÃO cruza o limiar do HRMS (ver o teste
espelhado em ``tests/test_hrms_sequential_test.py``) -- mas não porque
``mu_t`` seja zero em todo prefixo da série (não é: a soma até t=180, fim da
fase constante de -0,10, é -18,0, não zero -- só o agregado FINAL, t=230, é
exatamente zero). O mecanismo real, confirmado rodando ``_hrms_bound_trace``
sessão a sessão: enquanto ``delta`` é constante (t=1..180), ``mu_hat_{i-1}``
rastreia exatamente esse valor a partir de i=2, então ``V_t`` (tempo
intrínseco) fica travado em ~0,01 -- abaixo do piso ``HRMS_V_MIN=0,06`` --,
e o termo de boundary fica CONSTANTE nessa fase inteira (~26,6554,
independente de ``t``; não cresce com variância acumulada, porque quase não
há variância a acumular). Como ``UCB(t) = (S_t + 26,6554)/t = -0,10 +
26,6554/t`` decresce monotonicamente em ``t`` enquanto a fase dura, o mínimo
da UCB sobre toda a janela considerada (``min_sessions=60`` em diante) cai
exatamente no maior ``t`` dessa fase, ``t=180`` -- não porque a média
acumulada ali já seja zero (é -0,10), mas porque é o último ponto antes do
boundary voltar a crescer: assim que os saltos ``+-0,80`` começam (t=181),
``V_t`` salta de 0,01 para 0,50 num único passo e segue crescendo, empurrando
a UCB de volta para cima (0,097 em t=181, 0,147 em t=210, 0,238 em t=230) até
nunca mais se aproximar de zero. A UCB nesse mínimo ainda é positiva mas com
folga estreita SÓ depois de normalizada por ``t`` (``(26,6554-18)/180 ~=
0,048``) -- a folga bruta do boundary sobre o déficit acumulado
(``26,6554-18=8,6554``) não é pequena por si só. Como os dois campos usam
janelas de alpha
INDEPENDENTES (cada um a alpha=0,05/K por candidato), a probabilidade
combinada de QUALQUER UM dos dois campos apontar um vencedor errado é
limitada por união de eventos a <= 2*alpha -- não a alpha (ver
``combined_family_wise_alpha_bound`` no relatório). Recorte e metodologia
usados no HRMS (c=2, s=1.4, eta=2, v_min) são versionados via
``schema_version`` (ver ``main``); a construção pode ser recalculada sobre a
mesma série do ledger append-only sem descartar dados nem reiniciar a
coleta.

Não descarte estes dois parágrafos como redundantes: a distinção entre as
duas perguntas -- "o edge se sustentou no regime observado" (tático,
``sequential_winner``) vs. "qual modelo é melhor em média no longo prazo"
(estratégico, ``long_run_winner``) -- é o motivo do split em dois campos, e
não deve ser recolapsada num único ``quality_winner`` ambíguo (ver ADR /
backlog do IRAI-18 para o histórico completo da decisão).

Adicionar um challenger novo ao vivo abriria um novo e-process/"epoch" sem
alocação de alpha entre epochs -- não implementado, e não necessário enquanto
o loader mantiver o trio fixo (ver ``load_ledger_sessions``/
``TOURNAMENT_MODELS`` em ``compare_p_dynamic_parity``). A utilidade como gate
da estratégia manual é outra pergunta e permanece explicitamente
``NOT_EVALUATED`` aqui.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.compare_p_dynamic_parity import (
    LOCAL_VALUE_FIELDS,
    PUBLIC_VALUE_FIELDS,
    _document_with_session_rows,
    _extract_rows,
    _parse_timestamp,
    _session_rows,
    METHODOLOGY_VERSION,
    PUBLIC_MODEL,
    TOURNAMENT_MODELS,
    build_source_statuses,
    canonical_session_slots,
    capture_brt_offset_h,
    in_session_brt,
    normalize_series,
    session_intersection_stats,
    session_operational_points,
)
from backend.irai.runtime_revision import (
    prediction_revision_fingerprint,
    validate_engine_revision,
)


DEFAULT_MIN_SESSIONS = 60
DEFAULT_ALPHA = 0.05
EPSILON = 1e-6
LOCAL_TOURNAMENT_MODELS = ("v1", "v2")

# Constante do gate empirical-Bernstein (betting/e-process, inspirado em
# Waudby-Smith & Ramdas, JRSS-B, publicado online em 2023). O produto
# martingale 1 + lambda*(0.5 - x), x em [0,1], exige lambda < 2 para
# positividade no pior caso (x=1). 1.5 mantém 25% de margem desse limite --
# o suficiente para não colar no teto sem sacrificar poder no regime de
# variância baixa (que é o regime real deste torneio: variância do delta
# pareado observada é ~1000x menor que o pior-caso). Este valor foi fixado
# ANTES e INDEPENDENTE do ledger real de produção, calibrado só contra um
# fixture sintético determinístico -- só afeta poder, nunca a validade de
# Type-I (qualquer teto predeterminado preserva a garantia de Ville). Mas
# reajustá-lo DEPOIS de examinar o ledger real e então testar esse mesmo
# ledger sem correção pela seleção invalidaria a garantia (multiplicidade
# oculta por tuning nos mesmos dados) -- não fazer isso.
LAMBDA_MAX = 1.5
# Piso DEFENSIVO da variância preditiva plug-in: evita divisão por (quase)
# zero em cenários degenerados. Na prática, o pseudo-count 0,25 já mantém
# var_pred = (0,25 + soma_dos_quadrados)/i >> 1e-6 até i ~ 250 mil sessões
# (inatingível: 250 mil sessões de 5min úteis levariam décadas). Quem limita
# a velocidade de convergência neste torneio é o pseudo-count e o teto
# LAMBDA_MAX, não este piso -- ele é só defesa em profundidade (ex.: contra
# um `sum_sq` negativo por bug futuro), não algo que hoje entra em jogo.
VARIANCE_FLOOR = 1e-6

# Constantes do gate HRMS (boundary polynomial-stitching, Howard, Ramdas,
# McAuliffe & Sekhon 2021, "Time-uniform, nonparametric, nonasymptotic
# confidence sequences", Annals of Statistics 49(2), DOI 10.1214/20-AOS1991).
# eta=2 e s=1.4 são os defaults do paper/pacote de referência dos autores
# (confseq), fixados a priori -- mesmo princípio do LAMBDA_MAX acima: nunca
# tunados depois de olhar o ledger real.
HRMS_ETA = 2.0
HRMS_S = 1.4
# c = "sub-gamma scale parameter" (nomenclatura do próprio pacote de
# referência dos autores, confseq::poly_stitching_bound). NÃO é a redução
# clássica de Bennett-para-sub-gamma (c=b/3) -- essa reduação vale para OUTRA
# construção do mesmo paper (Corollary 3, LIL de autovalor máximo de matriz,
# incrementos de martingale limitados por b). A construção usada aqui é a do
# Theorem 4 do paper (empirical-Bernstein AUTONORMALIZADO, prova por
# self-normalization, não pela redução de Bennett) e o próprio enunciado do
# Theorem 4 fixa a escala como c = b-a diretamente (não b/3): "let u be any
# sub-exponential uniform boundary with crossing probability alpha for scale
# c = b-a" -- confirmado também pelo exemplo numérico do paper logo após o
# teorema, que usa c=1 para X_i em [0,1] (onde b-a=1, batendo com c=b-a, não
# c=(b-a)/3=1/3). Nosso delta_i vive em [-1,1], logo b-a=2 e c=2. (Um valor
# anterior desta constante, c=2/3, aplicava por engano a redução de Bennett
# do Corollary 3 -- teorema errado do mesmo paper -- em vez do Theorem 4 que
# de fato está implementado; achado por revisão externa via /codex-r,
# verificado contra o texto do Theorem 4 extraído do PDF arXiv:1810.08240.)
HRMS_C = 2.0
# zeta(1.4) (função zeta de Riemann) -- constante fixa da fórmula do boundary
# (não depende do ledger). Valor fixo hardcoded em vez de calculado em
# runtime (evita depender de scipy só por esta constante); verificado via
# `scipy.special.zeta(1.4, 1)` = 3.1055472779775815.
HRMS_ZETA_S = 3.1055472779775815
# v_min ancora onde o boundary fica mais apertado -- só afeta poder, nunca
# validade (é log-log-dependente, só precisa da ordem de grandeza correta).
# Fixado a priori como sessões_do_gate * variância_típica_do_delta_pareado;
# variância típica ~1e-3 (delta = brier[candidato]-brier[oponente], não a
# variável x=(delta+1)/2 usada no WSR) vem do mesmo fato já documentado acima
# para LAMBDA_MAX ("~1000x menor que o pior-caso": pior-caso de Var(x) em
# [0,1] é 0,25 por Popoviciu, e Var(delta)=4*Var(x) porque delta=2x-1). Este
# floor pressupõe que ``running_v`` (ver ``_hrms_bound_trace``) é uma SOMA
# CRUA (não diluída) de resíduos ao quadrado -- por isso cresce ~linear em n,
# e ``min_sessions * variância_típica`` é a ordem de grandeza esperada em
# n=min_sessions, não uma constante arbitrária.
HRMS_V_MIN = DEFAULT_MIN_SESSIONS * 1e-3
# NÃO existe um "prior/pseudo-contagem" somado a cada passo em running_v (uma
# versão anterior desta constante, HRMS_VARIANCE_PRIOR, foi removida: somar um
# prior DILUÍDO por índice -- (prior+sum_sq)/index -- a cada passo e acumular
# o resultado produz um artefato de crescimento logarítmico em running_v
# mesmo sob variância verdadeira zero, pois cada termo somado já é uma média
# corrente re-diluída -- soma de n termos ~1/i cresce como ln(n), não como a
# "variância acumulada" que o boundary espera. Confirmado contra DUAS
# implementações independentes de referência dos próprios autores
# (github.com/gostevehoward/confseq): ``predmix_empbern_lower_cs`` e
# ``conjmix_empbern_lower_cs`` (ambas em construções diferentes de
# polynomial-stitching, mas convergindo na MESMA convenção de "tempo
# intrínseco" empirical-Bernstein) computam
# ``V_t = cumsum((x_i - mu_hat_{i-1})^2)`` -- soma CRUA, sem divisão por
# índice e sem prior somado a cada termo -- e o próprio docstring de
# ``conjmix_empbern_lower_cs`` confirma a escala esperada (``v_opt =
# t*sigma^2``, ou seja, linear em t, nunca log(t)). O floor externo
# ``HRMS_V_MIN`` acima já cumpre o papel de regularização de amostra pequena
# que a constante removida tentava (incorretamente) cumprir por passo.
# Versão do SCHEMA do relatório deste avaliador (champion_report.json) --
# distinta de METHODOLOGY_VERSION (importado de compare_p_dynamic_parity, que
# versiona a CAPTURA do ledger, não a avaliação). Bump de 2 para 3 no split
# quality_winner -> sequential_winner + long_run_winner (IRAI-18): qualquer
# consumidor de relatórios antigos (schema_version=2, campo quality_winner)
# precisa saber que o formato mudou antes de reprocessar.
REPORT_SCHEMA_VERSION = 3


@dataclass(frozen=True)
class LedgerSession:
    session_date: str
    actual_up: bool
    forecasts: Mapping[str, Sequence[float]]


def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as source_file:
        return json.load(source_file)


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _engine_revision_from_manifest(manifest: dict) -> tuple[str, dict[str, str]]:
    """Valida a identidade do motor que produziu um bundle do ledger.

    ``methodology_version`` protege a regra de apuração. Ela não diz qual
    implementação gerou p_up. Sem este contrato, um restart após alterar o
    Kalman poderia acumular sessões incompatíveis no mesmo torneio.
    """
    try:
        normalized = validate_engine_revision(manifest.get("engine_revision"))
    except ValueError as exc:
        raise ValueError(f"manifesto sem revisão verificável do motor: {exc}") from exc

    fingerprint = prediction_revision_fingerprint(normalized)
    return fingerprint, normalized


def _outcome_rows(document, *, brt_offset_h: int) -> list[dict]:
    """Desfecho do WIN sobre as barras EM SESSÃO das fontes locais.

    Base deliberadamente distinta da interseção usada na pontuação: o rótulo é
    propriedade do MERCADO, não dos modelos. Ancorá-lo na última barra pontuada
    tornaria o alvo endógeno à disponibilidade do feed de terceiro e vazaria o
    preço quase-determinante para dentro do próprio rótulo. O que as duas bases
    partilham é a janela de pregão -- e é isso que importa.

    Sem essa janela, a barra de after-market (mesma data BRT, logo dentro do
    bundle, mas fora da pontuação) fixava o rótulo com um preço que nenhuma
    barra pontuada viu. Medido em data/irai.db: 40 de 1253 sessões (3,2%; 4,7%
    entre as 844 que têm barra após 18:00). O filtro é indispensável no regime
    de inverno, quando brt_offset_h=5 faz o payload cobrir até 18:55 BRT; com
    offset 6 quem carrega é o piso de 09:00, porque as barras de pré-mercado
    trazem o win_open da sessão ANTERIOR.
    """
    rows = [
        row
        for row in _extract_rows(document)
        if not row.get("is_ghost", False)
        and not row.get("is_preview", False)
        and row.get("win_open") is not None
        and row.get("win_current") is not None
        and in_session_brt(row.get("timestamp"), brt_offset_h=brt_offset_h)
    ]
    return rows


def _actual_outcome(local_documents, *, brt_offset_h: int) -> tuple[bool, str]:
    """Rótulo sobre a última barra comum às fontes locais.

    Preferir v2 incondicionalmente fazia o rótulo depender de qual documento
    estava mais completo: com v2 fechando 17:50 e v1 17:55, ambos elegíveis,
    a mesma sessão rendia desfechos diferentes -- 8 de 1253 sessões no banco.
    """
    by_source = {
        name: {
            _parse_timestamp(row["timestamp"])[0]: row
            for row in _outcome_rows(document, brt_offset_h=brt_offset_h)
        }
        for name, document in local_documents.items()
    }
    by_source = {name: rows for name, rows in by_source.items() if rows}
    missing_sources = sorted(set(LOCAL_TOURNAMENT_MODELS) - set(by_source))
    if missing_sources:
        raise ValueError(
            "fontes locais sem preço operacional para formar o outcome: "
            + ", ".join(missing_sources)
        )
    common = sorted(set.intersection(*(set(rows) for rows in by_source.values())))
    if not common:
        raise ValueError("fontes locais não compartilham barra em sessão para o outcome")
    def price(timestamp: str, field: str) -> float:
        values = {
            float(rows[timestamp][field])
            for rows in by_source.values()
            if timestamp in rows
        }
        if len(values) > 1:
            raise ValueError(
                f"fontes locais divergem em {field} na barra {timestamp}: "
                + ", ".join(str(value) for value in sorted(values))
            )
        return values.pop()

    return (
        price(common[-1], "win_current") > price(common[0], "win_open"),
        common[-1],
    )


def _validate_raw_archive(manifest: dict, bundle: Path) -> None:
    """Garante que o bundle elegível ainda possui os bytes auditáveis do trio.

    O capturador já marca falhas de arquivo como inelegíveis. Esta validação no
    leitor evita que manifesto corrompido ou forjado revogue essa decisão.
    """
    session = manifest.get("session")
    if not isinstance(session, dict) or session.get("raw_archive_complete") is not True:
        raise ValueError("cru não arquivado, sessão não é reprodutível")
    raw_entries = manifest.get("raw")
    if not isinstance(raw_entries, dict):
        raise ValueError("manifesto sem entradas de cru auditáveis")

    bundle_root = bundle.resolve()
    for name in TOURNAMENT_MODELS:
        entry = raw_entries.get(name)
        if not isinstance(entry, dict) or "error" in entry:
            raise ValueError(f"cru ausente ou falho para {name}")
        relative_path = entry.get("file")
        expected_sha256 = entry.get("sha256")
        expected_size = entry.get("bytes")
        if (
            not isinstance(relative_path, str)
            or not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
        ):
            raise ValueError(f"metadado de cru inválido para {name}")
        raw_path = (bundle / relative_path).resolve()
        try:
            raw_path.relative_to(bundle_root)
        except ValueError as exc:
            raise ValueError(f"caminho de cru fora do bundle para {name}") from exc

        digest = hashlib.sha256()
        actual_size = 0
        try:
            with gzip.open(raw_path, "rb") as source:
                while chunk := source.read(64 * 1024):
                    digest.update(chunk)
                    actual_size += len(chunk)
        except OSError as exc:
            raise ValueError(f"cru ilegível para {name}: {exc}") from exc
        if digest.hexdigest() != expected_sha256 or actual_size != expected_size:
            raise ValueError(f"integridade do cru inválida para {name}")


def _normalized_model_series(documents) -> dict[str, list]:
    """Normaliza cada documento uma vez, com o contrato de campo correto."""
    return {
        model: normalize_series(
            _extract_rows(document),
            value_fields=(
                PUBLIC_VALUE_FIELDS if model == PUBLIC_MODEL else LOCAL_VALUE_FIELDS
            ),
        )
        for model, document in documents.items()
    }


def _aligned_forecasts(
    normalized_models, *, brt_offset_h: int, minimum_rows: int
) -> dict[str, list[float]]:
    """Pontua todos os modelos exatamente nas MESMAS barras.

    Média sobre as barras que cada modelo por acaso tem não é comparação: as
    barras da manhã valem Brier ~0,25 (P≈0,5) e as do fim valem quase zero,
    então quem perde manhã ganha score de graça. Sob degradação simulada (perder
    as 10 piores barras) o ganho chega a +0,066 de Brier, várias vezes a margem
    que decide o torneio; nos dois bundles preservados, onde a divergência real
    era de uma barra, o efeito antigo->novo é de apenas +0,00015 e o ranking não
    muda. Alinhar por timestamp elimina a exposição na origem, em vez de tentar
    contê-la com limiar de elegibilidade.
    """
    by_model: dict[str, dict[str, float]] = {}
    for model, normalized_points in normalized_models.items():
        points = session_operational_points(
            normalized_points,
            brt_offset_h=brt_offset_h,
        )
        series = {}
        for point in points:
            probability = point.value / 100.0
            if not 0.0 <= probability <= 1.0:
                raise ValueError(
                    f"P_up fora de [0,100] em {point.timestamp}: {point.value}"
                )
            series[point.timestamp] = probability
        if not series:
            raise ValueError(f"modelo {model} sem forecasts operacionais na sessão")
        by_model[model] = series

    if not by_model:
        raise ValueError("bundle sem modelos para alinhar")
    common = set.intersection(*(set(series) for series in by_model.values()))
    ordered = sorted(common)
    covered = canonical_session_slots(ordered, brt_offset_h=brt_offset_h)
    if len(covered) < minimum_rows:
        raise ValueError(
            "interseção pontuável insuficiente: "
            f"{len(covered)} slots M5 < {minimum_rows} exigidos"
        )
    return {
        model: [series[timestamp] for timestamp in ordered]
        for model, series in by_model.items()
    }


def load_ledger_sessions(root: str | Path) -> tuple[list[LedgerSession], dict]:
    """Seleciona a captura fechada mais recente de cada sessão."""
    root = Path(root)
    manifests = sorted(root.glob("**/manifest.json"))
    audit = {
        "manifest_bundles": len(manifests),
        "incomplete_bundles": 0,
        "closed_bundles": 0,
        "invalid_bundles": 0,
        "selected_sessions": 0,
        "superseded_bundles": 0,
        "foreign_version_bundles": 0,
        "mixed_engine_revision_bundles": 0,
        "engine_revision_groups": {},
        "outcome_timestamps": {},
        "invalid_reasons": [],
        "dropped_models": [],
    }
    latest_by_session: dict[str, tuple[str, Path, dict]] = {}
    for manifest_path in manifests:
        try:
            manifest = _read_json(manifest_path)
            captured_methodology = manifest.get("methodology_version", 1)
            if not isinstance(captured_methodology, int) or isinstance(
                captured_methodology, bool
            ):
                raise ValueError(
                    f"methodology_version precisa ser inteiro: {captured_methodology!r}"
                )
            if captured_methodology != METHODOLOGY_VERSION:
                # Futuro também é incompatível: um rollback só do avaliador
                # agregaria bundles de régua mais nova como se fossem desta.
                key = (
                    "superseded_bundles"
                    if captured_methodology < METHODOLOGY_VERSION
                    else "foreign_version_bundles"
                )
                audit[key] += 1
                continue
            if not manifest.get("session", {}).get("closed", False):
                audit["incomplete_bundles"] += 1
                continue
            _engine_revision_from_manifest(manifest)
            audit["closed_bundles"] += 1
            session_date = str(manifest["session_date"])
            captured_at = str(manifest.get("captured_at", ""))
            previous = latest_by_session.get(session_date)
            if previous is None or captured_at > previous[0]:
                latest_by_session[session_date] = (captured_at, manifest_path, manifest)
        except Exception as exc:
            audit["invalid_bundles"] += 1
            audit["invalid_reasons"].append(f"{manifest_path}: {type(exc).__name__}: {exc}")

    sessions = []
    revisions_by_session: dict[str, tuple[str, dict[str, str]]] = {}
    for session_date, (_, manifest_path, manifest) in sorted(latest_by_session.items()):
        try:
            revision_fingerprint, revision = _engine_revision_from_manifest(manifest)
            bundle = manifest_path.parent
            files = manifest.get("files", {})
            documents = {
                model: _read_json(bundle / files[model])
                for model in manifest.get("models", [])
                if model in files
            }
            missing_core = sorted(set(TOURNAMENT_MODELS) - set(documents))
            if missing_core:
                raise ValueError(
                    "bundle sem os participantes obrigatórios do torneio: "
                    + ", ".join(missing_core)
                )
            _validate_raw_archive(manifest, bundle)
            brt_offset_h = capture_brt_offset_h(session_date, documents)
            # Defesa em profundidade: bundles gravados antes do filtro por
            # sessão BRT carregam a cauda da sessão anterior. Hoje ela é toda
            # ghost/preview e não pontua, mas depender desse flag para a
            # integridade do ledger é frágil -- barra de outra sessão não pode
            # entrar em Brier/log-loss nem definir o outcome do WIN.
            # Isola por modelo: um challenger esporádico sem barras da sessão
            # (ex.: rodada manual com --miqueias-static-config no mesmo
            # capture-dir) não pode derrubar miqueias/v1/v2 válidos e custar
            # uma sessão do gate de 60.
            session_documents = {}
            for model, document in documents.items():
                try:
                    session_documents[model] = _document_with_session_rows(
                        document,
                        _session_rows(
                            _extract_rows(document),
                            session_date=session_date,
                            brt_offset_h=brt_offset_h,
                            label=f"{model} no bundle de {session_date}",
                        ),
                    )
                except Exception as exc:
                    audit["dropped_models"].append(
                        f"{manifest_path}: {model}: {type(exc).__name__}: {exc}"
                    )
            missing_essential = sorted(
                set(TOURNAMENT_MODELS) - set(session_documents)
            )
            if missing_essential:
                raise ValueError(
                    "fontes essenciais sem barras da sessão: "
                    + ", ".join(missing_essential)
                )
            official_documents = {
                name: session_documents[name] for name in TOURNAMENT_MODELS
            }
            normalized_models = _normalized_model_series(official_documents)
            source_statuses = build_source_statuses(
                normalized_models,
                brt_offset_h=brt_offset_h,
            )
            incomplete_sources = sorted(
                model for model, status in source_statuses.items() if not status["closed"]
            )
            if incomplete_sources:
                raise ValueError(
                    "fontes sem fechamento operacional: " + ", ".join(incomplete_sources)
                )
            intersection = session_intersection_stats(
                normalized_models,
                brt_offset_h=brt_offset_h,
            )
            if not intersection["sufficient"]:
                raise ValueError(
                    "interseção pontuável insuficiente: "
                    f"{intersection['canonical_slots_covered']} slots M5 cobertos "
                    f"< {intersection['min_rows']} exigidos "
                    f"(gap máximo {intersection['max_gap_minutes']}min)"
                )
            forecasts = _aligned_forecasts(
                normalized_models,
                brt_offset_h=brt_offset_h,
                minimum_rows=intersection["min_rows"],
            )
            if len(forecasts) < 2:
                raise ValueError("bundle precisa de pelo menos dois modelos comparáveis")
            actual_up, outcome_timestamp = _actual_outcome(
                {
                    name: official_documents[name]
                    for name in LOCAL_TOURNAMENT_MODELS
                },
                brt_offset_h=brt_offset_h,
            )
            audit["outcome_timestamps"][session_date] = outcome_timestamp
            sessions.append(
                LedgerSession(
                    session_date=session_date,
                    actual_up=actual_up,
                    forecasts=forecasts,
                )
            )
            revisions_by_session[session_date] = (revision_fingerprint, revision)
        except Exception as exc:
            audit["invalid_bundles"] += 1
            audit["invalid_reasons"].append(
                f"{manifest_path}: {type(exc).__name__}: {exc}"
            )
    revision_groups: dict[str, dict] = {}
    for session_date, (fingerprint, revision) in revisions_by_session.items():
        group = revision_groups.setdefault(
            fingerprint,
            {"revision": revision, "session_dates": []},
        )
        group["session_dates"].append(session_date)
    audit["engine_revision_groups"] = revision_groups
    if len(revision_groups) > 1:
        audit["mixed_engine_revision_bundles"] = len(sessions)
        audit["invalid_reasons"].append(
            "ledger contém múltiplas revisões do motor; não mistura sessões no torneio"
        )
        sessions = []
    audit["selected_sessions"] = len(sessions)
    return sessions, audit


def _probability_losses(probability: float, actual_up: bool) -> tuple[float, float]:
    actual = 1.0 if actual_up else 0.0
    clipped = min(1.0 - EPSILON, max(EPSILON, probability))
    brier = (probability - actual) ** 2
    log_loss = -(actual * math.log(clipped) + (1.0 - actual) * math.log(1.0 - clipped))
    return brier, log_loss


def _session_scores(session: LedgerSession, model: str) -> dict:
    probabilities = list(session.forecasts[model])
    losses = [_probability_losses(probability, session.actual_up) for probability in probabilities]
    mean_probability = statistics.fmean(probabilities)
    return {
        "brier": statistics.fmean(loss[0] for loss in losses),
        "log_loss": statistics.fmean(loss[1] for loss in losses),
        "accuracy": float((mean_probability >= 0.5) == session.actual_up),
        "mean_probability": mean_probability,
        "observations": len(probabilities),
    }


def _roc_auc(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float | None:
    positives = [p for p, outcome in zip(probabilities, outcomes) if outcome]
    negatives = [p for p, outcome in zip(probabilities, outcomes) if not outcome]
    if not positives or not negatives:
        return None
    wins = 0.0
    for positive in positives:
        for negative in negatives:
            wins += positive > negative
            wins += 0.5 * (positive == negative)
    return wins / (len(positives) * len(negatives))


def _calibration_error(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float:
    bins: dict[int, list[tuple[float, bool]]] = {}
    for probability, outcome in zip(probabilities, outcomes):
        bucket = min(9, int(probability * 10.0))
        bins.setdefault(bucket, []).append((probability, outcome))
    total = len(probabilities)
    return sum(
        len(values) / total
        * abs(
            statistics.fmean(value[0] for value in values)
            - statistics.fmean(float(value[1]) for value in values)
        )
        for values in bins.values()
    )


def _empirical_bernstein_log_capitals(deltas: Sequence[float], *, alpha: float) -> list[float]:
    """Traço sessão-a-sessão do log-capital do martingale-produto empirical-
    Bernstein por betting (inspirado em Waudby-Smith & Ramdas, "Estimating
    means of bounded random variables by betting", JRSS-B, publicado online
    em 2023), lambda preditivo plug-in. ``delta = brier[candidato] -
    brier[oponente]`` em ``[-1, 1]``; a transformação ``x = (delta+1)/2 in
    [0,1]`` reduz o teste de ``H0: E[delta_i | histórico até i-1] >= 0`` (o
    nulo CONDICIONAL, sessão a sessão -- não a média marginal/histórica) a
    apostar no lado "candidato melhora" (``x < 0.5``).

    Validade (Ville): para qualquer sequência de apostas preditivas (usa só
    ``x_1..x_{i-1}``), ``K_t = prod(1 + lambda_i*(0.5-x_i))`` é um
    supermartingale não-negativo sob H0, logo ``P(exists t: K_t >= 1/alpha |
    H0) <= alpha`` -- vale para TODO t desde t=1, sem exigir i.i.d. (só media
    condicional >= 0 sob o nulo). O lambda plug-in só afeta poder, nunca essa
    garantia -- por isso o traço completo (não só o valor final) é exposto:
    é o que permite consultar a decisão em QUALQUER sessão (peeking diário)
    sem invalidar a cobertura.

    A ordem de cada passo é a parte crítica de corretude (deixar de respeitar
    a "preditividade" -- calcular lambda_i a partir de x_i em vez de só do
    passado -- invalida a garantia de Ville silenciosamente):
      1. lambda_i a partir do estado ANTERIOR (mu_hat/sigma^2_hat de x_1..x_{i-1});
      2. atualiza log-capital com x_i;
      3. só então incorpora x_i nas estatísticas preditivas do próximo passo.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha precisa estar em (0, 1)")
    log_threshold = math.log(1.0 / alpha)
    sum_x = 0.0
    sum_sq = 0.0
    log_capital = 0.0
    trace: list[float] = []
    for index, delta in enumerate(deltas, start=1):
        if not -1.0 - EPSILON <= delta <= 1.0 + EPSILON:
            raise ValueError(f"delta fora de [-1,1] na sessão #{index}: {delta}")
        x = (delta + 1.0) / 2.0
        mu_pred = (0.5 + sum_x) / index
        var_pred = max((0.25 + sum_sq) / index, VARIANCE_FLOOR)
        lam = min(
            LAMBDA_MAX,
            math.sqrt(2.0 * log_threshold / (var_pred * index * math.log(index + 1))),
        )
        log_capital += math.log1p(lam * (0.5 - x))
        sum_sq += (x - mu_pred) ** 2
        sum_x += x
        trace.append(log_capital)
    return trace


def _empirical_bernstein_sequential_test(deltas: Sequence[float], *, alpha: float) -> dict:
    """Teste sequencial anytime-valid, unicaudal, de ``H0: E[delta_i |
    histórico até i-1] >= 0`` -- o nulo CONDICIONAL, sessão a sessão (ver
    aviso no docstring do módulo sobre a diferença para uma média
    marginal/histórica sob roster não congelado).

    Ver ``_empirical_bernstein_log_capitals`` para a construção e a garantia
    de validade. ``rejects_null`` é sempre baseado SÓ no valor ATUAL
    (``trace[-1]``) -- reativo, nunca "grudento". ``running_max_log_capital``/
    ``first_crossing_session`` são expostos apenas como DIAGNÓSTICO histórico
    (o e-process cruzou o limiar em algum ponto do passado, mesmo que a
    decisão atual tenha voltado a ``False``), NUNCA como autoridade de
    promoção -- mesmo papel que ``running_min_upper_confidence_bound``/
    ``first_rejection_session`` cumprem em ``_hrms_sequential_test``.
    """
    trace = _empirical_bernstein_log_capitals(deltas, alpha=alpha)
    log_capital = trace[-1] if trace else 0.0
    log_threshold = math.log(1.0 / alpha)
    first_crossing_session = None
    for index, value in enumerate(trace, start=1):
        if value >= log_threshold:
            first_crossing_session = index
            break
    return {
        "sessions": len(deltas),
        "delta_brier_mean": round(statistics.fmean(deltas), 8) if deltas else None,
        "alpha": alpha,
        "log_capital": round(log_capital, 6),
        "log_threshold": round(log_threshold, 6),
        "running_max_log_capital": round(max(trace), 6) if trace else None,
        "first_crossing_session": first_crossing_session,
        "rejects_null": log_capital >= log_threshold,
    }


def _poly_stitching_bound(v: float, *, alpha: float) -> float:
    """Boundary polynomial-stitching unicaudal (Howard et al. 2021, Thm 1):
    ``u(v)`` tal que, para qualquer processo sub-gamma centrado com "tempo
    intrínseco" preditível ``V_n``, ``P(exists n: S_n >= u(V_n)) <= alpha``.
    alpha entra DIRETO (não alpha/2): é um boundary de UM lado só (ver
    ``_hrms_bound_trace``), não a construção bilateral do paper.

    Fórmula confirmada contra a implementação de referência dos próprios
    autores (github.com/gostevehoward/confseq, classe C++
    ``PolyStitchingBound`` em ``uniform_boundaries.h``; e a documentação R
    ``poly_stitching_bound.Rd``, que nomeia ``c`` explicitamente como
    "sub-gamma scale parameter") -- é a forma APERTADA
    ``sqrt(k1^2*v*ell + termo^2) + termo``, não a soma simples
    ``k1*sqrt(v*ell) + termo`` (mais frouxa, não é a que os autores usam).
    """
    v_eff = max(v, HRMS_V_MIN)
    log_eta = math.log(HRMS_ETA)
    k1 = (HRMS_ETA ** 0.25 + HRMS_ETA ** -0.25) / math.sqrt(2.0)
    k2 = (math.sqrt(HRMS_ETA) + 1.0) / 2.0
    ell = HRMS_S * math.log(math.log(HRMS_ETA * v_eff / HRMS_V_MIN)) + math.log(
        HRMS_ZETA_S / (alpha * log_eta ** HRMS_S)
    )
    second_term = k2 * HRMS_C * ell
    return math.sqrt((k1 ** 2) * v_eff * ell + second_term ** 2) + second_term


def _hrms_bound_trace(deltas: Sequence[float], *, alpha: float) -> list[float]:
    """Traço sessão-a-sessão da cota de confiança superior (UCB) uniforme de
    HRMS (Howard, Ramdas, McAuliffe & Sekhon 2021) sobre
    ``mu_t = t^-1 * soma_{i=1}^{t} E[delta_i | F_{i-1}]`` -- a média das
    expectativas CONDICIONAIS ao longo do tempo (não a média marginal
    populacional de uma amostra i.i.d.; ver docstring do módulo para a
    ressalva completa) -- ao contrário de ``_empirical_bernstein_log_capitals``
    (WSR), que testa o nulo condicional de UMA sessão por vez, sem acumular.
    Ver docstring do módulo para a distinção completa entre
    ``sequential_winner`` e ``long_run_winner``.

    Garantia: ``P(exists n: mu_n > U_n) <= alpha`` (``mu_n`` já É a média das
    expectativas condicionais até ``n``, não uma média adicional sobre a
    série de ``mu_1..mu_n``), onde
    ``U_n = (S_n + u(V_n))/n``, ``S_n = soma(delta_1..delta_n)`` e ``V_n`` é o
    "tempo intrínseco" empirical-Bernstein -- soma CRUA (não diluída) dos
    resíduos ao quadrado, cada um centrado pela média preditiva do passo
    (``mu_hat_{i-1}``, só dados passados), mas o próprio quadrado usa
    ``delta_i`` (o dado atual): ``V_n = soma_{i=1}^{n} (delta_i -
    mu_hat_{i-1})^2``, com ``mu_hat_0 := 0`` (centro do domínio ``[-1,1]``).
    alpha entra DIRETO na fórmula (ver ``_poly_stitching_bound``): o boundary
    cobre um único lado do martingale sub-gamma ``M_n = soma(mu_i -
    delta_i)``, e ``{S_n <= -u(V_n)}`` implica ``{M_n >= u(V_n)}`` sob
    qualquer ponto do nulo ``H0: mu >= 0`` -- usar alpha/2 seria a construção
    bilateral, desperdiçada aqui (nunca testamos o lado oposto com esta
    estatística).

    ``V_n`` usar ``delta_i`` (o dado atual, não só o passado) NÃO é um bug:
    é a mesma convenção usada em DUAS construções empirical-Bernstein
    independentes da própria implementação de referência dos autores
    (``predmix_empbern_lower_cs`` e ``conjmix_empbern_lower_cs``, pacote
    ``confseq``, github.com/gostevehoward/confseq) -- o que precisa ser
    preditivo (só passado) é a MÉDIA usada para centrar o resíduo
    (``mu_hat_{i-1}``), não o resíduo em si; é essa centragem pelo passado
    que preserva o argumento de boundary-crossing, mesmo com ``V_n`` sendo
    mensurável em relação a ``delta_i``. Uma versão anterior desta função
    diluía ``v_hat_i`` por ``1/index`` e somava um prior a cada passo -- isso
    produzia crescimento espúrio ``~ln(n)`` em ``running_v`` mesmo sob
    variância verdadeira zero (ver comentário de ``HRMS_V_MIN`` acima);
    removido em favor da soma crua, que é a convenção confirmada.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha precisa estar em (0, 1)")
    sum_delta = 0.0
    running_v = 0.0
    trace: list[float] = []
    for index, delta in enumerate(deltas, start=1):
        if not -1.0 - EPSILON <= delta <= 1.0 + EPSILON:
            raise ValueError(f"delta fora de [-1,1] na sessão #{index}: {delta}")
        mu_pred = sum_delta / (index - 1) if index > 1 else 0.0
        running_v += (delta - mu_pred) ** 2
        bound = _poly_stitching_bound(running_v, alpha=alpha)
        sum_delta += delta
        trace.append((sum_delta + bound) / index)
    return trace


def _hrms_sequential_test(
    deltas: Sequence[float], *, alpha: float, min_sessions: int
) -> dict:
    """Teste sequencial anytime-valid, unicaudal, de
    ``H0: mu_t >= 0`` onde ``mu_t = t^-1 * soma E[delta_i | F_{i-1}]`` -- a
    média das expectativas condicionais ATÉ A SESSÃO ATUAL ``t`` (não
    literalmente a média marginal populacional; ver aviso no docstring do
    módulo sobre essa distinção e sobre a diferença para o nulo condicional
    sessão-a-sessão do WSR).

    ``rejects_null`` é REATIVO: usa só ``U_t`` (a UCB da sessão atual,
    ``trace[-1]``), nunca o mínimo histórico. A garantia de Ville,
    ``P(para todo n: mu_n <= U_n) >= 1-alpha``, cobre CADA ``mu_n`` pelo SEU
    PRÓPRIO ``U_n`` -- ela NÃO licencia interseccionar UCBs de instantes
    diferentes (``min_i U_i <= U_t`` para ``i<t``) como se fossem cotas do
    MESMO alvo: isso só seria válido se ``mu`` fosse constante no tempo (aí
    ``mu <= U_i`` para todo ``i`` implicaria ``mu <= min_i U_i``), e o
    docstring do módulo insiste, corretamente, que ``mu_t`` é uma sequência
    PREDITÍVEL não-estacionária. Usar o mínimo histórico controla o nulo
    GLOBAL "``para todo n: mu_n >= 0``" -- uma travessia (``running_min <=
    0``) é evidência para a alternativa EXISTENCIAL "``exists n: mu_n <
    0``" (algum prefixo, com ``n >= min_sessions``, já teve média acumulada
    negativa), não para o nulo operacional declarado acima ("``mu_t`` AGORA
    é negativo")
    -- que é a pergunta que o timer systemd reavalia a cada reagregação
    diária do ledger. Contraexemplo
    determinístico que expõe a diferença: 300 sessões de ``delta=-0.10``
    seguidas de 300 de ``delta=+0.10`` (média final exatamente 0) dá
    ``U_600=+0.071349`` (corretamente não-significativo), mas o mínimo
    histórico (fase 1) vale ``-0.011149`` -- um sinal de promoção espúrio
    baseado em evidência revertida (ver
    ``test_reversao_de_regime_com_media_final_zero_nao_deve_ser_promovida``).
    ``min_sessions`` aqui só GATEIA quando o teste passa a reportar
    (``len(trace) >= min_sessions``), não muda a estatística em si -- é o
    mesmo gate de produto usado como piso em ``evaluate_champions``.
    ``running_min_upper_confidence_bound``/``first_rejection_session``
    continuam expostos apenas como DIAGNÓSTICO histórico ("em algum ponto do
    passado a média acumulada até ali já foi comprovadamente negativa"), NUNCA
    como autoridade de promoção -- só ``upper_confidence_bound``/
    ``rejects_null`` (ambos sobre ``trace[-1]``) decidem o campo
    ``long_run_winner``.
    """
    trace = _hrms_bound_trace(deltas, alpha=alpha)
    running_min = None
    first_rejection_session = None
    if len(trace) >= min_sessions:
        considered = trace[min_sessions - 1:]
        running_min = min(considered)
        for offset, value in enumerate(considered):
            if value <= 0.0:
                first_rejection_session = min_sessions + offset
                break
    return {
        "sessions": len(deltas),
        "delta_brier_mean": round(statistics.fmean(deltas), 8) if deltas else None,
        "alpha": alpha,
        "upper_confidence_bound": round(trace[-1], 6) if trace else None,
        "running_min_upper_confidence_bound": (
            round(running_min, 6) if running_min is not None else None
        ),
        "first_rejection_session": first_rejection_session,
        "rejects_null": len(trace) >= min_sessions and trace[-1] <= 0.0,
    }


def _with_causal_climatology(sessions: Sequence[LedgerSession]) -> list[LedgerSession]:
    """Acrescenta baseline Beta(1,1) usando somente sessões anteriores."""
    up_count = 1
    total_count = 2
    augmented = []
    for session in sorted(sessions, key=lambda item: item.session_date):
        forecasts = dict(session.forecasts)
        forecasts["baseline_climatology"] = [up_count / total_count]
        augmented.append(
            LedgerSession(
                session_date=session.session_date,
                actual_up=session.actual_up,
                forecasts=forecasts,
            )
        )
        up_count += int(session.actual_up)
        total_count += 1
    return augmented


def evaluate_champions(
    sessions: Sequence[LedgerSession],
    *,
    min_sessions: int = DEFAULT_MIN_SESSIONS,
    alpha: float = DEFAULT_ALPHA,
) -> dict:
    if min_sessions <= 0:
        raise ValueError("min_sessions precisa ser positivo")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha precisa estar em (0, 1)")
    sessions = _with_causal_climatology(sessions)
    # Roster deve ficar CONGELADO (ver docstring do módulo): a garantia
    # anytime-valid abaixo assume que os mesmos K modelos disputam o torneio
    # do início ao fim. Hoje isso vale porque o loader só alimenta
    # TOURNAMENT_MODELS (miqueias/v1/v2) + a baseline climatológica causal --
    # nenhum challenger novo entra aqui sem alocar alpha entre "epochs", o
    # que não está implementado.
    common_models = sorted(
        set().union(*(set(session.forecasts) for session in sessions))
        if sessions else set()
    )
    comparable_sessions = [
        session
        for session in sessions
        if all(session.forecasts.get(model) for model in common_models)
    ]
    scores = {
        model: [_session_scores(session, model) for session in comparable_sessions]
        for model in common_models
    }
    outcomes = [session.actual_up for session in comparable_sessions]
    metrics = {}
    for model in common_models:
        model_scores = scores[model]
        session_probabilities = [score["mean_probability"] for score in model_scores]
        auc = _roc_auc(session_probabilities, outcomes)
        metrics[model] = {
            "sessions": len(model_scores),
            "observations": sum(score["observations"] for score in model_scores),
            "brier": round(statistics.fmean(score["brier"] for score in model_scores), 8)
            if model_scores else None,
            "log_loss": round(statistics.fmean(score["log_loss"] for score in model_scores), 8)
            if model_scores else None,
            "directional_accuracy_pct": round(
                100.0 * statistics.fmean(score["accuracy"] for score in model_scores), 6
            ) if model_scores else None,
            "session_mean_auc": round(auc, 8) if auc is not None else None,
            "session_mean_calibration_error": round(
                _calibration_error(session_probabilities, outcomes), 8
            ) if model_scores else None,
        }

    ranking = sorted(
        common_models,
        key=lambda model: metrics[model]["brier"] if metrics[model]["brier"] is not None else math.inf,
    )

    # Qual modelo vira "candidato" é escolhido a partir do ranking observado,
    # não pré-registrado -- isso é multiplicidade real (seleção do candidato),
    # não apenas "candidato fixo vs. vários oponentes". Reserva-se alpha/K
    # para CADA um dos K modelos como candidato-em-espera; cada candidato só
    # precisa vencer todos os seus oponentes no MESMO alpha_j reservado (teste
    # de interseção-união, Berger 1982 -- não se divide alpha_j de novo entre
    # oponentes: rejeitar cada H0 individual a alpha_j já controla a união).
    # A MESMA reserva alpha/K é usada, independentemente, pelos dois campos
    # (sequential_winner via WSR, long_run_winner via HRMS) -- ver docstring
    # do módulo sobre o limite combinado de <= 2*alpha entre os dois.
    candidate_alpha = alpha / len(common_models) if common_models else alpha

    def _iut_winner(test_fn):
        tests = []
        winning_candidates = []
        if len(common_models) >= 2 and len(comparable_sessions) >= min_sessions:
            for candidate in common_models:
                candidate_tests = []
                for opponent in common_models:
                    if opponent == candidate:
                        continue
                    deltas = [
                        scores[candidate][index]["brier"] - scores[opponent][index]["brier"]
                        for index in range(len(comparable_sessions))
                    ]
                    candidate_tests.append({
                        "candidate": candidate,
                        "opponent": opponent,
                        **test_fn(deltas),
                    })
                tests.extend(candidate_tests)
                if candidate_tests and all(test["rejects_null"] for test in candidate_tests):
                    winning_candidates.append(candidate)
        return tests, winning_candidates

    sequential_tests, sequential_winning_candidates = _iut_winner(
        lambda deltas: _empirical_bernstein_sequential_test(deltas, alpha=candidate_alpha)
    )
    long_run_tests, long_run_winning_candidates = _iut_winner(
        lambda deltas: _hrms_sequential_test(
            deltas, alpha=candidate_alpha, min_sessions=min_sessions
        )
    )

    sequential_winner = None
    long_run_winner = None
    status = "INCONCLUSIVE"
    reasons = []
    if len(common_models) < 2:
        reasons.append("menos de dois modelos comuns")
    if len(comparable_sessions) < min_sessions:
        reasons.append(
            f"amostra abaixo do gate: {len(comparable_sessions)}/{min_sessions} sessões"
        )
    if sequential_winning_candidates:
        sequential_winner = next(
            model for model in ranking if model in sequential_winning_candidates
        )
    if long_run_winning_candidates:
        long_run_winner = next(
            model for model in ranking if model in long_run_winning_candidates
        )
    if len(comparable_sessions) >= min_sessions and len(common_models) >= 2:
        status = "EVALUATED"
        if sequential_winner is None:
            reasons.append(
                "WSR (sequential_winner) não rejeita H0 condicional: nenhum modelo "
                "supera todos os concorrentes sessão a sessão com significância "
                "anytime-valid"
            )
        if long_run_winner is None:
            reasons.append(
                "HRMS (long_run_winner) não rejeita H0 (mu_t, a média das "
                "expectativas condicionais acumulada até a sessão atual): "
                "nenhum modelo supera todos os concorrentes na média acumulada "
                "de longo prazo com significância anytime-valid"
            )

    return {
        "status": status,
        "sequential_winner": sequential_winner,
        "long_run_winner": long_run_winner,
        "common_sessions": len(comparable_sessions),
        "minimum_sessions_gate": min_sessions,
        "common_models": common_models,
        "ranking_by_brier": ranking,
        "metrics": metrics,
        "alpha": alpha,
        "candidate_alpha": candidate_alpha,
        "combined_family_wise_alpha_bound": round(2.0 * alpha, 6),
        "sequential_tests": sequential_tests,
        "long_run_tests": long_run_tests,
        "reasons": reasons,
        "objective": "nowcast da direção final da sessão WIN (close > open)",
        "tactical_gate": {
            "status": "NOT_EVALUATED",
            "reason": (
                "utilidade como filtro da regra GEX/MID/Pair/NWE exige regra de execução, "
                "fill, alvo, stop e custos separados"
            ),
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-dir", default="data/p_dynamic_parity")
    parser.add_argument("--min-sessions", type=int, default=DEFAULT_MIN_SESSIONS)
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    parser.add_argument("--output-json", default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    sessions, audit = load_ledger_sessions(args.ledger_dir)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "methodology_version": METHODOLOGY_VERSION,
        "ledger_dir": str(args.ledger_dir),
        "audit": audit,
        **evaluate_champions(
            sessions,
            min_sessions=args.min_sessions,
            alpha=args.alpha,
        ),
    }
    if args.output_json:
        _write_json(Path(args.output_json), report)
    print(
        f"Champion-challenger: {report['status']} — "
        f"sessões={report['common_sessions']}/{report['minimum_sessions_gate']}, "
        f"sequential_winner={report['sequential_winner']} (tático, WSR), "
        f"long_run_winner={report['long_run_winner']} (estratégico, HRMS)"
    )
    for model in report["ranking_by_brier"]:
        metrics = report["metrics"][model]
        print(
            f"{model}: Brier={metrics['brier']}, log-loss={metrics['log_loss']}, "
            f"AUC={metrics['session_mean_auc']}"
        )
    for reason in report["reasons"]:
        print(f"- {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
