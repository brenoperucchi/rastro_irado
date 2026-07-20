---
id: IRAI-9
title: NF-03 — implementar máquina de estados para hipótese aprovada
status: Blocked
assignee: []
created_date: '2026-07-15 22:49'
labels:
  - tactical
milestone: m-0
dependencies:
  - IRAI-7
priority: high
type: feature
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implementar estados, eventos, histerese, cooldown e idempotência somente para regra/modelo aprovado pelo gate.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 NAO_OPERAR tem precedência para qualidade ou aprovação insuficiente
- [ ] #2 Somente barra fechada pode avançar estado e persistir evento
- [ ] #3 CONFIRMADO exige distorção, região e reação aprovadas
- [ ] #4 Sequência completa, invalidação, cooldown e idempotência possuem regressões permanentes
<!-- AC:END -->
