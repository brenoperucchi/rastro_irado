---
id: IRAI-5
title: Auditar rollover e construção das séries contínuas WIN/WDO
status: In Progress
assignee:
  - '@codex'
created_date: '2026-07-15 22:48'
updated_date: '2026-07-16 05:31'
labels:
  - validation
  - operations
milestone: m-0
dependencies: []
references:
  - docs/plans/2026-07-13-irai-plano-consolidado.md
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
