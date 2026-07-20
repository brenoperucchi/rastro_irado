---
id: IRAI-11
title: NF-04 — distribuir Tactical por API/Firebase/UI com flag desligada
status: Blocked
assignee: []
created_date: '2026-07-15 22:49'
labels:
  - tactical
milestone: m-0
dependencies:
  - IRAI-9
priority: medium
type: feature
ordinal: 11000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Expor estados aprovados sem ativação prematura e sem recomputar regra quantitativa nos clientes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Contrato é aditivo, versionado e inclui reason_codes, região, invalidação e bar_closed
- [ ] #2 Cache inclui strategy_version e mantém invalidação por notify_update
- [ ] #3 API local, Firebase e UI possuem regressão de paridade
- [ ] #4 Feature flag permanece desligada até gates histórico e Windows/live
<!-- AC:END -->
