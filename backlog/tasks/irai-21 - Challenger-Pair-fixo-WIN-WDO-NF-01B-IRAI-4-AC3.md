---
id: IRAI-21
title: Challenger Pair fixo WIN-WDO (NF-01B / IRAI-4 AC#3)
status: Review
assignee:
  - '@claude'
created_date: '2026-07-16 15:04'
updated_date: '2026-07-16 15:18'
labels:
  - tactical
  - validation
  - challenger
dependencies: []
priority: high
ordinal: 21000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Executa o challenger Pair FIXO WIN-WDO como contraste ao Pair dinâmico (par escolhido pelo Kalman). Independente do engine/calibração (não sofre C1-a): lê WIN e WDO do market_bars, beta OLS rolling, mesma entrada executável (open da barra seguinte) e custos do Pair dinâmico. Comparação bruta e com frequência equivalente contra Pair dinâmico e baselines momentum/reversão. Metodologia congelada ANTES dos resultados. Artefato separado. Referencia IRAI-4 AC#3 (que fica com o codex no braço executável).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Metodologia congelada e commitada antes de qualquer resultado
- [x] #2 Par fixo WIN-WDO usa a mesma entrada executável e custos do Pair dinâmico
- [x] #3 Compara challenger vs Pair dinâmico vs baselines, bruta e com frequência equivalente
- [x] #4 Challenger é causal (sem lookahead) e reproduzível, com testes permanentes
- [x] #5 Artefato JSON separado versionado + comando/hash/limitações
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Challenger Pair fixo WIN-WDO concluído.

METODOLOGIA CONGELADA ANTES DOS RESULTADOS: docs/plans/2026-07-16-challenger-
pair-fixo-win-wdo.md, commit 06f20ba (antes de qualquer execução). Só ajustada
depois no eixo temporal (deslocamento +offset, ainda antes dos resultados) e na
ressalva de janela; §6 (resultados) adicionada após rodar.

IMPLEMENTAÇÃO (sem tocar em measure_pair_signal_value.py, que o codex edita no
IRAI-4): scripts/measure_pair_fixed_value.py força o par WIN<->WDO, computado
INDEPENDENTE do engine/Kalman/calibração (lê market_bars, beta OLS rolling 20 sem
intercepto, reusa zscore.py inalterado). Mesma entrada executável (open da barra
seguinte), custos, MFE/MAE OHLC e 4 timestamps causais do dinâmico via
extract_trade_outcomes; agregação via run() injetado por patch local.

COMPARAÇÃO (scripts/build_challenger_artifact.py): challenger vs pair dinâmico +
baselines do artefato irai-4. Bruta (retorno médio/evento) + frequência
equivalente (expectativa por sessão = média/evento × eventos/sessão).

RESULTADO (h=6, líquido de custo, produção --limit 2000):
  WIN$N: pair_fixo -10.89*** (exp/sessão -43.24) vs pair dinâmico -7.47
         (-30.04); momentum -12.39***, reversao -7.61.
  WDO$N: pair_fixo -0.84*** (-3.36) vs pair -1.00*** (-4.18); momentum
         -0.90***, reversao -1.10***.
TODOS negativos em ambos os alvos. Fixar o par NÃO recupera edge — em WIN$N é
mais negativo que o dinâmico, em WDO$N parecidos e adversos. Regra simples não
vence a complexa: as duas perdem. Controle limpo (sem C1-a) que reforça o achado
central do NF-01.

ARTEFATO SEPARADO: docs/artifacts/irai-21/ (summary legível + completo gzipado
com 10464 eventos + README). git.commit == origin_main == 5b67100 (localizável).
Comando/hash/parâmetros/limitações no artefato.

RESSALVA registrada: janelas de medição não idênticas (challenger toda a base
~1250 sessões; dinâmico PIT ~880 de 2022-12+). Expectativa por sessão normaliza a
frequência, não a janela temporal.

TESTES: 270 passed, 18 skipped. Novos: tests/test_measure_pair_fixed_value.py
(beta OLS, alinhamento WIN-WDO, marker edge-triggered causal, timestamp
deslocado, integração run_fixed) e tests/test_build_challenger_artifact.py
(comparação bruta+por-sessão, metadata, ressalva de janela). Commits: 06f20ba,
5b67100, 25fba06.
<!-- SECTION:NOTES:END -->
