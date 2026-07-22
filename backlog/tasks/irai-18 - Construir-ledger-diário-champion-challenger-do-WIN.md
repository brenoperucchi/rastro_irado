---
id: IRAI-18
title: Construir ledger diário champion-challenger do WIN
status: Review
assignee:
  - '@codex'
created_date: '2026-07-16 04:41'
updated_date: '2026-07-22 06:08'
labels:
  - validation
  - win
  - p-dynamic
  - gex
dependencies: []
references:
  - 'backlog://task/IRAI-17'
documentation:
  - docs/plans/2026-07-13-irai-plano-consolidado.md
priority: high
ordinal: 18000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Preservar, por sessão e de forma reproduzível, os dados necessários para comparar P Dinâmico do Miqueias, IRAI v1/v2 e versões futuras sem depender do Firebase corrente. O bundle deve reunir as séries de P, WIN M5 e sinais locais disponíveis, além do snapshot GEX/MID, e alimentar um avaliador que não declare vencedor abaixo do gate mínimo de amostra.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Cada captura preserva séries brutas de Miqueias, v1 e v2, metadados de origem e timestamp da coleta
- [x] #2 O bundle preserva WIN OHLC e campos Pair/NWE presentes nas séries locais, além do snapshot GEX/MID disponível para a sessão
- [x] #3 O avaliador calcula métricas de qualidade probabilística somente em barras operacionais e sessões fechadas
- [x] #4 O relatório distingue avaliação do objetivo diário do P da utilidade econômica como gate tático
- [x] #5 Abaixo do gate mínimo de sessões o resultado é INCONCLUSIVO e nenhum quality_winner é promovido
- [x] #6 Testes permanentes cobrem montagem do bundle, sessão incompleta, ausência de GEX e gate de amostra
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Auditar contratos API/Firebase/GEX e definir schema versionado do ledger.
2. Especificar por testes a captura atômica e os gates de sessão/amostra.
3. Implementar captura completa reutilizando o comparador existente.
4. Implementar avaliação champion-challenger para o objetivo diário, mantendo o gate tático separado.
5. Executar no Ryzen, publicar e registrar limitações.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Auditoria no Ryzen5WSL: `/api/irai/series` já expõe P, WIN, Pair, NWE, VWAP e ATR por barra; `/api/irai/gex` expõe gamma max/flip/min, walls e `mid_wall` separadamente. Banco de produção: WIN M5 tem 138.646 barras desde 2021-07-12, mas `gex_levels` possui apenas 2 datas (2026-07-10..2026-07-13). O ledger precisa começar imediatamente e o avaliador deve bloquear qualquer vencedor abaixo do gate.

Implementação local concluída: bundle versionado e atômico preserva documentos brutos Miqueias/v1/v2, manifesto de fechamento BRT, GEX/walls/mid_wall e relatório de paridade. Avaliador agrega Brier/log-loss dentro da sessão, inclui baseline climatológico causal Beta(1,1), exige 60 sessões comuns e bootstrap pareado IC95% contra todos os concorrentes; o gate tático permanece NOT_EVALUATED. Timer diário proposto para 17:56 BRT, somente leitura das APIs.

Validação produtiva no Ryzen5WSL após pull de `4495ac2`: 16 testes específicos passaram; serviço oneshot executou com status 0; bundle real preservou envelopes v1/v2, WIN/Pair/NWE, GEX ativo com 17 walls e 16 mid_walls. Captura pré-mercado foi corretamente marcada `closed=false`; avaliador retornou `INCONCLUSIVE`, 0/60 sessões, `quality_winner=null` e gate tático `NOT_EVALUATED`. Timer diário `rastro-irado-p-dynamic-ledger.timer` habilitado para Mon..Fri 17:56 BRT. Suíte mantida: `pytest -q tests --ignore=tests/test_measure_tactical_gate3.py` → 207 passed, 16 skipped. `pytest -q` global não é utilizável neste Linux porque coleta scripts/archive que exigem MT5 e um teste que exige sklearn.

Correção pós-revisão: a engine e `/api/irai/series` agora expõem `win_bar_open`, `win_high` e `win_low` por barra real, preservando `win_open` como abertura da sessão. Regressão permanente falhou antes com AttributeError em `IRAISnapshot.win_bar_open` e passou após a correção; o teste do bundle confirma persistência dos três campos.

Validação pós-correção no Ryzen5WSL (`444cc00`): regressão engine OHLC 2 passed; regressão HTTP OHLC 1 passed; ledger/evaluator 7 passed. API Windows reiniciada com o mesmo Uvicorn e retornou health ok; payload v1 contém as três chaves novas. Serviço oneshot gerou bundle `2026-07-16T050637Z` com status 0 e contrato OHLC preservado; valores nulos são esperados nas barras ghost pré-mercado.

Correção do NO-GO: fallback de `brt_offset_h` agora usa a regra sazonal compartilhada quando o envelope local não informa offset; o manifesto registra status de fechamento por fonte e só fecha com todas as fontes capturadas completas e ao menos uma local. O loader recalcula o fechamento dos documentos e rejeita manifesto antigo/corrompido com outcome parcial. Duas regressões novas falharam antes (janeiro +5h; v2 parando 17:30) e agora passam. Suíte mantida: 209 passed, 17 skipped.

Validação systemd revelou que o novo import de timezone dependia do cwd. Regressão subprocess fora da raiz falhou antes com `ModuleNotFoundError: backend`; o CLI agora adiciona explicitamente a raiz do repositório ao `sys.path`. Suíte mantida atual: 210 passed, 17 skipped.

Validação final no Ryzen5WSL após `13334ef`: 19 testes específicos passaram; serviço systemd executou com Result=success/ExecMainStatus=0; bundle `2026-07-16T051102Z` registrou offset 6 e status separado de Miqueias/v1/v2, todos corretamente incompletos no pré-mercado.

2026-07-21: revisão xhigh automática foi abortada; validação manual confirmou três falhas de integridade. (1) chronological_replay escolhe posterior Kalman errado quando há barra B3 real antes de 09:00 BRT; (2) engine_revision não inclui a calibração carregada de model_params; (3) walk-forward aceita barra fora da grade M5 que fecha após decision_time. Implementação reaberta: acrescentar regressões, corrigir e revisar independentemente antes de aceitar novas sessões do ledger.

2026-07-21: correção pós-revisão xhigh. Confirmados e corrigidos: (1) chronological_replay encadeava posterior errado com print B3 pré-abertura; (2) identidade runtime não cobria calibração/configuração que altera P_up; (3) git_commit fragmentava o ledger apesar de semântica idêntica; (4) walk-forward aceitava print fora da grade M5 ainda em formação. Revisão independente pipeline_deep_reviewer: GO após remediações adicionais (retry SQLite transitório, hash específico do WIN, rejeição de target fora do contrato, fail-closed sem WIN ativo). Metodologia sobe de 3 para 4; bundle v3 será preservado como superseded e a nova contagem inicia 0/60 após implantação. Validação: pytest -q tests -> 477 passed, 1 skipped; pytest focado -> 129 passed; git diff --check; python3 -m py_compile dos módulos alterados.

2026-07-21 (cont.): revisão `/codex-r` do item 2 (gate anti-peeking empirical-Bernstein WSR) em `scripts/evaluate_p_dynamic_champions.py`: veredito "NO-GO metodológico como documentado, GO se o estimando for explicitado" — o docstring afirmava H0 marginal (media(delta)>=0) quando a desigualdade de Ville garante apenas o nulo CONDICIONAL (E[delta_i|F_{i-1}]>=0), com contraexemplo de regime alternante válido. Corrigido só na documentação (módulo, LAMBDA_MAX, VARIANCE_FLOOR, docstrings das duas funções internas); nenhuma mudança de lógica. Suíte: 492 passed, 1 skipped.

Painel tri-r (deep-reasoner + fable-reasoner + codex, cego, focos distintos) resolveu tensão remanescente: consulta de design original do codex (que chegou atrasada nesta sessão) preferia HRMS (Howard/Ramdas/McAuliffe/Sekhon 2021) em vez do WSR já implementado por convergência cega 2-de-3. Veredito do painel: MANTER WSR agora. Convergência: (1) WSR não exige média condicional comum para ser válido — codex retratou a razão original da própria recomendação, confirmando o ponto independente do deep-reasoner; (2) HRMS-como-especificado sofreria regressão de poder de ~4-4.5x no regime real de baixa variância do torneio (deep-reasoner e fable-reasoner derivaram isso de forma independente); (3) IUT com alpha/K por candidato, LAMBDA_MAX (power-only) e VARIANCE_FLOOR (nunca vincula em contagem realista de sessões) confirmados corretos pelas três lentes.

Achado residual do codex, verificado por mim via execução direta de _empirical_bernstein_log_capitals (não aceito por confiança): o docstring corrigido continha um overclaim — "roster congelado garante coincidência entre nulo condicional e marginal" é falso; roster fixo evita multiplicidade de seleção de modelo/época mas não garante essa coincidência sob deriva de regime. Contraexemplo numérico confirmado (230 sessões, média exata 0, log-capital final 5.8295 > log(1/alpha_candidato)=4.3820, cruza em t=61). Classificado pelo próprio codex como P2 metodológico/documental, não bloqueante — só escalaria se "vencedor" for redefinido como "média acumulada de longo prazo positiva". Fix aplicado: docstring corrigido novamente removendo o overclaim + teste de regressão permanente novo (test_regime_alternante_pode_cruzar_o_limiar_com_media_acumulada_exatamente_zero) travando os valores numéricos verificados. Suíte final: 492 passed, 1 skipped. Nenhum commit feito ainda — aguardando reação do usuário ao relatório consolidado tri-r.

2026-07-21 (decisão de produto, HUMANO): usuário rejeitou classificar a distinção condicional-vs-marginal como P2 não-bloqueante. Decisão registrada: quality_winner é ambíguo e será substituído por DOIS campos explícitos — sequential_winner (rename do WSR atual, evidência sequencial sob o nulo condicional, útil para vantagem sustentada no regime observado) e long_run_winner (nova construção, CS anytime-valid sobre a média acumulada de longo prazo, útil como baseline estratégico). WSR não deve ser tratado como promoção de modelo — é evidência tática, não resposta a 'qual versão usar em produção'. Nenhum dado será descartado nem a coleta reiniciada: o ledger append-only já preserva os deltas necessários; o novo campo é recalculado sobre a mesma série, com metodologia e recorte versionados (bump de METHODOLOGY_VERSION). HRMS será avaliado como teste da pergunta de longo prazo (o estimando que ele mira nativamente), não como substituto de menor poder para o WSR — a comparação de poder ~4-4.5x do painel tri-r não se aplica a essa pergunta diferente. Em andamento: derivação independente da fórmula exata do HRMS (deep-reasoner + fable-reasoner em paralelo) antes de implementar, dado o histórico de bugs sutis de fórmula neste mesmo arquivo.

Verificação primária concluída p/ a constante de escala c do HRMS (polynomial stitching, Howard-Ramdas-McAuliffe-Sekhon 2021, AoS 49(2), DOI 10.1214/20-AOS1991): conflito deep-reasoner (c=2/3) vs fable-reasoner (c=2) resolvido a favor de c=2/3. Evidência decisiva: a documentação R do próprio pacote dos autores (github.com/gostevehoward/confseq, poly_stitching_bound.Rd) chama `c` explicitamente de 'sub-gamma scale parameter' -- termo padrão de Boucheron-Lugosi-Massart, onde a redução clássica de Bennett-para-sub-gamma dá c=b/3 para uma variável real centrada quase-certamente limitada por b (mesma constante '1/3' da convenção padrão p/ variável em [0,1]). No nosso caso Y_i = delta_i - D_hat_preditivo está em [-2,2] (b=2), logo c=2/3. O exemplo generic_ate_bound (c=2/min(treat_p,1-treat_p)) que parecia contradizer isso NÃO é confiável como contraevidência: não tenho a fórmula exata do estimador AIPW daquele caso p/ re-derivar o bound b real, então não invalida a regra clássica.

Fórmula do boundary confirmada via código de referência dos próprios autores (confseq C++ uniform_boundaries.h classe PolyStitchingBound, e cspaper R/boundaries.R função poly_stitching_bound): forma CORRETA é a mais apertada `u(v) = sqrt(k1^2*max(v,v_min)*ell(v) + second_term^2) + second_term` (second_term=k2*c*ell(v)), NÃO a forma aditiva simples `k1*sqrt(v*ell)+second_term` que as duas lentes (deep-reasoner e fable-reasoner) tinham proposto inicialmente no pseudocódigo -- fable-reasoner já tinha sinalizado essa forma alternativa como algo a conferir, e essa conferência confirmou a forma apertada. k1=(eta^0.25+eta^-0.25)/sqrt(2), k2=(sqrt(eta)+1)/2, eta=2/s=1.4 (defaults do paper, fixados a priori) dão k1≈1.435514, k2≈1.207107 -- confirmado por 4 fontes independentes (deep-reasoner, fable-reasoner, confseq C++, cspaper R). zeta(1.4) verificado numericamente via scipy.special.zeta = 3.1055472779775815 (bate com a estimativa de fable-reasoner ≈3.1055).

Despachado ao deep-reasoner (1 chamada final, focada) o fechamento do algoritmo exato: tradução do boundary bilateral p/ teste unicaudal H0:mu>=0, definição precisa de V_n (tempo intrínseco preditível, recorrência exata), ordem preditiva passo-a-passo (mesma disciplina da função WSR irmã), fórmula exata do traço por sessão, e escolha a priori de v_min sem depender do ledger real. Aguardando retorno antes de escrever `_hrms_bound_trace`/`_hrms_sequential_test` em scripts/evaluate_p_dynamic_champions.py. Nenhuma linha de código nova ainda.

2026-07-21 (cont.): implementação de _hrms_bound_trace/_hrms_sequential_test recebida do deep-reasoner e escrita nesta sessão continha um bug de acumulação: v_hat_i = max((HRMS_VARIANCE_PRIOR + sum_sq)/i, VARIANCE_FLOOR) somado em running_v produzia crescimento espúrio running_v ~ HRMS_VARIANCE_PRIOR*ln(n) mesmo sob variância verdadeira zero (cada termo já era uma média corrente rediluída por 1/i; soma de n termos ~1/i cresce como ln(n)). Sintoma: teste de viés constante delta=-0.10 x300 sessões só cruzava o limiar quase no fim (n=300, UCB=-0.044) em vez de rejeitar com folga bem antes.

Correção verificada por triangulação em fontes primárias (sem precisar despachar deep-reasoner de novo): fetch de github.com/gostevehoward/confseq/{misc.py,conjmix_bounded.py} (implementação de referência dos próprios autores). Duas construções empirical-Bernstein independentes (predmix_empbern_lower_cs e conjmix_empbern_lower_cs) convergem na mesma convenção: V_t = cumsum((x_i - mu_hat_{i-1})^2), soma CRUA sem divisão por índice e sem prior somado por passo; docstring de conjmix confirma v_opt=t*sigma^2 (linear em t, nunca log(t)). Fix: running_v += (delta - mu_pred)**2 direto, mu_pred = sum_delta/(index-1) if index>1 else 0.0 (0.0 = centro do domínio [-1,1], já era o valor usado). HRMS_VARIANCE_PRIOR removida inteiramente; HRMS_V_MIN (floor externo em _poly_stitching_bound) mantido como único regularizador de amostra pequena, agora consistente (running_v cresce linear, não log).

Validação numérica pós-fix: delta=-0.10 x300, alpha=0.05/4 -> rejeita em n=90 (UCB=-0.070157), running_v fica flat em 0.01 (era log-crescente antes). Diferenciador limpo WSR-vs-HRMS (mesmo efeito, delta=-0.02 constante): em n=300 WSR rejeita (log_capital=4.466584) mas HRMS não (UCB=0.009843); em n=500 HRMS já rejeita (n=448) -- demonstra diferença de PODER, não de capacidade, consistente com LAMBDA_MAX já documentado. Enumeração exaustiva (Bernoulli simétrico/assimétrico, horizonte<=14): todas as taxas de rejeição <= alpha, não-degenerada em horizonte>=12. Monte Carlo Type-I (N=5000, horizonte=300, alpha=0.05, min_sessions=60, 3 regimes de variância espelhando o WSR): as 3 taxas = 0.0 exatamente -- conservadorismo genuíno da construção aditiva vs a multiplicativa/betting do WSR no regime de baixa variância real do torneio, não um teste quebrado (evidenciado pelos testes complementares acima que TEM poder real).

Teste novo tests/test_hrms_sequential_test.py (9 testes, todos verdes): reimplementação de referência independente (double-loop) batendo com a versão vetorizada; enumeração exaustiva; Monte Carlo Type-I nos 3 regimes; viés constante sustentado; diferenciador de poder WSR-vs-HRMS. tests/test_p_dynamic_champion_evaluator.py atualizado (4 blocos) para os campos renomeados quality_winner->sequential_winner+long_run_winner, winner_tests->sequential_tests+long_run_tests, status 'WINNER'->'EVALUATED', usando valores reais recomputados (não hand-derived). Suíte mantida (tests/): 501 passed, 1 skipped (baseline pré-janela 492 passed + 9 testes novos = 501, sem regressão). Nenhum commit feito ainda.

2026-07-21 (correção retroativa desta mesma sessão): a nota anterior 'conflito deep-reasoner (c=2/3) vs fable-reasoner (c=2) resolvido a favor de c=2/3' estava ERRADA. Nova revisão /codex-r desta fatia (rodada após o fix do bug de acumulação de running_v) re-levantou a mesma disputa de forma independente. Resolvi desta vez por extração direta do texto primário do paper (curl do PDF em arXiv:1810.08240 + parsing local via pypdf, não WebFetch -- duas tentativas de WebFetch anteriores deram respostas que pareciam autoritativas mas eram geradas por conhecimento genérico, uma delas admitindo textualmente que o trecho relevante estava truncado). Achado: o Theorem 4 do paper (a construção REALMENTE implementada aqui -- empirical-Bernstein autonormalizado via self-normalization, não a redução de Bennett) fixa `c=b-a` DIRETAMENTE no próprio enunciado ('scale c = b-a'), confirmado pelo exemplo numérico do paper logo em seguida (c=1 para X_i em [0,1], onde b-a=1). A redução clássica de Bennett-para-sub-gamma c=b/3 (que a nota anterior citou via poly_stitching_bound.Rd) é para o Corollary 3 do MESMO paper -- uma construção DIFERENTE (LIL de autovalor máximo de matriz) -- não para o Theorem 4. A nota anterior aplicou por engano a convenção de escala de um teorema a outro teorema do mesmo artigo.

Fix aplicado: HRMS_C de 2.0/3.0 para 2.0 (delta em [-1,1], b-a=2) em scripts/evaluate_p_dynamic_champions.py, com comentário atualizado citando a distinção Theorem 4 vs Corollary 3 e o método de verificação (extração de PDF, não WebFetch). Todos os testes numéricos dependentes em tests/test_hrms_sequential_test.py foram recomputados rodando o código real (não à mão): trace golden de referência (5.723387877474716), teste de viés sustentado delta=-0.10x300 (agora rejeita em n=267, UCB=-0.011149, era n=90/-0.070157), diferenciador WSR-vs-HRMS delta=-0.02 (WSR ainda rejeita em n=300 log_capital=4.466584 inalterado; HRMS agora precisa de n=1333 para rejeitar o mesmo efeito, era n=448 -- ~3x mais sessões, boundary ficou ~3x mais largo). O teste de enumeração exaustiva Bernoulli(+-1) que antes provava 'não-degenerado' via taxa exata positiva em horizon=12 agora dá taxa exata 0.0 nesse horizonte (boundary mais largo) -- horizontes maiores são inviáveis por enumeração (o caminho mais adversarial, delta=-1.0 constante, só cruza em n=32, e 2^32 caminhos não enumera; tentei uma DP para evitar enumeração completa e ela estourou memória, >18GB, matei o processo). Substituí por um novo teste com prova fechada via caminho determinístico extremo: sob delta=-1.0 constante, running_v fica constante em 1.0 para sempre (mu_pred rastreia exatamente o caminho desde i=2), dando forma fechada trace(i)=(-i+B)/i com B=_poly_stitching_bound(1.0,alpha) constante, cruzando em i=32 -- como esse caminho tem probabilidade exata (1/2)^32 sob o nulo Bernoulli(+-1) verdadeiro, P(reject)>=(1/2)^32>0, provando não-degenerado sem enumeração. Suíte completa: 502 passed, 1 skipped (501 anterior + 1 teste novo, sem regressão).

Também corrigidas nesta fatia duas imprecisões de documentação apontadas pelo /codex-r anterior (P2/P3, não bloqueantes mas reais): (P2) a descrição do estimando de long_run_winner trocou 'média marginal/histórica' genérica por mu_t = t^-1 * soma E[delta_i|F_{i-1}] explícito (média das expectativas CONDICIONAIS ao longo do tempo -- só coincide com a média marginal populacional clássica sob estacionariedade, que não é assumida). (P3) removida a alegação de que sequential_winner e long_run_winner são testes 'INDEPENDENTES' -- rodam sobre a MESMA série de delta, não são independentes como variáveis aleatórias; o que é de fato separado é a RESERVA DE ALPHA (orçamento próprio por teste, não compartilhado), que é o que licencia o union bound (combined_family_wise_alpha_bound<=2*alpha), união que não depende de independência entre os testes. (P3) linguagem 'estruturalmente mais conservadora' (implicando ordenação de poder universal HRMS<WSR) foi escopada para os regimes/horizonte especificamente testados, deixando explícito que não é uma alegação geral. (P3) teste tests/test_p_dynamic_champion_evaluator.py::test_avaliador_promove_apenas_vencedor_significante_no_gate_sequencial renomeado para test_sequential_winner_separa_taticamente_sem_autorizar_promocao_via_long_run_winner (o nome antigo dizia 'promove' para o gate WSR, que não tem autoridade de promoção -- o teste demonstra exatamente o oposto: long_run_winner fica None). Follow-up /codex-r desta fatia completa (fix + recomputação + doc) despachado nesta sessão, aguardando retorno. Nenhum commit feito ainda.

2026-07-21 (cont., retorno do follow-up /codex-r desta mesma sessão): revisão recebida (task k010troej) confirmou o fix de HRMS_C=2.0 e as recomputações numéricas, mas achou um P1 REAL (não documental): em `_hrms_sequential_test`, `rejects_null = running_min <= 0.0` onde `running_min = min(trace[min_sessions-1:])` -- o mínimo de TODAS as UCBs históricas. Isso é 'pegajoso': uma vez que qualquer UCB cruzou zero no passado, o campo fica True para sempre, mesmo que a média acumulada atual (`mu_t`) tenha revertido completamente. Contraexemplo do Codex, reproduzido e CONFIRMADO por mim rodando o código real: 300 sessões delta=-0.10 seguidas de 300 delta=+0.10 (média final EXATA=0, sem vantagem real hoje) ainda retorna rejects_null=True (running_min=-0.011149 da 1a fase, UCB atual=+0.071349, positiva/correta). Isso é uma inconsistência genuína entre a garantia formal (confidence sequence dá cobertura simultânea de mu_n em CADA n respectivo, não que min_i U_i valha para o mu_t atual quando mu_t não é estacionário -- e a não-estacionariedade é uma escolha DELIBERADA já documentada nesta mesma sessão) e o propósito operacional do campo (decisão reavaliada a cada sessão nova pelo timer, não um experimento de parada única). Como isso redefine a semântica central da autoridade de promoção (mesma classe de decisão de desenho estatístico que já foi escalada ao usuário antes nesta task -- ver nota 'decisão de produto, HUMANO' acima), despachei deep-reasoner e fable-reasoner em paralelo, cegos entre si e cegos ao veredito do codex, para triangular a correção correta antes de agir (painel de 3 lentes: codex já deu a dele nesta review). Aguardando os dois retornos antes de decidir/implementar qualquer fix de P1.

Enquanto isso, corrigidas nesta mesma fatia as imprecisões menores (P2/P3) que sobreviveram à checagem: (1) linha do relatório final dizia 'H0 marginal' para long_run_winner, incoerente com a correção de mu_t já feita -- corrigido para citar mu_t explicitamente; (2) docstring de _hrms_bound_trace tinha um erro de dupla-média ('média(mu_1..mu_n) > U_n)', deveria ser só 'mu_n > U_n)', já que mu_n já É a média -- corrigido; (3) a alegação 'as duas [mu_t e média marginal] só coincidem sob estacionariedade' era forte demais -- estacionariedade sozinha não garante isso, precisa de algo como expectativa condicional constante para coincidência EXATA, ou estacionariedade+ergodicidade para coincidência ASSINTÓTICA (t->infinito, não em t finito) -- corrigido no docstring do módulo; (4) tests/test_hrms_sequential_test.py linha 36 dizia 'alpha=0,05/4' pros três testes Monte Carlo de Type-I, mas eles rodam com alpha=0.05 puro -- corrigido; (5) docstring do teste de caminho extremo alegava que o caminho delta=-1 constante 'perde em magnitude' contra qualquer outro caminho -- falso para outros caminhos Bernoulli(+-1), que têm a mesma magnitude 1 em todo passo; o que torna aquele caminho especial é running_v ficar constante (não a magnitude) -- corrigido, removida a alegação errada; (6) teste 'com_folga_confortavel' renomeado (margem real é só ~11%, 267 de 300) e docstring comparava delta=-0.10 diretamente contra 'variância típica ~1e-3' (unidades incompatíveis, mean vs variance) -- corrigido para comparar contra o desvio-padrão sqrt(1e-3)~0.032; MESMO ERRO replicado no teste diferenciador WSR-vs-HRMS (delta=-0.02), também corrigido; (7) nesse mesmo teste, 'cresceu de n=448 para n=1333 -- quase 4x' estava aritmeticamente errado (1333/448=2,98, ou seja quase 3x, não 4x) -- achado por mim, não pelo codex, corrigido; (8) achei um cross-reference pendurado: o docstring do módulo principal (long_run_winner) afirma que a mesma sequência adversarial do teste WSR test_regime_alternante_pode_cruzar_o_limiar_com_media_acumulada_exatamente_zero 'NÃO deve cruzar o limiar do HRMS, ver o teste espelhado em tests/test_hrms_sequential_test.py' -- mas esse teste espelho NÃO EXISTIA (grep confirmou). Verifiquei numericamente que a alegação é de fato verdadeira para essa sequência específica (UCB final=0.237998, mínimo corrente desde t=60=0.048086, nunca cruza) e adicionei o teste espelho que faltava (test_regime_alternante_com_media_acumulada_zero_nao_cruza_o_limiar_hrms) travando esses valores. Suíte completa após todas essas correções: 503 passed, 1 skipped (502 anterior + 1 teste novo). py_compile e git diff --check limpos. Nenhum commit feito ainda.

2026-07-21 — RESOLUÇÃO DO P1 (semântica "pegajosa" do running-min em long_run_winner). Triangulação de 3 lentes cegas entre si concluída: Codex (review original, job k010troej), deep-reasoner e fable-reasoner (despachados em paralelo nesta sessão) CONVERGIRAM, de forma totalmente independente, no MESMO diagnóstico e na MESMA correção mínima. Nenhum divergiu.

Diagnóstico compartilhado: a garantia de Ville do HRMS é `P(para todo n: mu_n <= U_n) >= 1-alpha` -- cada `mu_n` é coberto pelo SEU PRÓPRIO `U_n`. Usar `running_min = min(trace[min_sessions-1:])` como se fosse cota do `mu_t` ATUAL só é válido se `mu` fosse constante no tempo (aí interseccionar UCBs de instantes diferentes é lícito); mas o módulo já afirma, corretamente, que `mu_t` é não-estacionário. Logo `running_min <= 0` prova apenas `exists i: mu_i < 0` (nulo existencial sobre a história), não `mu_t < 0` (o nulo operacional que `long_run_winner` deveria testar a cada reagregação diária do timer systemd). Contraexemplo determinístico (300 sessões delta=-0.10 + 300 de delta=+0.10, média final exatamente 0): UCB atual em t=600 = +0.071349 (corretamente não-significativa) mas running_min (herdado da fase 1) = -0.011149 -- sinal de promoção espúrio.

Correção aplicada em scripts/evaluate_p_dynamic_champions.py::_hrms_sequential_test: `rejects_null` agora é REATIVO -- `len(trace) >= min_sessions and trace[-1] <= 0.0` -- em vez do mínimo histórico. `running_min_upper_confidence_bound`/`first_rejection_session` permanecem no dict de retorno, mas rebaixados a diagnóstico histórico (nunca mais decidem `rejects_null`); grep confirmou que nenhum outro arquivo do repo consome esses dois campos esperando autoridade de promoção. Docstring da função reescrito explicando a distinção nulo-existencial-vs-nulo-atual e por que a interseção de UCBs exige alvo constante.

Verificação adicional que fiz (não pedida por nenhuma das 3 lentes, achado bônus): com a semântica reativa, testei numericamente se a 'contradição simétrica' apontada pelo fable-reasoner (A vencer-B e B vencer-A simultaneamente, via épocas históricas disjuntas -- possível sob o running_min sticky) ainda ocorre. Não ocorre mais: rodei 500 sessões de delta_AB aleatório (seed=42) e seu espelho delta_BA=-delta_AB através de _hrms_bound_trace; em NENHUM t as duas UCBs (trace_AB[t] e trace_BA[t]) ficam <=0 simultaneamente (contagem=0/500) -- consequência de bound(V) ser idêntico para AB/BA (residuais ao quadrado simétricos) e dos dois mu_hat serem exatamente opostos, o que torna as duas condições mutuamente exclusivas sob t compartilhado. A correção reativa resolve esse ponto como corolário, não só o contraexemplo principal.

Testes: novo teste de regressão `test_reversao_de_regime_com_media_final_zero_nao_deve_ser_promovida` em tests/test_hrms_sequential_test.py trava o contraexemplo (asserts trace[-1]=+0.071349, running_min=-0.011149 preservado como diagnóstico, rejects_null=False). Os dois testes de viés CONSTANTE pré-existentes (n=267 e n=1333) continuam passando SEM alteração de asserção -- confirmei numericamente que, nesses fixtures específicos, running_min e trace[-1] convergem (viés constante faz a UCB decair monotonicamente até o fim da janela testada), então a troca de semântica não muda o resultado deles. Suíte completa: 504 passed, 1 skipped (era 503 antes desta fatia). py_compile e git diff --check limpos.

Pontos levantados pelo fable-reasoner que ficam FORA do escopo desta correção (não implementados, registrados para decisão futura do usuário): (1) 'âncora eterna' -- mu_t pesa igualmente uma sessão de 2026 e uma de 2030 num ledger que cresce para sempre, o que dilui informação recente a taxa 1/t; a correção reativa resolve o bug agudo (sticky) mas não esse problema crônico de design. (2) Ausência de caminho de rebaixamento -- evaluate_champions só promove, nunca há monitor de degradação do campeão incumbente; combinado com HRMS reativo isso já não trava um 'vencedor' baseado em evidência revertida, mas também não ALERTA proativamente se o campeão atual degradar. fable-reasoner sugeriu como alternativa estrutural (não implementada, não decidida): tratar promote/demote como problema de monitoramento de mudança (e-detectors CUSUM/Shiryaev-Roberts, ou re-ancorar a confidence sequence por épocas reiniciadas a cada promoção). NENHUMA dessas duas questões estruturais foi implementada nesta fatia -- ambas requerem decisão de produto do usuário sobre se cabem no escopo do IRAI-18 ou viram item de backlog separado.

Despachei review /codex-r de follow-up desta correção específica (job k2nbpj5bu, request_id review-irai18-hrms-p1-sticky-fix-001) -- ainda em background, aguardando retorno antes de considerar esta fatia finalizada. Nenhum commit feito ainda.

REVISÃO DE FOLLOW-UP DO FIX DE rejects_null (job codex-r k2nbpj5bu / relay-mrv3uvan-6sihnq, request_id review-irai18-hrms-p1-sticky-fix-001) — RESULTADO E CORREÇÕES APLICADAS:

VEREDITO: Codex APROVOU o fix funcional (rejects_null reativo via trace[-1], gate por min_sessions) — nenhum P0/P1 novo encontrado. Confirmou que a correção resolve corretamente o bug do 'mínimo histórico pegajoso' sem reintroduzir peeking nem quebrar a garantia de Ville.

Dois achados P2 (documentação, não lógica) foram levantados e AMBOS já corrigidos nesta janela:

1) Inversão nulo/alternativa no docstring de _hrms_sequential_test (scripts/evaluate_p_dynamic_champions.py, ~linha 858): eu tinha escrito que o mínimo histórico 'testaria o nulo existencial exists n: mu_n<0'; o correto é que ele CONTROLA o nulo GLOBAL 'para todo n: mu_n>=0', e uma travessia é evidência PARA a alternativa existencial, não um teste do nulo existencial. Corrigido — texto agora reflete a direção certa da lógica de teste de hipótese.

2) Overclaim 'mu_t é exatamente zero em TODOS os pontos' na sequência adversarial regime-alternante (180 sessões -0,10 + 30 alternando ±0,80 + 20 de +0,90): Codex apontou corretamente que isso é falso para prefixos — a soma até t=180 (fim da fase constante) é -18,0 (média -0,10, não zero); só o agregado FINAL (t=230) é exatamente zero (verificado via statistics.fmean). Corrigido em DOIS lugares para manter consistência: docstring do módulo (scripts/evaluate_p_dynamic_champions.py, ~linhas 69-74) e docstring do teste espelho (tests/test_hrms_sequential_test.py::test_regime_alternante_com_media_acumulada_zero_nao_cruza_o_limiar_hrms, ~linhas 380-391). Nova explicação, numericamente verificada: o UCB mínimo do trecho considerado (min_sessions=60 em diante) ocorre EXATAMENTE em t=180 (fim da fase constante, antes dos swings começarem), valor ≈0,0481 — margem estreita mas positiva, porque o termo de boundary naquele ponto (≈26,64, dirigido pela variância empírica acumulada da fase constante) supera por pouco o déficit acumulado (S_180=-18,0). A não-travessia é atribuída à largura do UCB naquele caminho específico, não a uma alegação de mu_t=0 em todo prefixo. Nenhuma asserção de teste mudou (só docstrings) — valores numéricos (trace[-1]≈0,238, min(trace[59:])≈0,0481 em t=180) permanecem os mesmos.

Terceiro ponto de Codex (RISKS, não bug): a garantia de reavaliação diária depende do MESMO processo prefixal — ledger imutável, ordem estável, roster/alpha/params fixados antes dos dados; backfill/reordenação/retuning quebrariam essa garantia. Registrado como caveat de operação, não requer ação de código nesta fatia.

VALIDAÇÃO FINAL APÓS AMBAS AS CORREÇÕES: python3 -m py_compile limpo nos dois arquivos; git diff --check limpo; suíte completa `pytest tests/ -q` → 504 passed, 1 skipped (idêntico ao estado antes destas correções, como esperado — mudança é só de docstring).

Escopo explicitamente NÃO tratado nesta fatia (achados estruturais do fable-reasoner, aguardando decisão do usuário): (a) problema da 'âncora eterna' — mu_t pondera igualmente toda a história para sempre, diluindo informação recente a taxa 1/t; (b) ausência de caminho de rebaixamento — evaluate_champions só promove, nunca monitora degradação do campeão incumbente. Nenhum dos dois foi implementado.

Nenhum commit foi feito (aguardando pedido explícito do usuário, regra permanente da sessão).

SEGUNDA RODADA DE REVISÃO /codex-r (job kgfwk9zyy / relay-mrv5nlit-n1ehqq, request_id review-irai18-hrms-p1-docfix-followup-002; a primeira tentativa 001 falhou por erro de ambiente do worker — mktemp em /tmp read-only, sem análise real) — achou problemas REAIS na minha primeira tentativa de corrigir os dois P2 anteriores, NÃO aprovados:

1) A inversão nulo/alternativa foi confirmada corrigida corretamente (minímo histórico = nulo global / alternativa existencial). Mas o texto dizia 'alguma janela histórica' quando o domínio efetivo do running_min é sempre um PREFIXO com n>=min_sessions (trace[min_sessions-1:]) — corrigido para 'algum prefixo, com n>=min_sessions'.

2) Achado mais sério, verificado numericamente por mim antes de aceitar (rodando o código real, não confiando na alegação do revisor): eu tinha escrito que o UCB mínimo em t=180 vinha da 'variância empírica acumulada da fase constante' — FALSO. V_180 real = 0.01 (quase zero, porque mu_hat_{i-1} rastreia exatamente o delta constante -0.10 a partir de i=2, dando resíduo~0 a cada passo), ABAIXO do piso HRMS_V_MIN=0.06 (=DEFAULT_MIN_SESSIONS*1e-3=60*1e-3). Ou seja: o termo de boundary fica CONSTANTE (~26.6554) durante TODA a fase constante (confirmado: bound idêntico em t=1,2,10,60,90,120,150,180), não cresce com variância alguma — é exatamente o OPOSTO do que eu tinha escrito. O mecanismo real: UCB(t)=-0.10+26.6554/t decresce monotonicamente enquanto a fase dura, então o mínimo cai no MAIOR t da fase (t=180) simplesmente porque é o último ponto antes de V_t saltar (confirmado: salta de 0.01 para 0.50 já em t=181, primeiro salto ±0.80) e o boundary voltar a crescer, empurrando a UCB de volta para cima (0.147 em t=210, 0.238 em t=230). Também corrigida a normalização: a folga BRUTA do boundary sobre o déficit (26.6554-18=8.6554) não é pequena por si só — só fica estreita (~0.048) depois de dividida por t=180; o texto anterior confundia os dois.

Correção aplicada em AMBOS os locais (scripts/evaluate_p_dynamic_champions.py ~linhas 69-90 e tests/test_hrms_sequential_test.py ~linhas 380-398), com a explicação mecanicista completa e numericamente verificada (V_t travado no piso, boundary constante, UCB=-0.10+C/t monotônica, salto de V_t em t=181). Também corrigido um valor numérico impreciso que eu mesmo tinha introduzido antes desta rodada (~26,64 → ~26,6554, recomputado via _poly_stitching_bound/_hrms_bound_trace diretamente).

Validação: python3 -m py_compile limpo; git diff --check limpo; pytest tests/ -q → 504 passed, 1 skipped (inalterado, mudança só de docstring). Terceira rodada de /codex-r já despachada para confirmar que esta explicação mecanicista está correta e que não sobrou nenhum outro resquício da alegação incorreta. Nenhum commit feito ainda.

TERCEIRA RODADA /codex-r (job kwltbeh0t / relay-mrv5xh9b-y9vsjy, request_id review-irai18-hrms-p1-docfix-followup-003) — APROVADO. Veredito: 'o mecanismo descrito está correto, consistente e a normalização da folga está clara' para os dois docstrings (scripts/evaluate_p_dynamic_champions.py ~linhas 69-92 e tests/test_hrms_sequential_test.py ~linhas 380-400). Codex recomputou de forma independente TODOS os valores citados (V_1..V_180=0.01, HRMS_V_MIN=0.06, boundary constante=26.655403415631326, UCB(180)=0.048086, V_181=0.50, bound(181)=36.311835, UCB(210)=0.147010, UCB(230)=0.237998) e confirmou bate com o código real. Busca ampla não achou nenhum resquício ativo das duas alegações incorretas anteriores ('variância acumulada da fase constante' e 'janela histórica') nos dois arquivos -- a única ocorrência da alegação antiga é esta própria nota de backlog (histórico de auditoria, explicitamente marcada como corrigida, esperado). tests/test_hrms_sequential_test.py: 12 passed (rodado isoladamente pelo próprio Codex). Nenhum RISK acionável levantado (dois comentários são confirmações de que o texto já está certo, não achados nfoos).

CICLO DE REVISão DO FIX DE rejects_null (P1 sticky running-min) ENCERRADO nesta fatia: 3 rodadas de /codex-r consecutivas (aprovação funcional -> 2 achados P2 -> correcção rejeitada por imprecião mecanicista -> correcção final aprovada). Estado final: scripts/evaluate_p_dynamic_champions.py e tests/test_hrms_sequential_test.py com rejects_null reativo (trace[-1], gate min_sessions), running_min rebaixado a diagnóstico, docstrings numericamente verificados em 3 rodadas independentes de revisão. Suite completa (medida por mim, ambiente com /tmp gravável): pytest tests/ -q -> 504 passed, 1 skipped. py_compile e git diff --check limpos.

Pendente para o usuário decidir (fora do escopo desta fatia, não implementado): (a) problema da 'âncora eterna' de mu_t (ponderação igual da história toda); (b) ausência de caminho de rebaixamento do campeão incumbente em evaluate_champions. Nenhum commit feito -- aguardando pedido explícito do usuário.

Divergência encontrada: mensagem recebida sobre a revisão do job relay-mrvlhkzc-m3x2vt afirmava 'Registrei o resultado na tarefa IRAI-18, sem mudar código' — mas a tarefa não tinha nenhuma entrada sobre esse job (Updated ficava em 2026-07-21 21:30, sem menção ao job). Não aceitei a afirmação sem checar; esta nota registra o achado real pela primeira vez.

Achados P2 daquela revisão (verificados diretamente no código, não aceitos de bate-pronto): (1) `_empirical_bernstein_sequential_test` (scripts/evaluate_p_dynamic_champions.py) não expunha o pico histórico do e-process/WSR nem a sessão do primeiro cruzamento — só o valor final. (2) Não havia teste end-to-end em `evaluate_champions()` provando que `long_run_winner` promove e depois regride para None (só existia cobertura na função interna `_hrms_sequential_test`). (3) Os testes de Type-I do HRMS (horizon=300/alpha=0.05) dão taxa amostral zero — confirmam 'não rejeita demais' mas não têm poder para detectar uma calibração quebrada (ex.: HRMS_C errado por um fator), já que qualquer bug de escala também daria zero nesse regime.

Correções aplicadas (branch fix/irai-18-methodology-v2, ainda não commitadas): (1) scripts/evaluate_p_dynamic_champions.py::_empirical_bernstein_sequential_test ganhou `running_max_log_capital` (=max do trace) e `first_crossing_session` (1º índice 1-based com trace[i]>=log_threshold), estritamente diagnósticos — `rejects_null` continua baseado só em trace[-1]. (2) Novo teste tests/test_p_dynamic_champion_evaluator.py::test_long_run_winner_promove_e_depois_regride_para_none_end_to_end — 150 sessões favoráveis a 'cand' (long_run_winner='cand', UCB=-0.071558) seguidas de 20 desfavoráveis invertidas (long_run_winner volta a None, UCB=0.113853), com sequential_winner='cand' inalterado nas duas chamadas e running_min_upper_confidence_bound/first_rejection_session preservados (-0.071558 / sessão 106) — prova a independência dos dois campos e a persistência do diagnóstico histórico. (3) Novo teste tests/test_hrms_sequential_test.py::test_type_i_monte_carlo_em_regime_artificial_detecta_erro_de_escala — regime artificial (alpha=0.99, horizon=5000, N=5000 réplicas, seed=1234, ruído uniform(-0.95,0.95)) escolhido só por tratabilidade computacional (nenhuma combinação alpha<=0.9/horizon<=3000 gerou rejeição em 5000 réplicas na varredura feita), dá taxa=0.16% (8/5000) — sensível o bastante para detectar erro de escala em HRMS_C (mutação para 1 ou 4 muda a taxa para 17/5000 e 1/5000, verificado manualmente). (4) Teste complementar tests/test_empirical_bernstein_sequential_test.py::test_running_max_e_first_crossing_sao_diagnostico_historico_nao_sticky espelhando o teste de reversão do HRMS, para o lado WSR. Suíte completa: 507 passed, 1 skipped (504 prévios + 3 testes novos), py_compile e git diff --check limpos nos 4 arquivos.

Revisão /codex-r desta rodada (job relay-mrvo5bbr-7gtfv2, gpt-5.6-sol): VERDICT concorda com P2 #1 e #2 (lógica correta, testes resistem a mutação — ex.: trocar a decisão por máximo histórico quebra o teste novo; independência WSR/HRMS confirmada no wiring, linhas ~1047-1072). Discorda parcialmente do enquadramento de P2 #3: o teste em regime artificial (alpha=0.99) detecta erro de escala grosseiro mas não é evidência de calibração Type-I no regime de produção (alpha<=0.05/K, horizon 60-300) — é um golden test de regressão, não uma prova de cobertura. RISKS: (a) docstrings pré-existentes do módulo HRMS afirmavam taxa 'EXATAMENTE zero' nos regimes de produção, o que é overclaim (taxa amostral zero em N finito != taxa populacional zero — ex.: delta=-0.3 constante cruza HRMS na sessão 77, logo há vizinhança de probabilidade positiva sob uniform(-0.3,0.3) não observada em 5000 réplicas); (b) os dois arquivos de teste novos (test_hrms_sequential_test.py, test_empirical_bernstein_sequential_test.py) seguem untracked — a cobertura dos P2 #1/#3 só acompanha a mudança quando forem incluídos no commit. GAPS: evidência Type-I mais próxima de produção exigiria importance sampling/rare-event simulation com alpha=0.05/K e horizon=300, não feito nesta rodada.

Em resposta ao risk (a) acima: renomeei o teste para test_type_i_monte_carlo_em_regime_artificial_detecta_erro_de_escala e reescrevi seu docstring e o item 7 do docstring do módulo para não afirmar mais 'calibração comprovada' nem 'taxa EXATAMENTE zero' — agora deixa explícito que é um golden test de regressão em regime artificial (detecta erro de escala tipo HRMS_C errado por um fator), não prova de cobertura Type-I em produção, e que 'zero' é amostral (N=5000, seed fixa), não populacional. Suíte revalidada após o ajuste: 507 passed, 1 skipped. Nenhum commit foi feito (aguardando decisão do usuário). Achados P3 (report não preserva constantes efetivas da metodologia HRMS; campos arredondados podem divergir marginalmente da decisão interna; HRMS_V_MIN não acompanha --min-sessions; diagnósticos históricos usam nomes que podem confundir com a decisão atual; mais o overclaim de 'taxa exatamente zero' nos docstrings pré-existentes) seguem deliberadamente fora de escopo desta rodada, por prioridade explícita do usuário nos três P2.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Ledger diário champion-challenger do WIN implementado, publicado e validado no Ryzen. Preserva Miqueias/v1/v2, OHLC M5, Pair/NWE, GEX/MID e metadados; exige fechamento causal por fonte, usa offset sazonal e rejeita manifesto adulterado. Avaliador exige 60 sessões e IC95% pareado antes de promover vencedor. Re-review independente: GO; 210 passed, 17 skipped.
<!-- SECTION:FINAL_SUMMARY:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @codex
created: 2026-07-16 04:51
---
Implementação pronta para revisão. A primeira sessão fechada será capturada automaticamente hoje às 17:56 BRT; nenhuma promoção ocorrerá antes de 60 sessões comuns e IC95% favorável.
---

author: @codex
created: 2026-07-16 04:59
---
Revisão pós-implementação encontrou lacuna no AC #2: o bundle preserva win_open (abertura da sessão) e win_current (close), mas a API ainda não expõe abertura da barra, máxima e mínima. Sem OHLC por barra não é possível medir MFE/MAE nem simular alvo/stop. Tarefa reaberta para corrigir o contrato com regressão permanente.
---

created: 2026-07-16 05:06
---
Revisão independente (@claude) — foco: causalidade, fechamento de sessão, BRT/EEST, baseline, bootstrap.

VEREDITO: NO-GO. 2 defeitos reais e reproduzidos, ambos em scripts/compare_p_dynamic_parity.py; nenhum coberto pelos 16 testes existentes.

DEFEITO 1 (BRT/EEST, direção segura mas real) — main() usa brt_offset_h=6 hardcoded como default e só sobrescreve com o valor real (via document["brt_offset_h"], que a API local calcula corretamente com backend/irai/timezones.py::brt_to_tickmill_offset_hours) SE v1 ou v2 estiverem disponíveis no momento da captura. brt_to_tickmill_offset_hours é sazonal (6h no horário de verão americano, 5h fora dele — 2a Sáb de março a 1a Sáb de novembro). Fora dessa janela (~nov-mar), se v1/v2 falharem ou estiverem indisponíveis na captura, o fallback hardcoded=6 fica 1h errado. Reproduzido: uma sessão que fechou às 17:55 BRT real em 2026-01-15 (offset real=5) é classificada closed=False com o fallback (calcula last_operational_brt=16:55), e closed=True com o offset correto. Direção seguraa (nunca marca sessão aberta como fechada), mas derruba sessões válidas silenciosamente durante ~4 meses/ano sempre que a API local não responder na captura — justo quando o timer roda perto do fechamento (17:56 BRT).

DEFEITO 2 (fechamento de sessão, mais sério) — capture_session_status() em main() só recebe `reference` (série pública do Miqueias); nunca verifica se as séries LOCAIS v1/v2 (fonte do outcome real em evaluate_p_dynamic_champions.py::_actual_outcome) estão igualmente completas. Reproduzido: bundle com referência completa até 17:55 BRT (manifest closed=True) e v2 com dado só até 17:30 BRT (25min faltando) é aceito sem erro por load_ledger_sessions e o outcome (actual_up) é calculado com o último preço LOCAL conhecido (17:30), não o fechamento real da sessão. Isso contamina o ground-truth usado pra pontuar TODOS os modelos naquela sessão, não é uma vantagem de um candidato específico — mas viola a garantia causal que o resto do projeto mantém rigorosamente (achado X3 do NF-01 é sobre exatamente este tipo de problema, barra em formação vazando pro resultado).

Achado menor, fora do escopo pedido mas relevante pro AC #2 já reaberto: o payload público do Miqueias (curl direto no Firebase) já inclui win_bar_open/win_high/win_low por barra — a API local (/api/irai/series) ainda não expõe isso, então o gap de OHLC (MFE/MAE, alvo/stop) é assimétrico entre referência e séries locais.

Baseline (Beta(1,1) climatológico) e bootstrap (resample por sessão, IC95% "vence todos os oponentes") foram auditados e estão corretos — nenhum achado nessas duas frentes.

Reproduções rodadas localmente com os módulos reais do repo (não pseudocódigo), disponíveis sob pedido.
---

author: @codex
created: 2026-07-16 05:06
---
IRAI-18 pronto novamente para revisão independente após fechar a lacuna OHLC. Captura diária das 17:56 permanece habilitada.
---

author: @codex
created: 2026-07-16 05:07
---
NO-GO do reviewer aceito. Reabrindo para corrigir offset sazonal no fallback e exigir fechamento consistente das fontes locais usadas para outcome antes de selecionar o bundle.
---

author: @codex
created: 2026-07-16 05:11
---
Correções do NO-GO prontas para re-review: commits 81d11cf e 13334ef. Favor repetir as duas reproduções originais e tentar adulterar `manifest.session.closed=true`; esperado: janeiro usa +5, fonte local parcial bloqueia fechamento e loader rejeita manifesto adulterado.
---

created: 2026-07-16 05:16
---
Re-revisão independente (@claude) dos commits 81d11cf e 13334ef.

VEREDITO: GO nos 2 defeitos anteriores — ambos corrigidos e reproduzidos.

Reprodução 1 (offset sazonal): capture_brt_offset_h('2026-01-15', {}) sem
nenhum documento local disponível agora devolve 5 (via
brt_to_tickmill_offset_hours), não mais o fallback hardcoded=6. Confirmado
rodando o código real.

Reprodução 2 (fechamento cruzado): bundle com referência (Miqueias)
completa até 17:55 BRT mas v2 só até 17:30 BRT é REJEITADO por
load_ledger_sessions (ValueError "fontes sem fechamento operacional: v2"),
mesmo com manifest.session.closed=True gravado explicitamente no
manifest.json (adulteração artificial, exatamente como pedido) —
confirma que load_ledger_sessions recalcula o fechamento por fonte de
forma independente, não confia no valor armazenado no manifesto. Contei
com esse teste como o próprio caso de "altere manifest.session.closed
para true"; não há diferença de comportamento entre um manifesto
adulterado manualmente e um gerado com o bug antigo, porque a
verificação nova ignora completamente o campo armazenado.

Suíte: 19/19 testes em test_compare_p_dynamic_parity.py +
test_p_dynamic_champion_evaluator.py; 210 passed/17 skipped na suíte
completa (--ignore=tests/test_measure_tactical_gate3.py, sklearn ausente
neste ambiente). Nenhuma regressão.

Baseline e bootstrap não foram re-tocados por estes commits — meu veredito
anterior sobre eles (corretos) continua válido.
---
<!-- COMMENTS:END -->
