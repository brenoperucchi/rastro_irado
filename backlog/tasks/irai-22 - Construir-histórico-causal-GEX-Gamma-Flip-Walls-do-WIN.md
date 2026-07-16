---
id: IRAI-22
title: Construir histórico causal GEX/Gamma Flip/Walls do WIN
status: Review
assignee:
  - '@codex'
created_date: '2026-07-16 15:12'
updated_date: '2026-07-16 17:23'
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
- [x] #1 O pipeline gera níveis históricos de WIN com source_session_date, effective_session_date, Gamma Max/Min, Gamma Flip, Walls/MID e flags explícitas de validade/proveniência
- [x] #2 Cada sessão efetiva usa somente arquivos oficiais B3 fechados no pregão anterior, sem reutilizar informação futura
- [x] #3 Backfill é idempotente, não sobrescreve silenciosamente dados válidos e reporta sessões aceitas, rejeitadas e motivos
- [x] #4 Cobertura e qualidade do histórico são auditadas por sessão antes de autorizar backtest da regra manual
- [x] #5 Testes permanentes e comando reproduzível no Windows/Ryzen são registrados
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

author: codex
created: 2026-07-16 15:53
---
INCIDENTE/RECUPERAÇÃO: a primeira ampliação foi iniciada com python3 Linux sobre o SQLite hospedado em /mnt/c enquanto os serviços escreviam via Python Windows. O locking/WAL cruzado corrompeu a imagem. O job foi interrompido e API/collector parados. Preservada cópia forense data/backups/irai_corrupt_20260716_1245.db (SHA256 e259c649...), backup 10/07 validado, e recuperação  criada em arquivo novo. A recuperada passou quick_check no Linux e no Windows, preservando 3.570.922 market_bars até 2026-07-16T18:40:00Z e 23 gex_levels; foi promovida atomicamente, mantendo original forense e recuperada validada em backups. API retornou 200 e collector voltou a inserir barras. Correção permanente: ensure_safe_sqlite_runtime recusa Linux/WSL sobre DrvFS e instrui Python Windows (commits 46496b2/26bb645). Teste falhou antes, agora 60 testes relacionados passam no Windows. Backfill-100 reiniciado como unit systemd usando py.exe 3.12 Windows.
---

author: codex
created: 2026-07-16 16:44
---
Ampliação final Ryzen/Windows: 200 sessões-fonte de 2025-09-24 a 2026-07-15, 73 válidas e 127 inválidas. Proveniência completa em 200/200 (effective_session_date, quatro hashes B3 e validity_reasons); quick_check=ok. Rejeições: 75 flip fora dos extremos, 31 também distante demais do spot, 50 sem flip. Cobertura mensal é regime-dependente: 2025-09 5/5, 10 6/23, 11 11/19, 12 5/20; 2026-01 14/21, 02 18/18, 03 0/22, 04 10/20, 05 0/20, 06 0/21, 07 4/11. Relatório: data/gex_history_200_audit.json; comando reproduzível usa py.exe 3.12 Windows com --limit 200 --audit-only. Validação Windows: 61 testes relacionados; suíte local mantida 292 passed, 18 skipped. API, collector M5 e tick collector ativos.
---

created: 2026-07-16 16:51
---
Bug LIVE reproduzido em 2026-07-16: o backfill histórico reutilizava gex_levels, tabela consumida pela API LIVE, e substituiu a linha inválida calculada pelo worker em 2026-07-15 por uma reconstrução histórica válida. Regressão permanente adicionada: test_backfill_historico_nunca_sobrescreve_tabela_gex_live falhou inicialmente por ausência de save_history_result. Correção em andamento separa a persistência histórica em gex_history_levels; nenhuma reconstrução poderá escrever no LIVE.
---

created: 2026-07-16 16:56
---
Correção LIVE concluída (commits 1d5452e/eb76c80). Causa: o backfill gravava em gex_levels e contaminava a fonte consumida pela API. Implementado gex_history_levels exclusivo, save_history_result e migração transacional identificada por meta.source_files. Regressões: teste novo falhou inicialmente na importação de save_history_result; testes finais 55 passed local e 55 passed no Python 3.12 Windows/Ryzen. Produção: backup consistente irai_pre_gex_live_fix_20260716_135413.db (quick_check=ok); 200 linhas movidas, 73 válidas, proveniência 200/200, quick_check=ok. Worker oficial reexecutado: WIN 2026-07-15 GammaMax=182497, Flip=186421, Min=171874, valid=false; API active=false. API e collector ativos.
---

created: 2026-07-16 17:04
---
Frontend follow-up: botão GEX agora usa disabled={!gex.active}; o payload é reconsultado a cada 60s e uma invalidação desliga GEX/MID localmente. Regressões novas em tests/test_gex_frontend_contract.py falharam antes e passam após correção. Validação: 57 testes GEX/backfill, npm run build. npm run lint segue com 20 erros preexistentes fora deste patch.
---

created: 2026-07-16 17:23
---
Gate GEX corrigido após comparação com MagicGEX/Miqueias. Causa: valid exigia GammaMax > GammaFlip > GammaMin, mas Flip é zero do acumulado e Max/Min são extremos pontuais; não há essa invariante. Regressão test-first falhou com snapshot líquido/próximo e Flip fora dos extremos, depois passou. Commits a13a547/7f12db0. Produção: WIN 15/07 agora valid=true e API active=true (Min 171874, Max 182497, Flip 186421); API/collector ativos. Histórico reclassificado com backup quick_check=ok: 200 sessões, 73→119 válidas, 46 promovidas, 0 rebaixadas; 81 inválidas (50 sem Flip, 31 Flip distante). Auditoria gex_history_200_audit.json regenerada. Validação Windows: 60 passed.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Pipeline histórico causal GEX do WIN implementado e validado. Usa arquivos oficiais B3 EOD de D somente na próxima sessão WIN, Selic causal, hashes/proveniência, persistência idempotente e auditoria sem recomputação. A base produtiva agora possui 200 sessões homogêneas e 73 GEX válidos, suficiente para iniciar a contagem exploratória de eventos da regra manual, ainda sem promoção econômica.
<!-- SECTION:FINAL_SUMMARY:END -->
