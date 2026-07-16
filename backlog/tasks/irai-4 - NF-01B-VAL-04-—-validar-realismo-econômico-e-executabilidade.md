---
id: IRAI-4
title: NF-01B / VAL-04 — validar realismo econômico e executabilidade
status: In Progress
assignee:
  - '@codex'
created_date: '2026-07-15 22:48'
updated_date: '2026-07-16 14:24'
labels:
  - tactical
  - validation
milestone: m-0
dependencies:
  - IRAI-3
references:
  - docs/plans/2026-07-13-irai-tactical-layer-win-wdo.md
modified_files:
  - scripts/measure_pair_signal_value.py
  - scripts/build_nf01_artifact.py
  - scripts/measure_rollover_sensitivity.py
  - tests/test_measure_pair_signal_value.py
  - tests/test_build_nf01_artifact.py
  - tests/test_measure_rollover_sensitivity.py
  - docs/artifacts/irai-4/README.md
  - docs/artifacts/irai-4/nf01_executable_pit.json.gz
  - docs/artifacts/irai-4/nf01_executable_pit_summary.json
  - docs/artifacts/irai-4/win-rollover-sensitivity-executable-v1.json
  - docs/plans/2026-07-13-irai-tactical-layer-win-wdo.md
priority: high
type: task
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Completar a aceitação econômica do NF-01 sem misturar pesquisa exploratória com promoção de produção.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Registra observation_bar_end, confirmation_bar_end, signal_available_at, entry_at e entry_price
- [x] #2 Fill usa o primeiro preço realmente negociável após a disponibilidade do sinal
- [ ] #3 Compara Pair dinâmico, Pair fixo e baselines simples com frequência comparável
- [x] #4 Reporta custos em 0,5x, 1,0x, 1,5x e 2,0x do cenário principal
- [ ] #5 Separa hipótese confirmatória, análises condicionais e achados exploratórios
- [x] #6 Resultado fica provisório enquanto rollover contínuo não estiver auditado
- [ ] #7 Avalia regra local com e sem gate IRAI, reportando expectativa, drawdown, cobertura, maus trades evitados e bons trades perdidos
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Incorporar a revisão dupla como endurecimento do braço executável antes do challenger. 2. Adicionar regressões que falhem no comportamento atual para gap temporal intra-sessão e cooldown após sinal sem open executável; cobrir OHLC incompleto no caminho e todos os campos da sensibilidade de custo. 3. Definir que todo sinal elegível consome cooldown mesmo quando o fill não pode ser provado, evitando substituir oportunisticamente um evento não executável por outro próximo. 4. Documentar o viés de seleção de OHLC ausente e corrigir comentários que tratam signal_available_at==entry_at como universal. 5. Executar teste estreito, suíte mantida completa, registrar evidências e publicar antes de o Claude iniciar o Pair fixo.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Iniciado após GO da revisão independente IRAI-3. O contrato de 4 timestamps + entry_price já foi entregue no IRAI-2 (AC1). A sensibilidade de rollover WIN foi concluída no IRAI-5; o WDO permanece pendente. Próxima fatia: primeiro preço executável, MFE/MAE OHLC e cenários de custo, sem promover hipótese exploratória.

Hardening pós-review: sinais sem open executável consomem cooldown; relatório data_quality expõe descartes por open e MFE/MAE incompleto. A ausência de OHLC é documentada como fonte possível de viés de seleção.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @codex
created: 2026-07-16 14:12
---
Braço executável PIT concluído no commit 8b9ac12 e artefato gerado no Windows/Ryzen: 18.005 eventos, entrada no open da M5 imediatamente posterior à confirmação, horizontes por barras completas, MFE/MAE por OHLC e custos 0,5x/1x/1,5x/2x. Resultado: WIN Pair sem edge positivo mesmo a 0,5x custo; WDO Pair significativamente negativo em todos os horizontes inclusive a 0,5x; Z/interseção não promovíveis. Sensibilidade WIN excluiu 7,06% e tornou h3 significativamente negativo, portanto rollover não escondia edge positivo. Tarefa permanece aberta para AC3 (Pair fixo/frequência comparável), AC5 e AC7.
---

author: @codex
created: 2026-07-16 14:20
---
Revisão independente deep+fable recebida: GO no núcleo de c7722ba/8b9ac12. Item E aceito: ausência de OHLC é dado potencialmente não aleatório e o sinal rejeitado não consumia cooldown. Decisão de implementação: o sinal elegível consumirá cooldown antes da validação do fill; se não houver open, nenhum trade é medido, mas um sinal próximo não poderá substituí-lo. É a política mais conservadora e fiel à cadência da estratégia.
---

author: @codex
created: 2026-07-16 14:24
---
Regressão permanente test_sinal_sem_open_executavel_ainda_consume_cooldown falhou antes da correção: o segundo sinal era aceito quando faltava open no primeiro. Após mover o consumo do cooldown para antes da validação do fill, passou. Acrescentados testes para gap intra-sessão (signal_available_at < entry_at), OHLC parcial com fwd preservado/MFE-MAE ausentes, contrato de média aritmética e CI/significância/win-rate dos quatro custos. Validação: 60 testes NF-01 relacionados passaram; suíte mantida pytest -q tests --ignore=tests/test_measure_tactical_gate3.py => 254 passed, 18 skipped. pytest -q global continua inadequado no Linux por coletar scripts/archive dependentes de MT5 e sklearn ausente.
---
<!-- COMMENTS:END -->
