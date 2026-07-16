---
id: IRAI-4
title: NF-01B / VAL-04 — validar realismo econômico e executabilidade
status: In Progress
assignee:
  - '@codex'
created_date: '2026-07-15 22:48'
updated_date: '2026-07-16 14:12'
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
  - tests/test_pair_signal_value.py
  - tests/test_nf01_artifact.py
  - tests/test_rollover_sensitivity.py
  - docs/artifacts/irai-4/README.md
  - docs/artifacts/irai-4/nf01_executable_pit.json.gz
  - docs/artifacts/irai-4/nf01_executable_pit_summary.json
  - docs/artifacts/irai-4/win-rollover-sensitivity-executable-v1.json
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Iniciado após GO da revisão independente IRAI-3. O contrato de 4 timestamps + entry_price já foi entregue no IRAI-2 (AC1). A sensibilidade de rollover WIN foi concluída no IRAI-5; o WDO permanece pendente. Próxima fatia: primeiro preço executável, MFE/MAE OHLC e cenários de custo, sem promover hipótese exploratória.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @codex
created: 2026-07-16 14:12
---
Braço executável PIT concluído no commit 8b9ac12 e artefato gerado no Windows/Ryzen: 18.005 eventos, entrada no open da M5 imediatamente posterior à confirmação, horizontes por barras completas, MFE/MAE por OHLC e custos 0,5x/1x/1,5x/2x. Resultado: WIN Pair sem edge positivo mesmo a 0,5x custo; WDO Pair significativamente negativo em todos os horizontes inclusive a 0,5x; Z/interseção não promovíveis. Sensibilidade WIN excluiu 7,06% e tornou h3 significativamente negativo, portanto rollover não escondia edge positivo. Tarefa permanece aberta para AC3 (Pair fixo/frequência comparável), AC5 e AC7.
---
<!-- COMMENTS:END -->
