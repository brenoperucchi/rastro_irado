---
id: IRAI-20
title: Coletar ticks WIN em terminal MT5 dedicado
status: Done
assignee:
  - '@codex'
created_date: '2026-07-16 06:20'
updated_date: '2026-07-16 15:10'
labels:
  - collection
  - mt5
  - execution
dependencies: []
references:
  - docs/plans/2026-07-13-irai-plano-consolidado.md
modified_files:
  - .gitignore
  - backend/workers/tick_collector_wsl.py
  - scripts/systemd/start-mt5-portable.ps1
  - scripts/systemd/win-ticks-wsl.sh
  - scripts/systemd/rastro-irado-win-ticks.service
  - tests/test_tick_collector.py
priority: high
type: feature
ordinal: 20000
---

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Terminal E:/MetaTradersWSL/wdowin/ira_ticks/terminal64.exe inicia obrigatoriamente com /portable e é validado antes da coleta
- [x] #2 WIN$N e o contrato WIN vigente têm ticks bid/ask/last/volume/time_msc/flags capturados sem interferir no coletor M5
- [x] #3 Ticks são deduplicados e persistidos em Parquet particionado por data e símbolo com estado recuperável após restart
- [x] #4 Serviço systemd --user dedicado fica instalado, habilitado e monitorável por status/health
- [x] #5 Testes permanentes e validação no sshWSL são registrados
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Reproduzir o zero-tick em sessão aberta e comparar terminal dedicado versus terminal XP principal: terminal_info, account_info, symbol_info, symbol_info_tick, copy_ticks_from/range e last_error. 2. Identificar se a causa é login/feed/Market Watch, faixa temporal/UTC ou contrato, sem interromper o collector M5 além do estritamente necessário. 3. Antes da correção, adicionar regressão permanente para o comportamento observável incorreto quando aplicável (inclusive health não pode declarar ok indefinidamente sem ticks durante sessão). 4. Corrigir launcher/coletor/configuração no menor escopo, validar WIN e WINQ26 com ticks reais e Parquet, reiniciar serviço. 5. Confirmar que o collector M5 continua ativo e registrar comandos/evidências no backlog.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implementação autorizada explicitamente pelo usuário em 2026-07-16. Terminal dedicado confirmado existente e ocioso em E:/MetaTradersWSL/wdowin/ira_ticks/terminal64.exe.

Implementação local test-first concluída: descoberta do contrato vigente, cursor atômico por time_msc+identidade, chunks Parquet content-addressed, health JSON, conexão MT5 persistente entre ciclos e launcher PowerShell com /portable. Teste de conexão persistente falhou antes da correção (2 initialize por 2 ciclos) e passou após manter uma única sessão MT5.

O requisito de performance foi incorporado antes do deploy: polling continua em 2s para não perder mercado, mas a persistência acumula até 5 minutos ou 250 mil linhas e só então grava Parquet ZSTD atômico. Isso evita milhares de arquivos minúsculos e deixa o dataset adequado a DuckDB/Polars. O cursor persistido só avança após o flush; queda do processo reconsulta o buffer perdido.

Causa raiz confirmada em produção: o feed XP codifica timestamps de tick no relógio local BRT como epoch. Às 15:00 UTC, o último tick aparece como 12:00 UTC bruto. A consulta UTC de 15 minutos retornava 0; a mesma janela deslocada -3h retornou 71.477 ticks. A persistência mantém o epoch bruto para paridade com market_bars BR e registra explicitamente a semântica no health.

Validação objetiva final no Windows/Ryzen em sessão B3 aberta: execução `--once` gravou 67.371 ticks de WIN$N e 70.071 de WINQ26 em dois arquivos Parquet particionados por data/símbolo. Schema verificado: symbol, time, time_msc, bid, ask, last, volume, flags, volume_real, collected_at. `state.json` persistiu cursores por símbolo; após reinício, o serviço aceitou somente ticks novos. `rastro-irado-win-ticks.service` e `rastro-irado-collector.service` permaneceram active e o serviço de ticks está enabled. Windows: `pytest -q tests/test_tick_collector.py` => 10 passed.

Risco operacional observado: `systemctl stop` sobre o processo Python Windows via WSL não entrega o sinal Python de encerramento, portanto o buffer corrente não faz flush imediato. O desenho recuperável mantém o cursor durável atrás do buffer e reconsulta esse intervalo no próximo start, evitando perda lógica; o flush normal continua a cada 5 minutos/250 mil linhas. Endurecimento de shutdown pode ser tratado separadamente, sem invalidar os ACs.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @codex
created: 2026-07-16 06:33
---
Deploy no Ryzen5WSL concluído em 2026-07-16 03:31 BRT. Runtime Windows Python 3.12 recebeu pyarrow 25.0.0. Validações: pytest -q tests/test_tick_collector.py => 8 passed no Windows; coleta --once conectou ao terminal dedicado, validou terminal_data_path=E:\MetaTradersWSL\wdowin\ira_ticks, descobriu WINQ26 e encerrou limpa; rastro-irado-win-ticks.service foi linked/enabled/started e está active/running; rastro-irado-collector.service permaneceu active/running. Health status=ok para WIN$N e WINQ26. Como a B3 está fechada, received/written=0 é esperado; AC #2 e o fechamento da tarefa aguardam o primeiro tick real da próxima sessão, sem backfill massivo artificial.
---

author: @codex
created: 2026-07-16 14:12
---
Verificação em sessão B3 aberta em 2026-07-16 ~10:06 BRT: serviço permaneceu active/running e ciclos reportaram status ok, porém received/written/buffered=0 tanto para WIN quanto WINQ26. Não há Parquet porque nenhum tick chegou ao Python. O problema está antes da persistência, provavelmente login/feed/Market Watch do terminal dedicado; AC2 permanece aberto e exige diagnóstico no MT5 Windows.
---

author: @codex
created: 2026-07-16 15:00
---
Diagnóstico retomado por autorização do usuário após restart do stack. Em sessão B3 aberta, serviço dedicado permanece active, porém received/accepted/written/buffered=0; health atual ainda informa status=ok. Próxima ação é comparar diretamente a sessão MT5 dedicada com o terminal XP principal.
---

author: @codex
created: 2026-07-16 15:05
---
Regressões permanentes adicionadas antes da correção: test_consulta_ticks_no_relogio_brt_codificado_pelo_broker_xp e test_health_fica_degraded_sem_ticks_durante_pregao_b3 falharam no código anterior. Após tornar o offset do servidor configurável (-3h) e consultar no eixo bruto XP, 9 testes passaram e 1 foi ignorado localmente (pyarrow). O health agora não declara ok durante B3 aberta quando received=0.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implementado e validado o coletor tick a tick dedicado do WIN no terminal MT5 portátil. A causa do feed zerado era a semântica temporal da XP: os ticks são codificados no relógio BRT como epoch; a consulta passou a usar offset configurável de -3h e o health agora degrada corretamente quando não chegam ticks durante o pregão. O pipeline descobre o contrato vigente, deduplica por identidade de tick, persiste Parquet ZSTD particionado e mantém cursor recuperável. Regressões permanentes falharam antes da correção e passaram depois. Validação real: 67.371 ticks WIN$N + 70.071 WINQ26 persistidos, schema/estado inspecionados, 10 testes no Windows e coexistência comprovada com o coletor M5.
<!-- SECTION:FINAL_SUMMARY:END -->
