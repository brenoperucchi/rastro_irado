---
id: IRAI-17
title: Comparar e avaliar o P Dinâmico do WIN
status: In Progress
assignee:
  - '@codex'
created_date: '2026-07-16 04:15'
updated_date: '2026-07-16 04:20'
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
- [ ] #1 A ferramenta lê a série pública WIN_N sem depender de código privado do Miqueias
- [ ] #2 A ferramenta alinha barras por instante respeitando o contrato de timezone e permite comparar v1 e v2 locais
- [ ] #3 O relatório de paridade apresenta cobertura, correlação, MAE, diferença máxima, concordância de regime 40/60 e primeiro ponto de divergência
- [ ] #4 Testes permanentes cobrem alinhamento, seleção do campo público e métricas de paridade
- [ ] #5 Uma execução real ou uma limitação ambiental objetiva fica registrada com comando reproduzível
- [ ] #6 O resultado distingue explicitamente proximidade entre curvas de qualidade preditiva; nenhuma versão é promovida apenas por semelhança
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
<!-- SECTION:NOTES:END -->
