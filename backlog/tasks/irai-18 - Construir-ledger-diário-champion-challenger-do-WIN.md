---
id: IRAI-18
title: Construir ledger diário champion-challenger do WIN
status: In Progress
assignee:
  - '@codex'
created_date: '2026-07-16 04:41'
updated_date: '2026-07-16 04:50'
labels:
  - validation
  - win
  - p-dynamic
  - gex
dependencies: []
references:
  - 'backlog://task/IRAI-17'
documentation:
  - docs/plans/2026-07-13-irai-plano-consolidado.md
modified_files:
  - scripts/compare_p_dynamic_parity.py
  - scripts/evaluate_p_dynamic_champions.py
  - tests/test_compare_p_dynamic_parity.py
  - tests/test_p_dynamic_champion_evaluator.py
  - scripts/systemd/rastro-irado-p-dynamic-ledger.service
  - scripts/systemd/rastro-irado-p-dynamic-ledger.timer
priority: high
type: feature
ordinal: 18000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Preservar, por sessão e de forma reproduzível, os dados necessários para comparar P Dinâmico do Miqueias, IRAI v1/v2 e versões futuras sem depender do Firebase corrente. O bundle deve reunir as séries de P, WIN M5 e sinais locais disponíveis, além do snapshot GEX/MID, e alimentar um avaliador que não declare vencedor abaixo do gate mínimo de amostra.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Cada captura preserva séries brutas de Miqueias, v1 e v2, metadados de origem e timestamp da coleta
- [ ] #2 O bundle preserva WIN OHLC e campos Pair/NWE presentes nas séries locais, além do snapshot GEX/MID disponível para a sessão
- [ ] #3 O avaliador calcula métricas de qualidade probabilística somente em barras operacionais e sessões fechadas
- [ ] #4 O relatório distingue avaliação do objetivo diário do P da utilidade econômica como gate tático
- [ ] #5 Abaixo do gate mínimo de sessões o resultado é INCONCLUSIVO e nenhum quality_winner é promovido
- [ ] #6 Testes permanentes cobrem montagem do bundle, sessão incompleta, ausência de GEX e gate de amostra
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Auditar contratos API/Firebase/GEX e definir schema versionado do ledger.
2. Especificar por testes a captura atômica e os gates de sessão/amostra.
3. Implementar captura completa reutilizando o comparador existente.
4. Implementar avaliação champion-challenger para o objetivo diário, mantendo o gate tático separado.
5. Executar no Ryzen, publicar e registrar limitações.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Auditoria no Ryzen5WSL: `/api/irai/series` já expõe P, WIN, Pair, NWE, VWAP e ATR por barra; `/api/irai/gex` expõe gamma max/flip/min, walls e `mid_wall` separadamente. Banco de produção: WIN M5 tem 138.646 barras desde 2021-07-12, mas `gex_levels` possui apenas 2 datas (2026-07-10..2026-07-13). O ledger precisa começar imediatamente e o avaliador deve bloquear qualquer vencedor abaixo do gate.

Implementação local concluída: bundle versionado e atômico preserva documentos brutos Miqueias/v1/v2, manifesto de fechamento BRT, GEX/walls/mid_wall e relatório de paridade. Avaliador agrega Brier/log-loss dentro da sessão, inclui baseline climatológico causal Beta(1,1), exige 60 sessões comuns e bootstrap pareado IC95% contra todos os concorrentes; o gate tático permanece NOT_EVALUATED. Timer diário proposto para 17:56 BRT, somente leitura das APIs.
<!-- SECTION:NOTES:END -->
