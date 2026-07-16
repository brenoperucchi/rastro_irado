---
id: IRAI-21
title: Challenger Pair fixo WIN-WDO (NF-01B / IRAI-4 AC#3)
status: In Progress
assignee:
  - '@claude'
created_date: '2026-07-16 15:04'
updated_date: '2026-07-16 15:04'
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
- [ ] #1 Metodologia congelada e commitada antes de qualquer resultado
- [ ] #2 Par fixo WIN-WDO usa a mesma entrada executável e custos do Pair dinâmico
- [ ] #3 Compara challenger vs Pair dinâmico vs baselines, bruta e com frequência equivalente
- [ ] #4 Challenger é causal (sem lookahead) e reproduzível, com testes permanentes
- [ ] #5 Artefato JSON separado versionado + comando/hash/limitações
<!-- AC:END -->
