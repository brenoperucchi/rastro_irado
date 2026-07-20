---
id: IRAI-13
title: Planejar Execution Layer MT5 após shadow aprovado
status: Blocked
assignee: []
created_date: '2026-07-15 22:49'
labels:
  - future
  - operations
milestone: m-1
dependencies:
  - IRAI-12
priority: low
type: docs
ordinal: 13000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Criar projeto separado para transportar intenção tática ao MT5, aplicar política de execução/risco e reconciliar ordens, sem ampliar o escopo atual.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Tactical continua autoridade do estado e nunca envia ordem diretamente
- [ ] #2 Bridge define arquivo/API local, versionamento, freshness, idempotência e symbol mapping
- [ ] #3 EA nasce com EnableTrading=false e account/server allowlist
- [ ] #4 Risk Layer, order lifecycle, kill switch e reconciliação são especificados separadamente
<!-- AC:END -->
