---
id: IRAI-22.1
title: 'GEX live: exibir último snapshot válido com proveniência'
status: Done
assignee:
  - '@codex'
created_date: '2026-07-21 14:06'
updated_date: '2026-07-21 14:17'
labels:
  - gex
  - frontend
  - api
dependencies: []
parent_task_id: IRAI-22
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
No modo live, quando o cálculo GEX mais recente for inválido ou não plotável, exibir o último snapshot PIT válido dentro da janela de frescor. A interface deve declarar explicitamente a proveniência e nunca alterar o histórico selecionado pelo usuário.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Live usa último snapshot PIT válido e fresco quando o cálculo atual é inválido
- [x] #2 Resposta expõe data efetiva, fonte EOD e condição de fallback
- [x] #3 Histórico por date permanece estrito, sem fallback
- [x] #4 Frontend identifica visualmente o nível como último válido
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implementado fallback live para o último snapshot PIT válido e fresco, com provenance no endpoint e rótulo explícito no frontend. Regressões: tests/test_api_gex_endpoint.py::test_get_gex_live_invalido_usa_ultimo_snapshot_pit_valido_e_fresco (falhava antes); tests/test_gex_frontend_contract.py::test_gex_live_fallback_identifica_o_ultimo_snapshot_valido. Validações: pytest -q tests/test_gex_worker.py tests/test_api_gex_endpoint.py tests/test_gex_frontend_contract.py (79 passed); frontend npm run build.

Revisão independente Sol High: dois achados corrigidos e revalidados. (1) fallback exige source_session_date < effective_session_date <= hoje; (2) fallback_reason diferencia invalid/stale/without_walls/missing e o tooltip usa a causa real. Regressões adicionais: test_get_gex_live_fallback_recusa_snapshot_pit_com_fonte_futura; test_get_gex_live_fallback_explica_snapshot_live_envelhecido. Validação final: pytest -q tests (481 passed, 1 skipped); npm run build; git diff --check. Verificação direta da base atual: active=true, fallback=true, as_of=2026-07-20, source_as_of=2026-07-17, live_valid=false, 36 walls.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Modo live mantém o último snapshot PIT GEX válido e fresco quando o cálculo mais recente não é plotável. A resposta e a interface declaram o fallback e sua proveniência; consultas históricas continuam estritas. Revisado independentemente e validado.
<!-- SECTION:FINAL_SUMMARY:END -->
