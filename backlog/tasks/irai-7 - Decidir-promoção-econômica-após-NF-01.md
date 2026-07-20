---
id: IRAI-7
title: Decidir promoção econômica após NF-01
status: Blocked
assignee: []
created_date: '2026-07-15 22:49'
labels:
  - tactical
  - validation
milestone: m-0
dependencies:
  - IRAI-4
  - IRAI-5
priority: high
type: task
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Aplicar o gate de produto que escolhe entre parar a hipótese, promover regra transparente ou autorizar NF-02.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Decisão usa somente análise confirmatória OOS líquida de custos e baselines
- [ ] #2 Sem edge: P/Z permanecem diagnósticos e Tactical retorna NAO_OPERAR
- [ ] #3 Regra simples aprovada: NF-02 é pulado e NF-03 usa a regra transparente
- [ ] #4 NF-02 só é autorizado quando houver hipótese econômica pré-registrada para ganho incremental
- [ ] #5 Decisão e evidências são registradas no plano e no Backlog
<!-- AC:END -->
