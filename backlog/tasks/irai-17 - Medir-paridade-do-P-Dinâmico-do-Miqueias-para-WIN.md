---
id: IRAI-17
title: Comparar e avaliar o P Dinâmico do WIN
status: Review
assignee:
  - '@codex'
created_date: '2026-07-16 04:15'
updated_date: '2026-07-16 04:31'
labels:
  - validation
  - win
  - p-dynamic
dependencies: []
references:
  - 'https://rastromacro.web.app/'
  - 'https://rastromacro-default-rtdb.firebaseio.com/series/WIN_N.json'
documentation:
  - docs/plans/2026-07-13-irai-plano-consolidado.md
modified_files:
  - scripts/compare_p_dynamic_parity.py
  - tests/test_compare_p_dynamic_parity.py
  - .gitignore
  - docs/plans/2026-07-13-irai-plano-consolidado.md
priority: high
type: spike
ordinal: 17000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Construir uma comparação reproduzível de caixa-preta entre a série pública do P Dinâmico do WIN no Rastro Macro do Miqueias e as séries locais IRAI v1/v2. Paridade é diagnóstico, não critério de promoção: o objetivo final é identificar qual versão é mais útil para a análise do WIN por evidência fora da amostra. Se o Miqueias vencer, buscar seus parâmetros; se uma versão local ou futura vencer, adotá-la como nova referência.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A ferramenta lê a série pública WIN_N sem depender de código privado do Miqueias
- [x] #2 A ferramenta alinha barras por instante respeitando o contrato de timezone e permite comparar v1 e v2 locais
- [x] #3 O relatório de paridade apresenta cobertura, correlação, MAE, diferença máxima, concordância de regime 40/60 e primeiro ponto de divergência
- [x] #4 Testes permanentes cobrem alinhamento, seleção do campo público e métricas de paridade
- [x] #5 Uma execução real ou uma limitação ambiental objetiva fica registrada com comando reproduzível
- [x] #6 O resultado distingue explicitamente proximidade entre curvas de qualidade preditiva; nenhuma versão é promovida apenas por semelhança
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Concluir o coletor/comparador de paridade e iniciar captura versionada da série pública.
2. Comparar Miqueias, v1 e v2 nas mesmas barras e separar pré-mercado de barras operacionais.
3. Quando houver outcomes comuns, avaliar direção de fechamento com Brier/log-loss/AUC/calibração por horário e estabilidade OOS.
4. Avaliar separadamente a utilidade do P como gate da regra manual, líquida de custos; não confundir com o objetivo diário do P.
5. Promover a versão somente por desempenho OOS e registrar limitações de amostra.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Pesquisa concluída: o bundle público seleciona `p_up_v1` quando presente e cai para `p_up`; o Firebase público expõe `/series/WIN_N.json`. A API local fornece `/api/irai/series?...&version=v1|v2`, com timestamps no eixo Tickmill e `brt_offset_h` para reconstrução BRT. O comparador alinhará ISO timestamps exatamente e distinguirá todas as barras do subconjunto operacional sem ghost/preview.

Execução real no Ryzen5WSL em 2026-07-16 04:27 UTC, após push/pull, contra Firebase público + API de produção local. Foram alinhadas 90/90 barras de pré-mercado. Miqueias versus v1/v2: correlação -0,596071; MAE 4,300889 pp; diferença máxima 12,83 pp; primeira divergência já em 00:00 do eixo Tickmill. v1 e v2 ficaram exatamente empatados porque ainda não havia barra real do WIN; 0 barras operacionais, portanto nenhum vencedor de qualidade pode ser declarado. A captura agora preserva Miqueias/v1/v2 e marca `quality_winner=null`. Próximo dado necessário: captura após o fechamento e acumulação de sessões intocadas para Brier/log-loss/AUC/calibração; a utilidade como gate tático será medida separadamente e líquida de custos.

Validação executada: `pytest -q tests/test_compare_p_dynamic_parity.py` → 9 passed localmente e 9 passed no Ryzen5WSL; `python3 -m py_compile scripts/compare_p_dynamic_parity.py`; execução produtiva via `python3 -X utf8 scripts/compare_p_dynamic_parity.py --local-api http://localhost:8888 --capture-dir data/p_dynamic_parity --output-json data/p_dynamic_parity/latest.json`. Commits publicados e aplicados no WSL: `9f4631a`, `8ffb7b0`, `06f2f73`.

Captura operacional de fechamento agendada no Ryzen5WSL com timer de usuário `irai-p-dynamic-parity-close-20260716.timer` para 2026-07-16 17:56 BRT (AccuracySec=30s), antes da virada do eixo Tickmill. O serviço executará o mesmo comparador e atualizará `data/p_dynamic_parity/latest.json`, mantendo o bundle bruto Miqueias/v1/v2.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @codex
created: 2026-07-16 04:29
---
Implementação pronta para revisão. `quality_winner` permanece intencionalmente nulo: a captura disponível tinha 0 barras operacionais e não autoriza escolher Miqueias, v1 ou v2.
---
<!-- COMMENTS:END -->
