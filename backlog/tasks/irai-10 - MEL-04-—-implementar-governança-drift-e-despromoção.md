---
id: IRAI-10
title: 'MEL-04 — implementar governança, drift e despromoção'
status: Blocked
assignee: []
created_date: '2026-07-15 22:49'
labels:
  - tactical
  - operations
milestone: m-0
dependencies:
  - IRAI-9
priority: high
type: enhancement
ordinal: 10000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Tornar aprovação reversível e bloquear runtime quando regra/modelo perder validade estatística ou operacional.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Artefato aprovado possui validade, owner, cutoff, schema hash e política de revisão
- [ ] #2 Drift de features, cobertura, custos e desempenho live são monitorados
- [ ] #3 Critérios determinísticos devolvem approved para experimental e Tactical para NAO_OPERAR
- [ ] #4 Despromoção e recuperação ficam auditadas e testadas
<!-- AC:END -->
