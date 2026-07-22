"""Regressões numéricas do gate sequencial empirical-Bernstein (betting/e-process,
inspirado em Waudby-Smith & Ramdas, JRSS-B, publicado online em 2023) usado
por ``evaluate_p_dynamic_champions``.

``tests/test_p_dynamic_champion_evaluator.py`` já cobre a integração (gate de
60 sessões, split de alpha entre candidatos, teste de interseção-união). Este
módulo valida a MATEMÁTICA da construção em si, isolada de qualquer wiring do
torneio -- chama ``_empirical_bernstein_log_capitals`` diretamente, não
``evaluate_champions`` (não cobre, portanto, derivação do roster nem o split
alpha/K entre candidatos-em-espera):

1. Diferencial contra uma reimplementação independente (recomputada do zero a
   cada passo, sem estado incremental compartilhado com a produção) -- pega
   bug de ordem/off-by-one que uma chamada direta à mesma função não pegaria.
2. Identidade exata do martingale (E[K_T] = 1 sob o nulo na fronteira, via
   enumeração exaustiva -- não Monte Carlo, então sem ruído estatístico).
3. Simulação de Type-I sob "peeking" diário (a garantia de Ville consultada em
   TODA sessão, não só na última), sob cenários i.i.d. -- valida o e-process
   isolado sob peeking repetido, não o pipeline completo do torneio. Com
   N=5000 réplicas e seed fixa, é uma checagem empírica de bom senso (as
   taxas observadas ficam bem abaixo de alpha), não um teste estatístico
   calibrado sobre a taxa de falso-positivo: o erro-padrão perto da fronteira
   de 5% com N=5000 é ~0,31pp, então uma asserção nua ``rate <= 0.05`` não
   teria poder de detectar um desvio pequeno -- as margens aqui são folgadas
   o bastante para não depender dessa precisão.
4. Contraexemplo verificado (achado do painel de design tri-lente sobre
   WSR-vs-HRMS): o gate controla o nulo CONDICIONAL sessão a sessão, não a
   média marginal/acumulada -- roster congelado evita multiplicidade de
   seleção, mas NÃO garante essa coincidência sozinho. Documentado como
   comportamento esperado (não bug) no docstring do módulo principal.
5. Diagnóstico histórico (``running_max_log_capital``/``first_crossing_
   session``) é sempre não-decisório: prova que os dois campos continuam
   registrando um cruzamento passado do limiar mesmo depois de
   ``rejects_null`` (baseado só em ``trace[-1]``) já ter revertido para
   ``False`` -- espelha o teste de reversão análogo no módulo HRMS irmão.
"""

from __future__ import annotations

import itertools
import math
import os
import random
import statistics
import sys

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.evaluate_p_dynamic_champions import (  # noqa: E402
    LAMBDA_MAX,
    VARIANCE_FLOOR,
    _empirical_bernstein_log_capitals,
    _empirical_bernstein_sequential_test,
)


def _reference_log_capital(deltas, alpha):
    """Reimplementação independente: recomputa mu_hat/sigma^2_hat do zero a
    cada passo via slicing/soma, em vez do acumulador incremental da produção.
    Um bug de índice (ex.: lambda_i vazando x_i) muda o resultado aqui mesmo
    que a produção "pareça" certa por inspeção visual."""
    log_threshold = math.log(1.0 / alpha)
    xs = [(delta + 1.0) / 2.0 for delta in deltas]
    log_capital = 0.0
    for i in range(1, len(xs) + 1):
        prior = xs[: i - 1]
        mu_pred = (0.5 + sum(prior)) / i
        sq_errors = []
        for j in range(1, i):
            earlier = xs[: j - 1]
            mu_hat_j = (0.5 + sum(earlier)) / j
            sq_errors.append((xs[j - 1] - mu_hat_j) ** 2)
        var_pred = max((0.25 + sum(sq_errors)) / i, VARIANCE_FLOOR)
        lam = min(
            LAMBDA_MAX,
            math.sqrt(2.0 * log_threshold / (var_pred * i * math.log(i + 1))),
        )
        log_capital += math.log1p(lam * (0.5 - xs[i - 1]))
    return log_capital


def test_log_capital_bate_com_reimplementacao_independente():
    """Golden/diferencial numa série curta e de baixa variância -- o regime
    real do torneio (~1000x menor que o pior caso). Lambda satura em
    LAMBDA_MAX nos 5 passos, exercitando especificamente o ramo de teto."""
    deltas = [-0.2, 0.1, -0.4, 0.0, 0.2]
    alpha = 0.05

    trace = _empirical_bernstein_log_capitals(deltas, alpha=alpha)
    reference = _reference_log_capital(deltas, alpha=alpha)

    assert trace[-1] == pytest.approx(reference, abs=1e-9)
    # Valor âncora cruzado pelas duas implementações independentes.
    assert trace[-1] == pytest.approx(0.16164573587516287, abs=1e-9)
    # delta=0.0 no passo 4 aposta exatamente no ponto de indiferença: o
    # log-capital não pode se mover nesse passo, qualquer que seja lambda.
    assert trace[3] == pytest.approx(trace[2], abs=1e-12)


def test_log_capital_bate_com_reimplementacao_em_serie_mais_longa_e_ruidosa():
    """Mesma reimplementação, série longa o bastante para lambda deixar de
    saturar em LAMBDA_MAX em algum ponto -- cobre o ramo da fórmula fechada,
    não só o do teto."""
    rng = random.Random(2026)
    deltas = [rng.uniform(-0.6, 0.6) for _ in range(40)]
    alpha = 0.05

    trace = _empirical_bernstein_log_capitals(deltas, alpha=alpha)
    reference = _reference_log_capital(deltas, alpha=alpha)

    assert trace[-1] == pytest.approx(reference, abs=1e-9)


def test_lambda_nao_esta_sempre_saturado_no_cenario_acima():
    """Sanity check do teste anterior: se lambda sempre batesse no teto, a
    série longa não exerceria nada de novo frente à série curta."""
    rng = random.Random(2026)
    deltas = [rng.uniform(-0.6, 0.6) for _ in range(40)]
    alpha = 0.05
    log_threshold = math.log(1.0 / alpha)

    sum_x = sum_sq = 0.0
    unsaturated_found = False
    for index, delta in enumerate(deltas, start=1):
        x = (delta + 1.0) / 2.0
        mu_pred = (0.5 + sum_x) / index
        var_pred = max((0.25 + sum_sq) / index, VARIANCE_FLOOR)
        raw_lambda = math.sqrt(
            2.0 * log_threshold / (var_pred * index * math.log(index + 1))
        )
        if raw_lambda < LAMBDA_MAX:
            unsaturated_found = True
        sum_sq += (x - mu_pred) ** 2
        sum_x += x

    assert unsaturated_found


def _mean_capital_over_all_paths(value_probabilities, horizon, alpha):
    """E[K_T] exato via enumeração exaustiva de TODOS os caminhos possíveis
    (não Monte Carlo -- sem ruído estatístico). ``value_probabilities`` é uma
    lista de (delta, probabilidade) i.i.d. por sessão, com média EXATA 0 (a
    fronteira do nulo). lambda_i é F_{i-1}-mensurável (só usa x_1..x_{i-1}),
    então Ville garante E[K_T]=1 exatamente, qualquer que seja a forma da
    distribuição -- não só para Bernoulli simétrico."""
    total = 0.0
    weight_total = 0.0
    for combo in itertools.product(value_probabilities, repeat=horizon):
        deltas = [delta for delta, _ in combo]
        weight = 1.0
        for _, probability in combo:
            weight *= probability
        trace = _empirical_bernstein_log_capitals(deltas, alpha=alpha)
        total += weight * math.exp(trace[-1])
        weight_total += weight
    assert weight_total == pytest.approx(1.0, abs=1e-9)
    return total


def test_martingale_e_k_t_igual_a_um_sob_nulo_bernoulli_simetrico():
    """Identidade exata de Ville na fronteira do nulo (media(delta)=0), com
    deltas nos extremos de [-1,1] (Bernoulli 0,5) -- o caso mais estressante
    para a positividade do produto (x em {0,1})."""
    mean_capital = _mean_capital_over_all_paths(
        [(-1.0, 0.5), (1.0, 0.5)], horizon=8, alpha=0.05
    )
    assert mean_capital == pytest.approx(1.0, abs=1e-9)


def test_martingale_e_k_t_igual_a_um_sob_nulo_assimetrico():
    """Mesma identidade sob uma distribuição ASSIMÉTRICA (90% choque pequeno
    negativo, 10% choque grande positivo, recentrada para média exata 0) --
    a família que codex propôs para estressar robustez a não-normalidade."""
    mean_capital = _mean_capital_over_all_paths(
        [(-0.1, 0.9), (0.9, 0.1)], horizon=6, alpha=0.05
    )
    assert mean_capital == pytest.approx(1.0, abs=1e-9)


def _type_i_false_positive_rate(dist_fn, *, n_replicas, horizon, alpha, min_sessions, seed):
    """Taxa empírica de "WINNER" sob o nulo, CONSULTANDO a decisão em toda
    sessão >= min_sessions (peeking diário real do agendador systemd) -- não
    só na última. Testar um só t não pegaria uma inflação de Type-I que só
    aparece ao espiar repetidamente; isto cobre o e-process isolado sob
    cenários i.i.d., não o pipeline completo do torneio (ver docstring do
    módulo)."""
    rng = random.Random(seed)
    log_threshold = math.log(1.0 / alpha)
    hits = 0
    for _ in range(n_replicas):
        deltas = [dist_fn(rng) for _ in range(horizon)]
        trace = _empirical_bernstein_log_capitals(deltas, alpha=alpha)
        if any(value >= log_threshold for value in trace[min_sessions - 1 :]):
            hits += 1
    return hits / n_replicas


def test_type_i_sob_peeking_diario_nao_excede_alpha_regime_baixa_variancia():
    """Regime de variância baixa, o real do torneio: ruído simétrico pequeno
    (uniform(-0.05,0.05)), média exata 0. Horizonte de 300 sessões, floor de
    60 -- espelha o ledger de produção (~230 sessões hoje)."""

    def low_variance_symmetric(rng):
        return rng.uniform(-0.05, 0.05)

    rate = _type_i_false_positive_rate(
        low_variance_symmetric,
        n_replicas=5000,
        horizon=300,
        alpha=0.05,
        min_sessions=60,
        seed=1234,
    )
    assert rate <= 0.05


def test_type_i_sob_peeking_diario_nao_excede_alpha_regime_assimetrico():
    """Ruído assimétrico (90% choque pequeno negativo, 10% choque grande
    positivo), recentrado para média exata 0 -- família sugerida por codex
    para estressar robustez a não-normalidade sob peeking repetido."""

    def skewed(rng):
        return -0.02 if rng.random() < 0.9 else 0.18

    rate = _type_i_false_positive_rate(
        skewed,
        n_replicas=5000,
        horizon=300,
        alpha=0.05,
        min_sessions=60,
        seed=1234,
    )
    assert rate <= 0.05


def test_type_i_sob_peeking_diario_nao_excede_alpha_regime_variancia_alta_e_nao_e_degenerado():
    """Variância bem mais alta que o regime real (uniform(-0.3,0.3)) -- ainda
    sob o nulo EXATO (média 0, não uma alternativa -- isto não é um teste de
    poder estatístico, que só se mede sob H1). Além de não estourar alpha,
    exige taxa > 0: prova que esta checagem não é vazia por construção -- uma
    taxa sempre-zero não distinguiria "gate correto" de "gate conservador
    demais para rejeitar qualquer coisa, inclusive sob variância alta"."""

    def high_variance_symmetric(rng):
        return rng.uniform(-0.3, 0.3)

    rate = _type_i_false_positive_rate(
        high_variance_symmetric,
        n_replicas=5000,
        horizon=300,
        alpha=0.05,
        min_sessions=60,
        seed=1234,
    )
    assert 0.0 < rate <= 0.05


def test_regime_alternante_pode_cruzar_o_limiar_com_media_acumulada_exatamente_zero():
    """Contraexemplo verificado (achado do painel de design tri-lente, codex):
    o gate controla o nulo CONDICIONAL sessão a sessão, não a média
    marginal/acumulada -- as duas coincidem sob (quase-)estacionariedade, mas
    NÃO por roster congelado sozinho. Esta sequência tem média exata 0 (180
    sessões de -0.10, 30 alternando -0.80/+0.80, 20 de +0.90) e ainda assim
    cruza o limiar em t=61, logo após o gate de 60 sessões abrir.

    Isto NÃO é um bug: é o comportamento esperado e documentado no docstring
    do módulo -- "vencedor" aqui significa "nenhum trecho da evidência causal
    pareceu convincentemente ruim", não "a média de longo prazo é positiva".
    Este teste existe para que um refactor futuro que mude esse comportamento
    o faça por decisão explícita, não por acidente."""
    deltas = [-0.10] * 180
    for _ in range(15):
        deltas.append(-0.80)
        deltas.append(0.80)
    deltas += [0.90] * 20
    assert len(deltas) == 230
    assert statistics.fmean(deltas) == pytest.approx(0.0, abs=1e-9)

    alpha = 0.05 / 4  # candidate_alpha com K=4 (miqueias/v1/v2/climatologia)
    trace = _empirical_bernstein_log_capitals(deltas, alpha=alpha)
    log_threshold = math.log(1.0 / alpha)

    assert trace[-1] == pytest.approx(5.829487868301871, abs=1e-9)
    assert trace[-1] >= log_threshold
    first_cross = next(
        index + 1 for index, value in enumerate(trace) if index + 1 >= 60 and value >= log_threshold
    )
    assert first_cross == 61


def test_running_max_e_first_crossing_sao_diagnostico_historico_nao_sticky():
    """Espelha ``test_reversao_de_regime_com_media_final_zero_nao_deve_ser_
    promovida`` do módulo HRMS irmão, para o lado WSR: ``running_max_log_
    capital``/``first_crossing_session`` devem registrar que o e-process
    cruzou o limiar no passado, MESMO quando ``rejects_null`` (baseado só em
    ``trace[-1]``) já reverteu para ``False`` -- provando que os dois novos
    campos são diagnóstico histórico puro, nunca autoridade de decisão.

    10 sessões extremamente favoráveis (``delta=-1.0``, o candidato "vence"
    ao máximo) cruzam o limiar em t=6 e sobem até um pico em t=10; 3 sessões
    extremamente desfavoráveis (``delta=+1.0``) em seguida derrubam o log-
    capital de volta abaixo do limiar."""
    alpha = 0.05
    deltas = [-1.0] * 10 + [1.0] * 3

    result = _empirical_bernstein_sequential_test(deltas, alpha=alpha)

    assert result["sessions"] == 13
    assert result["log_capital"] == pytest.approx(2.574071, abs=1e-6)
    assert result["log_threshold"] == pytest.approx(2.995732273553991, abs=1e-6)
    assert result["rejects_null"] is False
    # Diagnóstico histórico: cruzou e chegou a um pico bem acima do limiar,
    # mesmo a decisão ATUAL (13ª sessão) já tendo revertido para False.
    assert result["running_max_log_capital"] == pytest.approx(5.596157879354227, abs=1e-6)
    assert result["first_crossing_session"] == 6
