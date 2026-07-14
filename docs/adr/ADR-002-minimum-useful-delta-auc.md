# ADR 002: Limiar mínimo de ΔAUC (+0,02) para o macro ter valor tático

## Contexto
O Gate 3b (`scripts/measure_tactical_gate3.py`, commit `b93cbfe`) precisava de um
critério objetivo para decidir se acrescentar features do `P_up` macro (nível, Δ no
horizonte, persistência, divergência preço-vs-P_up) a um modelo aninhado de momentum
próprio produz ganho *tático* relevante, e não apenas estatisticamente não-nulo. A
constante `MINIMUM_USEFUL_DELTA_AUC = 0.02` (`scripts/measure_tactical_gate3.py:60`)
foi introduzida nesse commit — **antes** do walk-forward ancorado (`b7e7a37`,
2026-07-14) que efetivamente decidiu o veredito "o macro não agrega valor tático em
h=3/h=6" usando esse mesmo limiar.

O `/tri-review` de 2026-07-14 sobre os commits do walk-forward (achado A#4,
relatório em `~/.claude/jobs/b05b2f48/tmp/tri-review-irai-nwe.md`) verificou:
- **Pré-registro confirmado.** `git log --all -S "MINIMUM_USEFUL_DELTA_AUC" --
  scripts/measure_tactical_gate3.py` retorna só `b93cbfe`, que precede `b7e7a37`. O
  limiar não foi ajustado depois de ver o resultado — a inferência de equivalência é
  metodologicamente legítima quanto a *timing*.
- **Nunca derivado de custo/PnL real — e o próprio commit de origem já registra
  isso.** `TARGET_COST_POINTS = {"WIN$N": 10.0, "WDO$N": 1.0}`
  (`scripts/measure_tactical_gate3.py:61`) existe no mesmo módulo, mas alimenta
  apenas o rótulo do modelo multinomial (`multinomial_label`,
  `fit_residualized_multinomial`) — uma mecânica de *label*, não a derivação do
  limiar de decisão do gate. A mensagem do commit `b93cbfe` é explícita: **"o limiar
  ΔAUC=0,02 foi inventado por nós. Sem regra de execução, sizing e payoff, AUC não
  determina break-even após custos — a pergunta certa pode ser de PnL, não de
  AUC."** Não é um valor de bibliografia — é um número ad hoc, com a ressalva de
  método já registrada por quem o escreveu.
- **É decisivo no braço mais próximo de zero.** No walk-forward final (`aggregate.json`,
  8 folds, 673/674 sessões, ver `docs/plans/2026-07-13-irai-plano-consolidado.md`
  §3.7), o único ponto positivo (WIN$N v1 h=3, ΔAUC=+0,0067) fica bem abaixo do
  limiar, mas o teto do IC95% de vários braços chega perto de 0,02 — o veredito de
  "sem valor tático" depende de o limiar estar calibrado corretamente, não só do
  sinal do ponto estimado.

## Decisão
Manter `MINIMUM_USEFUL_DELTA_AUC = 0.02` como o limiar operacional vigente do Gate 3
e de qualquer walk-forward futuro que o reutilize, **registrando explicitamente que
é uma heurística pré-registrada, não uma derivação de custo real** — para que uma
revisão futura não confunda "pré-registrado" com "calibrado economicamente" (são
propriedades independentes: a primeira protege contra p-hacking de timing, a
segunda garante que o limiar tem o valor certo).

Não elevar nem reduzir o valor sem antes fazer a derivação pendente abaixo — mudar o
número agora, sem essa base, trocaria uma heurística não fundamentada por outra.

## Trabalho pendente (não bloqueia o veredito atual, mas deveria preceder o próximo uso do gate)
`ΔAUC` sozinho não determina break-even após custos — a ressalva do `b93cbfe` está
certa: sem uma regra de execução, sizing e payoff definidos, não existe uma conversão
única de "ΔAUC" para "R$ ou pontos esperados". Derivar um limiar economicamente
fundamentado exige, nesta ordem:
1. **Definir a regra de execução candidata primeiro** (que sinal dispara entrada,
   que stop/alvo, que horizonte de saída) — sem isso, "ganho esperado por sessão"
   não é uma quantidade bem definida a partir de AUC isolado.
2. Só então simular PnL líquido de custo (`TARGET_COST_POINTS`: 10 pts WIN$N, 1 pt
   WDO$N) sob essa regra, comparando o braço com e sem as features de `P_up`, em vez
   de inferir o ganho a partir da AUC via uma fórmula fechada.
3. Tratar a pergunta como podendo ser de **PnL, não de AUC** (nas palavras do
   commit de origem) — ou seja, considerar seriamente que o critério de decisão
   correto para a Etapa 3 pode não ser "ΔAUC > X" e sim um teste de PnL/Sharpe OOS
   diretamente, tornando `MINIMUM_USEFUL_DELTA_AUC` um filtro estatístico prévio
   (barato, sem precisar de regra de execução), não o critério econômico final.
4. Comparar o que sair do passo 2/3 com o `0,02` atual; documentar a diferença (se
   houver) e só então decidir se o número — ou o próprio critério — muda.

## Consequências
- O veredito "o macro não agrega valor tático em h=3/h=6" (plano consolidado §3.7)
  continua válido sob o limiar pré-registrado atual — não é revertido por este ADR.
- Qualquer novo gate estatístico do Tactical Layer (NF-02/NF-03) que reutilize
  `MINIMUM_USEFUL_DELTA_AUC` deve citar este ADR e não pode apresentar o número como
  "custo real medido" até o trabalho pendente acima ser feito.
- Se a derivação de custo real (passos 1-4) produzir um limiar bem diferente de
  `0,02`, os gates já fechados com o valor atual (Gate 3b, walk-forward de
  `b7e7a37`) precisam ser reavaliados sob o novo número antes de reabrir a discussão
  de incluir o macro como feature tática.
