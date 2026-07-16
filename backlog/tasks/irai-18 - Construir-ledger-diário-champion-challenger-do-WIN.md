---
id: IRAI-18
title: Construir ledger diário champion-challenger do WIN
status: Review
assignee:
  - '@codex'
created_date: '2026-07-16 04:41'
updated_date: '2026-07-16 05:11'
labels:
  - validation
  - win
  - p-dynamic
  - gex
dependencies: []
references:
  - 'backlog://task/IRAI-17'
documentation:
  - docs/plans/2026-07-13-irai-plano-consolidado.md
modified_files:
  - scripts/compare_p_dynamic_parity.py
  - scripts/evaluate_p_dynamic_champions.py
  - tests/test_compare_p_dynamic_parity.py
  - tests/test_p_dynamic_champion_evaluator.py
  - scripts/systemd/rastro-irado-p-dynamic-ledger.service
  - scripts/systemd/rastro-irado-p-dynamic-ledger.timer
  - backend/irai/engine.py
  - backend/api/main.py
  - tests/test_api_nwe_contract.py
  - tests/test_nwe_causality.py
priority: high
type: feature
ordinal: 18000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Preservar, por sessão e de forma reproduzível, os dados necessários para comparar P Dinâmico do Miqueias, IRAI v1/v2 e versões futuras sem depender do Firebase corrente. O bundle deve reunir as séries de P, WIN M5 e sinais locais disponíveis, além do snapshot GEX/MID, e alimentar um avaliador que não declare vencedor abaixo do gate mínimo de amostra.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Cada captura preserva séries brutas de Miqueias, v1 e v2, metadados de origem e timestamp da coleta
- [x] #2 O bundle preserva WIN OHLC e campos Pair/NWE presentes nas séries locais, além do snapshot GEX/MID disponível para a sessão
- [x] #3 O avaliador calcula métricas de qualidade probabilística somente em barras operacionais e sessões fechadas
- [x] #4 O relatório distingue avaliação do objetivo diário do P da utilidade econômica como gate tático
- [x] #5 Abaixo do gate mínimo de sessões o resultado é INCONCLUSIVO e nenhum quality_winner é promovido
- [x] #6 Testes permanentes cobrem montagem do bundle, sessão incompleta, ausência de GEX e gate de amostra
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Auditar contratos API/Firebase/GEX e definir schema versionado do ledger.
2. Especificar por testes a captura atômica e os gates de sessão/amostra.
3. Implementar captura completa reutilizando o comparador existente.
4. Implementar avaliação champion-challenger para o objetivo diário, mantendo o gate tático separado.
5. Executar no Ryzen, publicar e registrar limitações.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Auditoria no Ryzen5WSL: `/api/irai/series` já expõe P, WIN, Pair, NWE, VWAP e ATR por barra; `/api/irai/gex` expõe gamma max/flip/min, walls e `mid_wall` separadamente. Banco de produção: WIN M5 tem 138.646 barras desde 2021-07-12, mas `gex_levels` possui apenas 2 datas (2026-07-10..2026-07-13). O ledger precisa começar imediatamente e o avaliador deve bloquear qualquer vencedor abaixo do gate.

Implementação local concluída: bundle versionado e atômico preserva documentos brutos Miqueias/v1/v2, manifesto de fechamento BRT, GEX/walls/mid_wall e relatório de paridade. Avaliador agrega Brier/log-loss dentro da sessão, inclui baseline climatológico causal Beta(1,1), exige 60 sessões comuns e bootstrap pareado IC95% contra todos os concorrentes; o gate tático permanece NOT_EVALUATED. Timer diário proposto para 17:56 BRT, somente leitura das APIs.

Validação produtiva no Ryzen5WSL após pull de `4495ac2`: 16 testes específicos passaram; serviço oneshot executou com status 0; bundle real preservou envelopes v1/v2, WIN/Pair/NWE, GEX ativo com 17 walls e 16 mid_walls. Captura pré-mercado foi corretamente marcada `closed=false`; avaliador retornou `INCONCLUSIVE`, 0/60 sessões, `quality_winner=null` e gate tático `NOT_EVALUATED`. Timer diário `rastro-irado-p-dynamic-ledger.timer` habilitado para Mon..Fri 17:56 BRT. Suíte mantida: `pytest -q tests --ignore=tests/test_measure_tactical_gate3.py` → 207 passed, 16 skipped. `pytest -q` global não é utilizável neste Linux porque coleta scripts/archive que exigem MT5 e um teste que exige sklearn.

Correção pós-revisão: a engine e `/api/irai/series` agora expõem `win_bar_open`, `win_high` e `win_low` por barra real, preservando `win_open` como abertura da sessão. Regressão permanente falhou antes com AttributeError em `IRAISnapshot.win_bar_open` e passou após a correção; o teste do bundle confirma persistência dos três campos.

Validação pós-correção no Ryzen5WSL (`444cc00`): regressão engine OHLC 2 passed; regressão HTTP OHLC 1 passed; ledger/evaluator 7 passed. API Windows reiniciada com o mesmo Uvicorn e retornou health ok; payload v1 contém as três chaves novas. Serviço oneshot gerou bundle `2026-07-16T050637Z` com status 0 e contrato OHLC preservado; valores nulos são esperados nas barras ghost pré-mercado.

Correção do NO-GO: fallback de `brt_offset_h` agora usa a regra sazonal compartilhada quando o envelope local não informa offset; o manifesto registra status de fechamento por fonte e só fecha com todas as fontes capturadas completas e ao menos uma local. O loader recalcula o fechamento dos documentos e rejeita manifesto antigo/corrompido com outcome parcial. Duas regressões novas falharam antes (janeiro +5h; v2 parando 17:30) e agora passam. Suíte mantida: 209 passed, 17 skipped.

Validação systemd revelou que o novo import de timezone dependia do cwd. Regressão subprocess fora da raiz falhou antes com `ModuleNotFoundError: backend`; o CLI agora adiciona explicitamente a raiz do repositório ao `sys.path`. Suíte mantida atual: 210 passed, 17 skipped.

Validação final no Ryzen5WSL após `13334ef`: 19 testes específicos passaram; serviço systemd executou com Result=success/ExecMainStatus=0; bundle `2026-07-16T051102Z` registrou offset 6 e status separado de Miqueias/v1/v2, todos corretamente incompletos no pré-mercado.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @codex
created: 2026-07-16 04:51
---
Implementação pronta para revisão. A primeira sessão fechada será capturada automaticamente hoje às 17:56 BRT; nenhuma promoção ocorrerá antes de 60 sessões comuns e IC95% favorável.
---

author: @codex
created: 2026-07-16 04:59
---
Revisão pós-implementação encontrou lacuna no AC #2: o bundle preserva win_open (abertura da sessão) e win_current (close), mas a API ainda não expõe abertura da barra, máxima e mínima. Sem OHLC por barra não é possível medir MFE/MAE nem simular alvo/stop. Tarefa reaberta para corrigir o contrato com regressão permanente.
---

created: 2026-07-16 05:06
---
Revisão independente (@claude) — foco: causalidade, fechamento de sessão, BRT/EEST, baseline, bootstrap.

VEREDITO: NO-GO. 2 defeitos reais e reproduzidos, ambos em scripts/compare_p_dynamic_parity.py; nenhum coberto pelos 16 testes existentes.

DEFEITO 1 (BRT/EEST, direção segura mas real) — main() usa brt_offset_h=6 hardcoded como default e só sobrescreve com o valor real (via document["brt_offset_h"], que a API local calcula corretamente com backend/irai/timezones.py::brt_to_tickmill_offset_hours) SE v1 ou v2 estiverem disponíveis no momento da captura. brt_to_tickmill_offset_hours é sazonal (6h no horário de verão americano, 5h fora dele — 2a Sáb de março a 1a Sáb de novembro). Fora dessa janela (~nov-mar), se v1/v2 falharem ou estiverem indisponíveis na captura, o fallback hardcoded=6 fica 1h errado. Reproduzido: uma sessão que fechou às 17:55 BRT real em 2026-01-15 (offset real=5) é classificada closed=False com o fallback (calcula last_operational_brt=16:55), e closed=True com o offset correto. Direção seguraa (nunca marca sessão aberta como fechada), mas derruba sessões válidas silenciosamente durante ~4 meses/ano sempre que a API local não responder na captura — justo quando o timer roda perto do fechamento (17:56 BRT).

DEFEITO 2 (fechamento de sessão, mais sério) — capture_session_status() em main() só recebe `reference` (série pública do Miqueias); nunca verifica se as séries LOCAIS v1/v2 (fonte do outcome real em evaluate_p_dynamic_champions.py::_actual_outcome) estão igualmente completas. Reproduzido: bundle com referência completa até 17:55 BRT (manifest closed=True) e v2 com dado só até 17:30 BRT (25min faltando) é aceito sem erro por load_ledger_sessions e o outcome (actual_up) é calculado com o último preço LOCAL conhecido (17:30), não o fechamento real da sessão. Isso contamina o ground-truth usado pra pontuar TODOS os modelos naquela sessão, não é uma vantagem de um candidato específico — mas viola a garantia causal que o resto do projeto mantém rigorosamente (achado X3 do NF-01 é sobre exatamente este tipo de problema, barra em formação vazando pro resultado).

Achado menor, fora do escopo pedido mas relevante pro AC #2 já reaberto: o payload público do Miqueias (curl direto no Firebase) já inclui win_bar_open/win_high/win_low por barra — a API local (/api/irai/series) ainda não expõe isso, então o gap de OHLC (MFE/MAE, alvo/stop) é assimétrico entre referência e séries locais.

Baseline (Beta(1,1) climatológico) e bootstrap (resample por sessão, IC95% "vence todos os oponentes") foram auditados e estão corretos — nenhum achado nessas duas frentes.

Reproduções rodadas localmente com os módulos reais do repo (não pseudocódigo), disponíveis sob pedido.
---

author: @codex
created: 2026-07-16 05:06
---
IRAI-18 pronto novamente para revisão independente após fechar a lacuna OHLC. Captura diária das 17:56 permanece habilitada.
---

author: @codex
created: 2026-07-16 05:07
---
NO-GO do reviewer aceito. Reabrindo para corrigir offset sazonal no fallback e exigir fechamento consistente das fontes locais usadas para outcome antes de selecionar o bundle.
---

author: @codex
created: 2026-07-16 05:11
---
Correções do NO-GO prontas para re-review: commits 81d11cf e 13334ef. Favor repetir as duas reproduções originais e tentar adulterar `manifest.session.closed=true`; esperado: janeiro usa +5, fonte local parcial bloqueia fechamento e loader rejeita manifesto adulterado.
---
<!-- COMMENTS:END -->
