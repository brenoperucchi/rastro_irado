---
id: IRAI-12
title: VAL-05 — executar shadow live e reconciliar backtest com mercado
status: Blocked
assignee: []
created_date: '2026-07-15 22:49'
labels:
  - validation
  - operations
milestone: m-0
dependencies:
  - IRAI-6
  - IRAI-10
  - IRAI-11
priority: high
type: task
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Operar o pipeline completo sem ordens, registrando decisões e preços realmente disponíveis para medir o gap entre pesquisa e live.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Ledger registra eventos confirmados, bloqueados, invalidados e near-misses
- [ ] #2 Registra signal_available_at, preço disponível, slippage hipotético e resultado líquido
- [ ] #3 Compara distribuição de features, frequência, fills e expectativa backtest versus live
- [ ] #4 Nenhuma ordem é enviada e divergências relevantes bloqueiam ativação
<!-- AC:END -->
