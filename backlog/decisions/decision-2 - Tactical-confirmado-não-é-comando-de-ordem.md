---
id: decision-2
title: Tactical confirmado não é comando de ordem
date: '2026-07-15 22:50'
status: accepted
---
## Context

O Tactical transforma observações causais em estados explicáveis. Existe interesse futuro
em consumir esses estados por um EA no MT5, mas execução automática permanece fora da v1.


## Decision

`CONFIRMADO` significa hipótese tática aprovada e vigente; não autoriza uma ordem. Um futuro
Execution Layer separado deverá transformar o evento em intenção, aplicar risco, validar
conta/símbolo/freshness/idempotência e somente então interagir com o broker.


## Consequences

- O backend Tactical nunca envia ordens diretamente.
- O EA futuro nasce em shadow mode e com `EnableTrading=false`.
- VAL-05 precisa ser aprovado antes de planejar execução real.
