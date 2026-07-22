"""Regressões numéricas do gate sequencial HRMS (boundary polynomial-stitching,
Howard, Ramdas, McAuliffe & Sekhon, "Time-uniform, nonparametric, nonasymptotic
confidence sequences", Annals of Statistics 49(2), 2021) usado por
``evaluate_p_dynamic_champions``.

Espelha ``tests/test_empirical_bernstein_sequential_test.py`` (o mesmo módulo
para o gate WSR/``sequential_winner``), adaptado para as diferenças reais
entre as duas construções -- ver docstring de ``_hrms_bound_trace`` e do
módulo principal para a distinção completa entre ``sequential_winner``
(WSR, nulo CONDICIONAL) e ``long_run_winner`` (HRMS, nulo do valor médio de
longo prazo). Este módulo valida a MATEMÁTICA da construção em si, isolada de
qualquer wiring do torneio -- chama ``_hrms_bound_trace``/
``_hrms_sequential_test`` diretamente, não ``evaluate_champions``:

1. Diferencial contra uma reimplementação independente (recomputa
   ``running_v`` do zero, via duplo loop, sem nenhum estado incremental
   compartilhado com a produção) -- pega bug de ordem/off-by-one que uma
   chamada direta à mesma função não pegaria.
2. Cota exata de rejeição via enumeração exaustiva (não Monte Carlo, sem
   ruído estatístico). AO CONTRÁRIO do martingale WSR (identidade EXATA
   E[K_T]=1), a garantia HRMS é uma DESIGUALDADE (``P(reject) <= alpha``) --
   não há identidade a verificar, só o limite superior. Com o valor correto
   de ``HRMS_C`` (c=2, ver docstring do módulo principal), a enumeração
   exaustiva já não é o veículo prático para provar não-degenerado: mesmo o
   caminho mais adversarial possível (Bernoulli(+-1) simétrico) só cruza o
   boundary em n=32, e 2**32 caminhos é inviável para enumerar. Por isso a
   prova de não-degenerado migrou para o item 4 (caminho determinístico
   extremo, fechado, sem enumeração) -- os testes de enumeração exaustiva
   aqui só verificam a desigualdade ``<= alpha`` (trivialmente satisfeita
   por taxa zero em horizontes pequenos), não mais uma cota positiva.
3. Simulação de Type-I sob "peeking" diário, mesmos três regimes de variância
   do espelho WSR. IMPORTANTE: ao contrário do WSR (cuja taxa Monte Carlo no
   regime de alta variância é positiva, ver o teste
   ``test_type_i_sob_peeking_diario_nao_excede_alpha_regime_variancia_alta_e_nao_e_degenerado``
   no módulo irmão), a taxa aqui é EXATAMENTE zero nos três regimes testados
   a horizonte=300/alpha=0,05 -- isto não é um sinal de bug, é o próprio
   motivo documentado de LAMBDA_MAX/HRMS_V_MIN: NESTES regimes/horizonte
   específicos, a construção CS aditiva (boundary sobre a soma acumulada)
   acaba menos potente que o martingale multiplicativo/betting do WSR (ver
   também o teste de diferenciador abaixo, onde o HRMS precisa de ~4x mais
   sessões que o WSR para o mesmo efeito). Isto NÃO é uma alegação de que o
   HRMS é sempre/estruturalmente menos potente que o WSR em qualquer regime
   -- é uma observação empírica destes fixtures específicos, consistente com
   a decisão de produto de tratar ``sequential_winner`` (WSR) como evidência
   tática e ``long_run_winner`` (HRMS) como a autoridade de promoção. A prova
   de que o gate NÃO é "impossível de rejeitar por construção" vem, em vez
   disso, do teste determinístico do item 4 (caminho extremo fechado) e do
   teste de viés sustentado logo em seguida.
4. Prova de não-degenerado por caminho determinístico extremo (fechado, sem
   enumeração nem Monte Carlo): sob o nulo Bernoulli(+-1) simétrico, a
   realização ``delta=-1.0`` constante tem probabilidade exata ``(1/2)^n``
   e -- por ser a mais adversarial possível dentro do domínio ``[-1,1]`` --
   cruza o boundary em n=32 (ver ``_poly_stitching_bound(1.0, alpha=0.05)``).
   Logo ``P(reject) >= (1/2)^32 > 0`` sob o nulo exato, o que basta para
   provar que o gate não é vazio por construção, sem precisar somar sobre
   os ``2**32`` caminhos possíveis.
5. Diferenciador correto (pós-correção do bug de acumulação de ``running_v``
   E do bug de escala ``HRMS_C``, ver histórico no módulo principal): um
   viés condicional pequeno e sustentado (``delta=-0.02`` constante, na
   mesma ordem de grandeza da variância típica documentada no módulo
   principal) faz o WSR rejeitar em n=300 sessões enquanto o HRMS ainda não
   rejeita -- exatamente o tradeoff WSR-vs-HRMS já documentado em
   ``LAMBDA_MAX`` (WSR é mais potente que HRMS no regime de variância
   baixa). O HRMS SÓ precisa de mais sessões para o MESMO efeito (n=1333,
   não "nunca") -- não é uma diferença de pergunta estatística respondida,
   é uma diferença de PODER, o que é consistente com os dois campos serem
   evidência complementar, não substitutos.
6. Prova de que ``rejects_null`` é REATIVO (pós-correção do bug de semântica
   "pegajosa" do mínimo histórico -- ver histórico no docstring de
   ``_hrms_sequential_test`` no módulo principal): uma reversão de regime
   (300 sessões de ``delta=-0.10`` seguidas de 300 de ``delta=+0.10``, média
   final exatamente zero) NÃO deve ser promovida, mesmo tendo cruzado o
   limiar em algum trecho do passado -- porque ``mu_t`` (a média acumulada
   ATUAL) já reverteu para zero. Antes da correção, o mínimo histórico
   (herdado da fase 1, quando o viés negativo ainda estava ativo) travava
   ``rejects_null=True`` para sempre, mesmo após a reversão.
7. Como o item 3 dá taxa amostral zero (em 5000 réplicas, seed fixa) nos três
   regimes/horizonte de produção -- o que confirma "não rejeitou demais
   NESTAS simulações" mas NÃO é prova de calibração Type-I no regime
   produtivo, nem tem poder para distinguir uma calibração correta de uma
   arbitrariamente conservadora (um ``HRMS_C`` errado também daria zero
   amostral ali) -- um teste à parte busca deliberadamente um regime
   ARTIFICIAL onde a simulação Monte Carlo produz uma taxa observável e
   não-zero sob o nulo exato, só para ter poder de detectar um erro de
   escala no boundary (golden test de regressão, não evidência de cobertura
   em produção). Usa ``alpha=0.99``/``horizon=5000`` (fora do regime de
   produção, só por tratabilidade computacional) -- ver
   ``test_type_i_monte_carlo_em_regime_artificial_detecta_erro_de_escala``.
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
    _empirical_bernstein_sequential_test,
    _hrms_bound_trace,
    _hrms_sequential_test,
    _poly_stitching_bound,
)


def _reference_hrms_bound_trace(deltas, alpha):
    """Reimplementação independente: recomputa ``running_v`` do zero a cada
    passo via duplo loop/slicing, em vez do acumulador incremental da
    produção. Reusa ``_poly_stitching_bound`` (fórmula fechada já verificada
    contra a implementação C++ de referência dos autores -- não é o alvo
    deste teste) mas reimplementa inteiramente a ordem/acumulação de
    ``running_v`` e ``S_n``, que é onde o bug de crescimento espúrio
    ``~ln(n)`` vivia antes da correção."""
    n = len(deltas)
    trace = []
    for i in range(1, n + 1):
        running_v = 0.0
        for j in range(1, i + 1):
            earlier = deltas[: j - 1]
            mu_hat_j = sum(earlier) / (j - 1) if j > 1 else 0.0
            running_v += (deltas[j - 1] - mu_hat_j) ** 2
        bound = _poly_stitching_bound(running_v, alpha=alpha)
        s_i = sum(deltas[:i])
        trace.append((s_i + bound) / i)
    return trace


def test_trace_bate_com_reimplementacao_independente():
    """Golden/diferencial numa série curta -- mesma série usada no espelho
    WSR (``test_log_capital_bate_com_reimplementacao_independente``), para
    que os dois testes fiquem lado a lado comparáveis."""
    deltas = [-0.2, 0.1, -0.4, 0.0, 0.2]
    alpha = 0.05

    trace = _hrms_bound_trace(deltas, alpha=alpha)
    reference = _reference_hrms_bound_trace(deltas, alpha=alpha)

    assert trace == pytest.approx(reference, abs=1e-9)
    # Valor âncora cruzado pelas duas implementações independentes.
    assert trace[-1] == pytest.approx(5.723387877474716, abs=1e-9)


def test_trace_bate_com_reimplementacao_em_serie_mais_longa_e_ruidosa():
    """Mesma reimplementação, série longa (mesma série/seed do espelho WSR)
    para exercitar mais passos de acumulação de ``running_v``."""
    rng = random.Random(2026)
    deltas = [rng.uniform(-0.6, 0.6) for _ in range(40)]
    alpha = 0.05

    trace = _hrms_bound_trace(deltas, alpha=alpha)
    reference = _reference_hrms_bound_trace(deltas, alpha=alpha)

    assert trace == pytest.approx(reference, abs=1e-9)


def _exact_rejection_probability(value_probabilities, horizon, alpha, min_sessions):
    """``P(exists n em [min_sessions, horizon]: U_n <= 0)`` exato, via
    enumeração exaustiva de TODOS os caminhos possíveis (não Monte Carlo --
    sem ruído estatístico). Ao contrário do martingale WSR (identidade
    E[K_T]=1), a garantia HRMS é a DESIGUALDADE ``P(...) <= alpha`` -- não há
    um valor exato "esperado" a verificar, só o limite superior."""
    total = 0.0
    weight_total = 0.0
    for combo in itertools.product(value_probabilities, repeat=horizon):
        deltas = [delta for delta, _ in combo]
        weight = 1.0
        for _, probability in combo:
            weight *= probability
        weight_total += weight
        if weight == 0.0:
            continue
        trace = _hrms_bound_trace(deltas, alpha=alpha)
        considered = trace[min_sessions - 1 :]
        if any(value <= 0.0 for value in considered):
            total += weight
    assert weight_total == pytest.approx(1.0, abs=1e-9)
    return total


def test_prob_rejeicao_exata_nao_excede_alpha_sob_nulo_bernoulli_simetrico():
    """Nulo na fronteira (média(delta)=0), deltas nos extremos de [-1,1]
    (Bernoulli 0,5) -- o caso mais estressante para a positividade do
    boundary. ``min_sessions=1`` (não 60) para manter a enumeração exaustiva
    tratável (2^12 caminhos). Com ``HRMS_C=2`` (valor correto, ver módulo
    principal) a cota exata neste horizonte pequeno é zero -- o caminho mais
    adversarial possível (``delta=-1.0`` constante) só cruza o boundary em
    n=32 (ver ``test_caminho_extremo_deterministico_prova_nao_degenerado_do_boundary``),
    bem além de horizon=12. Isto é consistente com a desigualdade
    (``P(reject) <= alpha``), não uma identidade que precise ser positiva
    aqui -- a prova de não-degenerado do gate vem do teste determinístico
    logo abaixo, não desta enumeração."""
    rate = _exact_rejection_probability(
        [(-1.0, 0.5), (1.0, 0.5)], horizon=12, alpha=0.05, min_sessions=1
    )
    assert rate == pytest.approx(0.0, abs=1e-12)
    assert rate <= 0.05


def test_caminho_extremo_deterministico_prova_nao_degenerado_do_boundary():
    """Prova de não-degenerado que substitui a enumeração exaustiva (inviável
    computacionalmente com o ``HRMS_C`` correto -- ver módulo principal e
    item 2/4 da docstring do módulo): sob o nulo Bernoulli(+-1) simétrico, a
    realização ``delta=-1.0`` constante é UM caminho possível dentre vários
    igualmente extremos em magnitude (qualquer sequência Bernoulli(+-1) tem
    |delta|=1 em todo passo) -- o que torna ESTE caminho especial não é
    magnitude, é ser o único onde a média amostral acumulada rastreia
    exatamente o próprio delta a cada passo, fazendo ``running_v`` ficar
    constante em 1.0 a partir de i=2 (em vez de crescer, como aconteceria em
    qualquer caminho não-constante) -- isso permite a fórmula fechada abaixo
    sem precisar comparar poder contra os demais caminhos possíveis. A
    realização tem probabilidade exata ``(1/2)^n`` sob o nulo Bernoulli(+-1)
    simétrico. Isto dá uma fórmula fechada: trace(i) = (-i + B)/i onde
    B=_poly_stitching_bound(1.0, alpha) é constante, cruzando zero em
    i=ceil(B). Confirma-se abaixo que i=32 é o primeiro inteiro que cruza
    (i=31 ainda positivo) -- logo ``P(reject) >= (1/2)^32 ~= 2.3e-10 > 0``
    sob o nulo exato, o que já basta para provar que o gate não é vazio por
    construção, sem somar sobre os ``2**32`` caminhos possíveis."""
    alpha = 0.05
    bound_at_v1 = _poly_stitching_bound(1.0, alpha=alpha)
    assert bound_at_v1 == pytest.approx(31.31587057497297, abs=1e-9)

    trace = _hrms_bound_trace([-1.0] * 32, alpha=alpha)
    assert trace[30] > 0.0  # n=31: ainda não cruzou
    assert trace[31] <= 0.0  # n=32: cruza
    assert trace[31] == pytest.approx(-0.021379044532094715, abs=1e-9)


def test_prob_rejeicao_exata_nao_excede_alpha_sob_nulo_assimetrico():
    """Nulo na fronteira sob distribuição ASSIMÉTRICA (90% choque pequeno
    negativo, 10% choque grande positivo, recentrada para média exata 0) --
    mesma família de ``test_martingale_e_k_t_igual_a_um_sob_nulo_assimetrico``
    no espelho WSR. Aqui a cota exata é zero neste horizonte pequeno (14
    sessões) -- consistente com a desigualdade (``<= alpha``), não uma
    identidade que precise ser positiva."""
    rate = _exact_rejection_probability(
        [(-0.02, 0.9), (0.18, 0.1)], horizon=14, alpha=0.05, min_sessions=1
    )
    assert rate == pytest.approx(0.0, abs=1e-12)
    assert rate <= 0.05


def _type_i_false_positive_rate(dist_fn, *, n_replicas, horizon, alpha, min_sessions, seed):
    """Taxa empírica de rejeição sob o nulo, CONSULTANDO a decisão em toda
    sessão >= min_sessions (peeking diário real do agendador systemd) -- não
    só na última. Mesma estrutura de
    ``_type_i_false_positive_rate`` no espelho WSR."""
    rng = random.Random(seed)
    hits = 0
    for _ in range(n_replicas):
        deltas = [dist_fn(rng) for _ in range(horizon)]
        trace = _hrms_bound_trace(deltas, alpha=alpha)
        if any(value <= 0.0 for value in trace[min_sessions - 1 :]):
            hits += 1
    return hits / n_replicas


def test_type_i_sob_peeking_diario_nao_excede_alpha_regime_baixa_variancia():
    """Regime de variância baixa, o real do torneio: ruído simétrico pequeno
    (uniform(-0.05,0.05)), média exata 0. Mesmos parâmetros do espelho WSR
    (horizonte=300, floor=60, N=5000, seed=1234) para comparação direta."""

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
    assert rate == 0.0
    assert rate <= 0.05


def test_type_i_sob_peeking_diario_nao_excede_alpha_regime_assimetrico():
    """Ruído assimétrico (90% choque pequeno negativo, 10% choque grande
    positivo), recentrado para média exata 0 -- mesma família do espelho
    WSR."""

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
    assert rate == 0.0
    assert rate <= 0.05


def test_type_i_sob_peeking_diario_nao_excede_alpha_regime_variancia_alta():
    """Variância bem mais alta que o regime real (uniform(-0.3,0.3)) -- ainda
    sob o nulo EXATO (média 0). AO CONTRÁRIO do espelho WSR (cujo teste
    análogo exige ``rate > 0`` para provar não-degenerado), aqui a taxa é
    EXATAMENTE zero: a construção CS aditiva do HRMS precisa de MUITO mais
    sessões que 300 para acumular evidência suficiente contra ruído
    puro de variância alta -- ver o teste de não-degenerado via caminho
    determinístico extremo
    (``test_caminho_extremo_deterministico_prova_nao_degenerado_do_boundary``)
    e o teste determinístico de viés sustentado abaixo para a prova de que o
    gate consegue rejeitar quando há efeito real."""

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
    assert rate == 0.0
    assert rate <= 0.05


def test_type_i_monte_carlo_em_regime_artificial_detecta_erro_de_escala():
    """Os três testes de Type-I acima (regime real, horizonte=300/alpha=0,05)
    dão taxa zero NAS 5000 RÉPLICAS SIMULADAS -- isso confirma que o boundary
    não rejeita demais nesse regime observado, mas NÃO é evidência de
    calibração Type-I no regime de produção: uma taxa amostral zero com N
    finito não é o mesmo que taxa populacional zero (existe caminho
    admissível -- ex. ``delta=-0.3`` constante cruza o HRMS na sessão 77 sob
    ``alpha=.05`` -- logo uma vizinhança dele tem probabilidade positiva sob
    ``uniform(-.3,.3)``, só não observada em 5000 réplicas). Este teste NÃO
    tenta resolver isso: continua sendo um golden test de regressão
    (detecta erro de escala grosseiro, ex. ``HRMS_C`` errado por um fator),
    não uma prova de cobertura/poder Type-I do regime produtivo -- essa
    evidência exigiria importance sampling/rare-event simulation no regime
    real (``alpha<=0,05/K``, horizonte 60-300), não feito aqui.

    Este teste busca deliberadamente um regime ARTIFICIAL onde o boundary
    aditivo/LIL cruza com frequência mensurável sob o nulo exato (média 0),
    só para que a simulação Monte Carlo tenha PODER de detectar uma
    calibração errada (o valor ``rate == 0.0016`` é sensível o bastante para
    isso: mutar ``HRMS_C`` de 2 para 1 ou 4 muda a taxa observada para
    17/5000 e 1/5000 respectivamente, verificado manualmente), não só
    confirmar "nunca rejeita" -- o que os três testes acima, sozinhos, não
    conseguem fazer.

    ``alpha=0.99`` NÃO é representativo de produção (lá ``alpha<=0.05`` /
    K -- ver ``candidate_alpha`` em ``evaluate_champions``) -- é escolhido só
    por tratabilidade computacional: o boundary polynomial-stitching é tão
    conservador (tipo lei-do-logaritmo-iterado) que nenhuma combinação de
    ``alpha<=0,9``/``horizon<=3000`` produziu uma única rejeição em 5000
    réplicas durante a varredura que motivou este teste. Só na combinação de
    ``alpha~0,99`` + ``horizon~5000`` + variância próxima do extremo do
    domínio (``uniform(-0.95,0.95)``) a taxa se torna observável e não-zero
    (0,16%, 8 de 5000 réplicas) permanecendo, como exigido, <= alpha.
    """
    def near_domain_extreme(rng):
        return rng.uniform(-0.95, 0.95)

    rate = _type_i_false_positive_rate(
        near_domain_extreme,
        n_replicas=5000,
        horizon=5000,
        alpha=0.99,
        min_sessions=60,
        seed=1234,
    )
    assert rate == pytest.approx(0.0016, abs=1e-9)
    assert 0.0 < rate <= 0.99


def test_vies_constante_sustentado_e_rejeitado_dentro_do_horizonte_de_300_sessoes():
    """Prova de não-degenerado por construção determinística (complementa os
    testes de Type-I acima, que são todos zero neste horizonte, e o caminho
    extremo do teste anterior): um viés condicional GRANDE e sustentado
    (``delta=-0.10`` constante, cerca de 3x o desvio-padrão típico por sessão
    ``sqrt(1e-3)~0.032`` documentado no comentário de ``HRMS_V_MIN`` -- não
    confundir com o próprio ``HRMS_V_MIN=0.06``, que é ``min_sessions`` vezes
    essa variância, não a variância em si) é detectado dentro do horizonte de
    300 sessões, mas com margem estreita
    (~11%) após a correção do ``HRMS_C`` (c=2/3 -> c=2 torna o boundary ~3x
    mais largo): cruza em n=267 com UCB final=-0.011149 (antes da correção
    cruzava em n=90 com UCB=-0.070157 -- o gate ficou estruturalmente mais
    conservador, não quebrou). Este teste é uma regressão sensível à
    constante, não uma demonstração de folga confortável de poder."""
    deltas = [-0.10] * 300
    alpha = 0.05 / 4  # candidate_alpha com K=4 (miqueias/v1/v2/climatologia)

    result = _hrms_sequential_test(deltas, alpha=alpha, min_sessions=60)

    assert result["rejects_null"] is True
    assert result["first_rejection_session"] == 267
    assert result["upper_confidence_bound"] == pytest.approx(-0.011149, abs=1e-6)


def test_vies_pequeno_sustentado_diferencia_sequential_e_long_run_winner():
    """Diferenciador correto pós-correção do bug de ``running_v`` E do bug de
    escala ``HRMS_C`` (c=2/3 -> c=2, ver módulo principal): um viés
    condicional pequeno e sustentado (``delta=-0.02``, mesma ordem de
    grandeza do desvio-padrão típico por sessão ``sqrt(1e-3)~0.032``
    documentado no comentário de ``HRMS_V_MIN`` -- comparação em desvio-
    padrão, não em variância, que tem unidade diferente de ``delta``) faz o
    WSR (``sequential_winner``) rejeitar em n=300 sessões enquanto o HRMS
    (``long_run_winner``) ainda NÃO rejeita -- consistente com o tradeoff já
    documentado em ``LAMBDA_MAX`` (WSR mais potente que HRMS no regime de
    variância baixa). Isto NÃO é uma falha do HRMS: o mesmo efeito constante
    É detectado pelo HRMS com mais sessões -- ver a asserção final, que prova
    que a diferença é de PODER (sample size), não de o HRMS ser incapaz de
    responder à mesma pergunta. Com o ``HRMS_C`` corrigido (boundary ~3x mais
    largo), o número de sessões necessário cresceu de n=448 para n=1333 --
    quase 3x o horizonte anterior (1333/448~2,98x), uma diferença de poder bem mais acentuada
    do que a documentada antes da correção, mas ainda assim finita e
    alcançável (não "nunca")."""
    alpha = 0.05 / 4
    deltas_300 = [-0.02] * 300

    wsr = _empirical_bernstein_sequential_test(deltas_300, alpha=alpha)
    hrms = _hrms_sequential_test(deltas_300, alpha=alpha, min_sessions=60)

    assert wsr["rejects_null"] is True
    assert wsr["log_capital"] == pytest.approx(4.466584, abs=1e-6)
    assert hrms["rejects_null"] is False
    assert hrms["upper_confidence_bound"] == pytest.approx(0.068851, abs=1e-6)

    # O HRMS detecta o MESMO efeito com mais sessões -- diferença de poder,
    # não de validade nem de "incapaz de rejeitar".
    deltas_1333 = [-0.02] * 1333
    hrms_1333 = _hrms_sequential_test(deltas_1333, alpha=alpha, min_sessions=60)
    assert hrms_1333["rejects_null"] is True
    assert hrms_1333["first_rejection_session"] == 1333


def test_regime_alternante_com_media_acumulada_zero_nao_cruza_o_limiar_hrms():
    """Espelha ``tests/test_empirical_bernstein_sequential_test.py::
    test_regime_alternante_pode_cruzar_o_limiar_com_media_acumulada_
    exatamente_zero`` -- mesma sequência adversarial (180 sessões de -0.10,
    30 alternando -0.80/+0.80, 20 de +0.90), usada lá para mostrar que o WSR
    PODE cruzar seu limiar mesmo com a soma acumulada FINAL (t=230)
    exatamente zero. Aqui o HRMS NÃO cruza -- mas a explicação correta NÃO é
    "``mu_t``/a média acumulada é zero em todo prefixo desta série" (é zero
    SÓ no agregado final: a soma até t=180, fim da fase constante, é -18.0,
    não zero -- ver revisão codex-r job k2nbpj5bu/relay-mrv3uvan-6sihnq, que
    apontou essa imprecisão numa versão anterior deste docstring). Mecanismo
    real (confirmado rodando ``_hrms_bound_trace`` sessão a sessão -- ver
    revisão codex-r job kgfwk9zyy/relay-mrv5nlit-n1ehqq, que apontou uma
    segunda imprecisão numa versão anterior desta explicação): enquanto
    ``delta`` é constante (t=1..180), ``mu_hat_{i-1}`` rastreia esse valor a
    partir de i=2, então ``V_t`` fica travado em ~0.01 -- ABAIXO do piso
    ``HRMS_V_MIN=0.06`` --, e o termo de boundary fica CONSTANTE nessa fase
    inteira (``~26.6554``), não dirigido por variância acumulada (quase não
    há nenhuma). Como ``UCB(t) = -0.10 + 26.6554/t`` decresce
    monotonicamente em ``t`` enquanto a fase dura, o mínimo do trecho
    considerado (``min_sessions=60`` em diante) cai exatamente no maior
    ``t`` da fase, ``t=180`` (``trace[179]~=0.048``) -- não porque a média
    acumulada ali seja zero (é -0.10), mas porque é o último ponto antes de
    ``V_t`` saltar para ~0.50 já em t=181 (primeiro salto ``+-0.80``) e
    seguir crescendo, empurrando a UCB de volta para cima (0.147 em t=210,
    0.238 em t=230) sem nunca voltar perto de zero. A folga em t=180 só é
    estreita depois de normalizada por ``t`` -- a folga bruta do boundary
    sobre o déficit acumulado (``26.6554-18.0=8.6554``) não é pequena por si
    só. Confirmado numericamente abaixo: nem o UCB final (t=230) nem o
    mínimo corrente desde ``min_sessions=60`` (que ocorre em t=180) cruza
    zero."""
    deltas = [-0.10] * 180
    for _ in range(15):
        deltas.append(-0.80)
        deltas.append(0.80)
    deltas += [0.90] * 20
    assert len(deltas) == 230
    assert statistics.fmean(deltas) == pytest.approx(0.0, abs=1e-9)

    alpha = 0.05 / 4
    trace = _hrms_bound_trace(deltas, alpha=alpha)
    result = _hrms_sequential_test(deltas, alpha=alpha, min_sessions=60)

    assert trace[-1] == pytest.approx(0.2379980709341325, abs=1e-9)
    assert min(trace[59:]) == pytest.approx(0.04808557453128522, abs=1e-9)
    assert result["rejects_null"] is False
    assert result["first_rejection_session"] is None


def test_reversao_de_regime_com_media_final_zero_nao_deve_ser_promovida():
    """Contraexemplo que expõe (e trava a correção d)o bug da semântica
    "pegajosa" do mínimo histórico: 300 sessões de ``delta=-0.10`` (viés
    negativo sustentado, o MESMO trecho testado isoladamente em
    ``test_vies_constante_sustentado_e_rejeitado_dentro_do_horizonte_de_300_sessoes``,
    onde cruza o limiar em n=267) seguidas de 300 sessões de ``delta=+0.10``
    (reversão completa) -- a média acumulada FINAL (``mu_600``) é exatamente
    zero, então ``long_run_winner`` não deveria promover o candidato: não há
    vantagem de longo prazo no presente, e a decisão operacional (reavaliada
    diariamente pelo timer systemd sobre o ledger append-only) é sobre
    ``mu_t`` AGORA, não sobre se algum trecho histórico já cruzado o zero.

    Antes desta correção, ``rejects_null`` usava o MÍNIMO de todas as UCBs
    desde ``min_sessions``: como a fase 1 (idêntica ao teste de viés
    constante acima) já cruzava o zero em n=267 e ficava negativa até o fim
    da fase 1, o mínimo histórico ficava travado em ``-0.011149`` para
    sempre, mesmo após a reversão completa -- ``rejects_null`` reportava
    ``True`` com base em evidência que já havia revertido. A correção usa só
    ``trace[-1]`` (a UCB da sessão atual), que reflete corretamente que
    ``mu_600 = 0`` não é significativamente negativo."""
    deltas = [-0.10] * 300 + [0.10] * 300
    assert len(deltas) == 600
    assert statistics.fmean(deltas) == pytest.approx(0.0, abs=1e-9)

    alpha = 0.05 / 4
    trace = _hrms_bound_trace(deltas, alpha=alpha)
    result = _hrms_sequential_test(deltas, alpha=alpha, min_sessions=60)

    # UCB corrente (mu_600): corretamente não-significativo.
    assert trace[-1] == pytest.approx(0.07134923176968339, abs=1e-9)
    # Mínimo histórico (herdado da fase 1, ainda exposto como diagnóstico):
    # seria o valor que travava rejects_null=True antes da correção.
    assert min(trace[59:]) == pytest.approx(-0.011148655281229432, abs=1e-9)
    assert result["upper_confidence_bound"] == pytest.approx(0.071349, abs=1e-6)
    assert result["running_min_upper_confidence_bound"] == pytest.approx(
        -0.011149, abs=1e-6
    )
    assert result["rejects_null"] is False
