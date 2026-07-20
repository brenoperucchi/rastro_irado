---
id: IRAI-1
title: Documentar gates econômicos e roadmap causal do Tactical
status: Review
assignee: []
created_date: '2026-07-15 22:48'
updated_date: '2026-07-15 22:53'
labels:
  - tactical
  - validation
milestone: m-0
dependencies: []
references:
  - docs/plans/2026-07-13-irai-plano-consolidado.md
  - docs/plans/2026-07-13-irai-tactical-layer-win-wdo.md
modified_files:
  - docs/plans/2026-07-13-irai-plano-consolidado.md
  - docs/plans/2026-07-13-irai-tactical-layer-win-wdo.md
  - docs/plans/README.md
  - AGENTS.md
  - CLAUDE.md
  - backlog.config.yml
priority: high
type: docs
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Atualizar o plano consolidado e a especificação Tactical para preservar a definição honesta do produto, as saídas legítimas do gate, NF-01B/VAL-04, shadow live, governança e a fronteira futura com MT5.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Plano consolidado registra NF-01A, NF-01B/VAL-04 e as três saídas do gate econômico
- [x] #2 Tactical define instante executável, baselines, custos, múltiplos testes e governança
- [x] #3 Shadow live e fronteira futura com o EA ficam documentados sem entrar na v1
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Editar primeiro o plano consolidado, depois a especificação normativa Tactical; revisar consistência e manter execução automática fora do escopo da v1.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Validação: git diff --check; busca de consistência por NF-01B/VAL-04/shadow/governança/Execution Layer; backlog config list; codex mcp list; claude mcp list.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Planos atualizados com definição honesta do produto, gate econômico em três rotas, executabilidade, custos, baselines, múltiplos testes, IRAI como filtro, governança reversível, shadow live e fronteira futura com MT5. Backlog.md inicializado e semeado com milestones, tarefas e dependências.
<!-- SECTION:FINAL_SUMMARY:END -->
