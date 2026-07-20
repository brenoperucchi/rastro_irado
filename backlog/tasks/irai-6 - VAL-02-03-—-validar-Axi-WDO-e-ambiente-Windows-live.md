---
id: IRAI-6
title: 'VAL-02/03 — validar Axi, WDO e ambiente Windows live'
status: Backlog
assignee: []
created_date: '2026-07-15 22:48'
labels:
  - validation
  - operations
milestone: m-0
dependencies: []
references:
  - docs/plans/2026-07-13-irai-plano-consolidado.md
priority: high
type: task
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fechar os bloqueios de ambiente ainda abertos antes de qualquer ativação do WDO ou declaração de paridade live.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Relógio Axi é medido contra fonte conhecida e incorporado à geometria temporal se necessário
- [ ] #2 Cesta, pesos, versão, pair_factor e comportamento do WDO são conferidos em produção
- [ ] #3 Replay/live final roda no Windows com terminais MT5 reais
- [ ] #4 WIN é validado antes do WDO e nenhum resultado live é alegado a partir do Linux
<!-- AC:END -->
