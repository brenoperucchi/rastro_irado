---
id: IRAI-5
title: Auditar rollover e construção das séries contínuas WIN/WDO
status: In Progress
assignee:
  - '@codex'
created_date: '2026-07-15 22:48'
updated_date: '2026-07-16 05:39'
labels:
  - validation
  - operations
milestone: m-0
dependencies: []
references:
  - docs/plans/2026-07-13-irai-plano-consolidado.md
modified_files:
  - scripts/audit_continuous_rollover.py
  - tests/test_audit_continuous_rollover.py
  - docs/artifacts/irai-5/win-rollover-audit-v1.json
priority: high
type: spike
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Determinar no MT5 se as séries $N são ajustadas, concatenadas cruas ou tratadas de outra forma e medir impacto nos eventos e retornos.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Método de construção de WIN$N e WDO$N é verificado no ambiente Windows/MT5
- [ ] #2 Datas de rollover relevantes são identificadas no histórico do NF-01
- [ ] #3 Sensibilidade dos resultados com e sem janelas de rollover é reportada
- [ ] #4 Gate econômico deixa de carregar status provisório somente após esta auditoria
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Verificar no MT5/XP as propriedades e o histórico de WIN$N e contratos individuais. 2. Identificar trocas de contrato e descontinuidades no banco usado pelo NF-01. 3. Classificar a série como ajustada, concatenada crua ou outro método. 4. Criar auditor reproduzível e medir sensibilidade dos eventos/retornos excluindo janelas de rollover. 5. Começar por WIN; WDO permanece segunda perna do mesmo gate.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Trabalho iniciado em paralelo ao fechamento do IRAI-2 pelo Claude. A primeira fatia é WIN$N, coerente com o piloto atual; nenhuma conclusão do NF-01 será promovida antes deste gate.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @codex
created: 2026-07-16 05:39
---
Fatia WIN executada no ambiente Windows/MT5 e reproduzida por auditor. O symbol_info de WIN$N no terminal XP descreve: 'IBOVESPA MINI - Por Liquidez (WINQ26) - Sem Ajustes' (path BMF\SERIES CONTINUAS\WIN$N, expiration_time=0), classificando a série como contínua por liquidez sem back-adjustment. O banco de produção contém 1251 sessões M5 de 2021-07-12 a 2026-07-15. Nos 30 vencimentos WIN observados, 30/30 gaps são positivos; mediana 2267,5 pts versus mediana absoluta geral 340 pts; 25/30 excedem o p95 geral (1498,25 pts). Auditor e spec: scripts/audit_continuous_rollover.py e tests/test_audit_continuous_rollover.py; artefato: docs/artifacts/irai-5/win-rollover-audit-v1.json. Commit 4e7cc8a, push origin/main e pull/pytest no Ryzen concluídos. ACs permanecem abertos: WDO ainda não auditado e a sensibilidade econômica aguarda o ledger versionado do IRAI-2.
---
<!-- COMMENTS:END -->
