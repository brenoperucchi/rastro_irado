---
id: IRAI-20
title: Coletar ticks WIN em terminal MT5 dedicado
status: In Progress
assignee:
  - '@codex'
created_date: '2026-07-16 06:20'
updated_date: '2026-07-16 06:25'
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
- [ ] #1 Terminal E:/MetaTradersWSL/wdowin/ira_ticks/terminal64.exe inicia obrigatoriamente com /portable e é validado antes da coleta
- [ ] #2 WIN$N e o contrato WIN vigente têm ticks bid/ask/last/volume/time_msc/flags capturados sem interferir no coletor M5
- [ ] #3 Ticks são deduplicados e persistidos em Parquet particionado por data e símbolo com estado recuperável após restart
- [ ] #4 Serviço systemd --user dedicado fica instalado, habilitado e monitorável por status/health
- [ ] #5 Testes permanentes e validação no sshWSL são registrados
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Implementar primitivas testáveis de descoberta do contrato, deduplicação e cursor. 2. Persistir chunks Parquet atômicos e health/state JSON. 3. Criar launcher que inicia o terminal /portable antes do Python. 4. Instalar dependência analítica e unidade systemd --user. 5. Validar isolamento, conexão e persistência.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implementação autorizada explicitamente pelo usuário em 2026-07-16. Terminal dedicado confirmado existente e ocioso em E:/MetaTradersWSL/wdowin/ira_ticks/terminal64.exe.

Implementação local test-first concluída: descoberta do contrato vigente, cursor atômico por time_msc+identidade, chunks Parquet content-addressed, health JSON, conexão MT5 persistente entre ciclos e launcher PowerShell com /portable. Teste de conexão persistente falhou antes da correção (2 initialize por 2 ciclos) e passou após manter uma única sessão MT5.
<!-- SECTION:NOTES:END -->
