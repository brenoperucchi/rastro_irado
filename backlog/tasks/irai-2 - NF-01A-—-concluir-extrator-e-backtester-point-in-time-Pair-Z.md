---
id: IRAI-2
title: NF-01A — concluir extrator e backtester point-in-time Pair/Z
status: In Progress
assignee:
  - '@claude'
created_date: '2026-07-15 22:48'
updated_date: '2026-07-16 05:28'
labels:
  - tactical
  - validation
milestone: m-0
dependencies: []
references:
  - docs/plans/2026-07-13-irai-tactical-layer-win-wdo.md
priority: high
type: feature
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Entregar o núcleo causal e reproduzível do backtester de distorções Pair/Z, compartilhável por pesquisa, replay e runtime. Trabalho atualmente em execução pelo Claude.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Extrator usa apenas dados disponíveis no fechamento determinístico de cada barra
- [ ] #2 Replay não persiste estado no banco de produção nem contamina o estado Kalman live
- [ ] #3 Pair, Z e baselines previstos geram eventos reproduzíveis por sessão
- [ ] #4 Comando, testes executados, artefato e limitações conhecidas são reportados
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Concluir a implementação já em andamento, testes permanentes, comando reproduzível e artefato de exemplo; não ampliar para NF-02/NF-03.
<!-- SECTION:PLAN:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @codex
created: 2026-07-16 01:08
---
Revisão parcial do item 1 (Pair isolado), commit 496f739, enquanto o item 2 segue em execução. A suíte específica passou (14 testes) e o recorte correlato passou (53 passed, 4 skipped). Antes de encerrar NF-01A, revisar na primitiva compartilhada: (1) registrar observation_bar_end, confirmation_bar_end, signal_available_at e entry_at; (2) explicitar que o fill atual é o fechamento da próxima M5, não o primeiro preço executável, e comparar/sensibilizar essa política; (3) substituir UTC-5 fixo da quebra BRT pelo offset sazonal documentado; (4) tratar os *** como exploratórios devido a múltiplos horizontes/direções correlacionados; (5) não afirmar que C1-a torna necessariamente o edge negativo conservador, pois a direção do viés não foi demonstrada; (6) guardar comando e JSON reproduzível. O achado econômico provisório continua válido como alerta: Pair não deve ser promovido; WIN sem evidência e WDO com padrão adverso sob a política de entrada/custo testada.
---

author: @codex
created: 2026-07-16 03:49
---
Revisão incremental dos commits 13ac01c, fbe5469 e 002b614. Entregue: definição pré-registrada da interseção Pair∩Z; replay expandido (~5 anos); gate mínimo de 100 eventos; ressalva de múltiplos testes; estabilidade anual descritiva; infraestrutura de calibração point-in-time com cesta fixa e target_div_sigma as-of. Validação local: 81 passed, 4 skipped no recorte NF-01/markers/timezone. Em 2026-07-16 a primeira execução PIT ainda estava ativa no Windows/WSL, portanto infraestrutura commitada não equivale ainda a resultado PIT registrado. Permanecem antes de encerrar NF-01A/VAL-04: quatro timestamps causais por evento; política de fill/primeiro preço negociável (atual = close da próxima M5); offset BRT sazonal na quebra horária (atual = UTC-5 fixo); artefatos JSON versionados ou identificados por hash/comando; custos/baselines e rollover. A visão executiva para o Miqueias também precisará substituir 'Pair sem edge no WIN' pelo resultado expandido/PIT definitivo quando a rodada terminar.
---

author: @codex
created: 2026-07-16 05:28
---
Gate de prontidão antes de abrir IRAI-3 (auditoria em 2026-07-16): NF-01A ainda NÃO está entregável. Evidências: (1) scripts dizem explicitamente que não implementam baselines momentum/reversão, mas AC #3 os exige; (2) não existe artefato JSON NF-01 versionado/localizável no repo — os JSONs reportados ficaram em scratchpad /tmp; (3) TradeOutcome não registra observation_bar_end, confirmation_bar_end, signal_available_at e entry_at; (4) _hour_brt ainda subtrai 5h aproximado em vez do helper sazonal; (5) entry_price continua sendo close da próxima M5 e precisa ser rotulado como política hipotética, deixando primeiro preço executável para VAL-04; (6) MFE/MAE usa apenas closes apesar de OHLC agora existir. Para fechar IRAI-2 sem invadir VAL-04: corrigir relógio e contrato temporal, gerar baselines como eventos reproduzíveis, publicar 1 artefato PIT com comando/hash/limitações, manter fill e MFE/MAE atuais explicitamente como provisórios. Fill executável, custos completos e MFE/MAE OHLC ficam em IRAI-4. Marcar ACs e mover para Review somente depois disso.
---
<!-- COMMENTS:END -->
