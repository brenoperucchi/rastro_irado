---
title: "Collector não corrige barras recém-fechadas (late tick / ajuste do broker)"
date: 2026-07-14
priority: low
context: "5ª rodada de revisão /codex-r do achado X3 (barra fechada) do plano consolidado — achado adjacente, diferente do X3 original, registrado separado a pedido do usuário em vez de expandir escopo naquela correção"
ref: "backend/workers/collector.py:84-110, backend/irai/engine.py (bar_may_be_forming/BAR_FORMING_MAX_AGE)"
---

# Collector não corrige barras recém-fechadas

## Contexto

`collect_recent_bars()` (`backend/workers/collector.py`) busca as 5 barras M5 mais
recentes via `mt5.copy_rates_from_pos(symbol, TIMEFRAME_M5, 0, n_bars=5)` a cada ciclo.
Só a última (`i == len(rates) - 1`, a barra "corrente") usa `INSERT OR REPLACE`; as outras
4 usam `INSERT OR IGNORE` — ou seja, uma barra é gravada **permanentemente** assim que
deixa de ser "corrente" (na próxima vez que aparecer no lote de 5, na posição
`len(rates)-2`, o `IGNORE` preserva o valor já gravado).

**O problema teórico:** se o MT5/broker ajustar um pouco o OHLCV de uma barra logo depois
dela fechar (late tick que chega com timestamp dentro da janela mas processado um
instante depois, ou correção server-side do broker), esse ajuste nunca chega ao banco — a
barra já foi congelada pelo `IGNORE` no ciclo anterior, com o valor que era conhecido
naquele momento, não necessariamente o valor final.

## Por que não foi resolvido junto com o X3

O achado X3 (CRÍTICO, plano consolidado §3.1) é sobre **causalidade de leitura**: não ler
uma barra que ainda está sendo ativamente reescrita como "corrente". A correção aplicada em
`backend/irai/engine.py` (`bar_may_be_forming`, `BAR_FORMING_MAX_AGE=10min`,
`_now_on_tickmill_axis()`) resolve exatamente isso e foi validada em 4 rodadas de revisão
`/codex-r` adversarial.

Este achado é sobre **confiabilidade de coleta** — um problema diferente, que afetaria
qualquer campo derivado (P_up, NWE, z-scores, GEX), não só os markers. Resolvê-lo exigiria:
1. Mudar `collect_recent_bars()` pra fazer UPSERT nas 5 barras retornadas, não só na
   última — permitindo que uma correção tardia do MT5 se propague ao banco por alguns
   ciclos após o fechamento.
2. Alargar `is_last_target_bar` em `engine.py` de "só a última barra" pra "as últimas
   ~5", senão o achado X3 reaparece numa forma mais estreita (marker nascendo da 2ª/3ª
   barra mais recente, que também passaria a ser reescrita).

Ambos os passos tocam `collector.py` — componente Windows-only (depende do
`MetaTrader5` Python lib), não testável de verdade neste ambiente de desenvolvimento Linux
— e são **teóricos**: nenhum incidente real de correção tardia foi observado nos dados de
produção até agora, só deduzido a partir da lógica do `IGNORE`.

## Tarefas (quando for priorizado)

- [ ] Confirmar empiricamente, com dados reais de produção, se barras M5 já corrigidas
      pelo broker/MT5 depois do fechamento realmente ocorrem neste setup (XP/Tickmill/Axi)
      — sem essa evidência, o UPSERT de 5 barras é uma mudança especulativa num caminho
      crítico de coleta.
- [ ] Se confirmado: mudar `collect_recent_bars()` pra UPSERT (não só a última barra).
- [ ] Alargar `is_last_target_bar`/`bar_may_be_forming` em `engine.py` na mesma janela
      (últimas N barras, não só a última), com regressão dedicada.
- [ ] Validar em replay/live no Windows (VAL-03) antes de considerar concluído — não dá
      pra validar coleta MT5 real a partir do Linux.
