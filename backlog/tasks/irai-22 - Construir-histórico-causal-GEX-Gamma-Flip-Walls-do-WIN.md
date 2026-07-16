---
id: IRAI-22
title: Construir histórico causal GEX/Gamma Flip/Walls do WIN
status: In Progress
assignee:
  - '@codex'
created_date: '2026-07-16 15:12'
updated_date: '2026-07-16 15:43'
labels:
  - gex
  - validation
  - backfill
dependencies: []
references:
  - docs/plans/2026-07-16-regra-manual-miqueias-win.md
  - docs/plans/2026-07-13-irai-plano-consolidado.md
modified_files:
  - .gitignore
  - scripts/backfill_gex_history.py
  - tests/test_backfill_gex_history.py
priority: high
type: feature
ordinal: 22000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Disponibilizar histórico diário point-in-time suficiente dos níveis GEX do WIN para que regras de pullback em GEX/MID possam ser avaliadas sem lookahead. O banco atual contém somente três sessões e não sustenta comparação estatística.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 O pipeline gera níveis históricos de WIN com source_session_date, effective_session_date, Gamma Max/Min, Gamma Flip, Walls/MID e flags explícitas de validade/proveniência
- [ ] #2 Cada sessão efetiva usa somente arquivos oficiais B3 fechados no pregão anterior, sem reutilizar informação futura
- [ ] #3 Backfill é idempotente, não sobrescreve silenciosamente dados válidos e reporta sessões aceitas, rejeitadas e motivos
- [ ] #4 Cobertura e qualidade do histórico são auditadas por sessão antes de autorizar backtest da regra manual
- [ ] #5 Testes permanentes e comando reproduzível no Windows/Ryzen são registrados
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Usar exclusivamente os arquivos históricos oficiais da B3 por pregão: preço/posição por série, prêmio de referência de opções e índice IBOV; não depender de símbolos expirados do MT5. 2. Criar parsers streaming e testes com fixtures mínimas para OI IBOV, metadados/prêmio, fechamento IBOV e escolha causal do contrato WIN mais líquido. 3. Montar o input de `compute_gex` sem alterar sua fórmula, registrando hashes/arquivos, source_session_date e effective_session_date (próximo pregão WIN); EOD de D só pode valer em D+1. 4. Implementar backfill idempotente com cache local, política explícita de skip/replace e relatório por data. 5. Rodar testes locais, publicar, executar no Windows/Ryzen sobre uma janela piloto e então ampliar conforme cobertura/qualidade observada; auditar válidos, inválidos e causas antes de liberar qualquer backtest da regra manual.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Regressão test-first: `tests/test_backfill_gex_history.py` falhou inicialmente na coleta com `ModuleNotFoundError: scripts.backfill_gex_history`. Após a implementação, 11 testes passaram. Suíte GEX relacionada: 46 passed, 8 skipped.

Prova real read-only em 2025-07-10: bundle oficial B3 recuperou SPRE (1.044.395 bytes), SPRD (69.195), PE (1.355.636) e IR (23.180); 506 séries IBOV com OI casaram com 506 prêmios, IBOV=136.743,26, WINQ25 escolhido por 4.282.620 negócios com ajuste 138.319 e Selic SGS 1178=14,90%. O cálculo produziu 74 strikes/10 líquidos, mas sem Gamma Flip, logo sessão invalidada corretamente. Isso demonstra parser e causalidade sem transformar ausência de flip em dado válido.

Limitação registrada no artefato: a B3 pode republicar arquivos antigos. O pipeline grava hash e retrieved_at da vintage hoje baixada, mas não afirma que ela seja byte a byte a publicação original de D+1.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: codex
created: 2026-07-16 15:43
---
Piloto Ryzen concluído. A chamada inicial com --db data/irai_live.db reproduziu um risco real: get_connection criou SQLite vazio; regressão permanente adicionada e corrigida por open_backfill_database (commit c0ee381). Suíte relacionada após correção/auditoria: 50 passed, 8 skipped local e 58 passed no Ryzen. Backfill real de 20 sessões: 4 válidas/16 inválidas, sem erros; 16 inserts inválidos, 1 válido, 1 promoção invalid->valid e 2 válidos legados preservados. Auditoria explícita adicionada (commit 95a5fc5): 13 gamma_flip_not_between_extrema, 9 gamma_flip_too_far_from_spot, 3 missing_gamma_flip. Proveniência causal completa (D -> próxima sessão WIN, quatro hashes B3) em 18/20; os dois snapshots legados válidos foram deliberadamente preservados. Janela de 100 sessões em execução no Ryzen; downloads são pré-carregados em paralelo, persistência segue serial/idempotente.
---
<!-- COMMENTS:END -->
