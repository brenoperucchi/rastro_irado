---
id: IRAI-20
title: Coletar ticks WIN em terminal MT5 dedicado
status: In Progress
assignee:
  - '@codex'
created_date: '2026-07-16 06:20'
updated_date: '2026-07-16 14:12'
labels:
  - collection
  - mt5
  - execution
dependencies: []
references:
  - docs/plans/2026-07-13-irai-plano-consolidado.md
modified_files:
  - backend/workers/tick_collector_wsl.py
  - scripts/systemd/start-mt5-portable.ps1
  - scripts/systemd/win-ticks-wsl.sh
  - scripts/systemd/rastro-irado-win-ticks.service
  - tests/test_tick_collector.py
  - .gitignore
priority: high
type: feature
ordinal: 20000
---

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Terminal E:/MetaTradersWSL/wdowin/ira_ticks/terminal64.exe inicia obrigatoriamente com /portable e é validado antes da coleta
- [ ] #2 WIN$N e o contrato WIN vigente têm ticks bid/ask/last/volume/time_msc/flags capturados sem interferir no coletor M5
- [x] #3 Ticks são deduplicados e persistidos em Parquet particionado por data e símbolo com estado recuperável após restart
- [x] #4 Serviço systemd --user dedicado fica instalado, habilitado e monitorável por status/health
- [x] #5 Testes permanentes e validação no sshWSL são registrados
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Implementar primitivas testáveis de descoberta do contrato, deduplicação e cursor. 2. Persistir chunks Parquet atômicos e health/state JSON. 3. Criar launcher que inicia o terminal /portable antes do Python. 4. Instalar dependência analítica e unidade systemd --user. 5. Validar isolamento, conexão e persistência.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implementação autorizada explicitamente pelo usuário em 2026-07-16. Terminal dedicado confirmado existente e ocioso em E:/MetaTradersWSL/wdowin/ira_ticks/terminal64.exe.

Implementação local test-first concluída: descoberta do contrato vigente, cursor atômico por time_msc+identidade, chunks Parquet content-addressed, health JSON, conexão MT5 persistente entre ciclos e launcher PowerShell com /portable. Teste de conexão persistente falhou antes da correção (2 initialize por 2 ciclos) e passou após manter uma única sessão MT5.

O requisito de performance foi incorporado antes do deploy: polling continua em 2s para não perder mercado, mas a persistência acumula até 5 minutos ou 250 mil linhas e só então grava Parquet ZSTD atômico. Isso evita milhares de arquivos minúsculos e deixa o dataset adequado a DuckDB/Polars. O cursor persistido só avança após o flush; queda do processo reconsulta o buffer perdido.
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
<!-- COMMENTS:END -->
