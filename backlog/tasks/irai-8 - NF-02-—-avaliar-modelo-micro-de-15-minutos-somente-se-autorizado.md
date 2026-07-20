---
id: IRAI-8
title: NF-02 — avaliar modelo micro de 15 minutos somente se autorizado
status: Blocked
assignee: []
created_date: '2026-07-15 22:49'
labels:
  - tactical
  - research
milestone: m-0
dependencies:
  - IRAI-7
priority: medium
type: feature
ordinal: 8000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Treinar e validar modelo micro apenas se o gate econômico registrar hipótese incremental que a regra simples não resolve.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Hipótese, features e métrica incremental são pré-registradas
- [ ] #2 Modelo supera regra transparente e baselines no OOS líquido
- [ ] #3 Artefato JSON é reproduzível, versionado e recusado quando incompatível
- [ ] #4 Modelo reprovado permanece experimental e não desbloqueia NF-03
<!-- AC:END -->
