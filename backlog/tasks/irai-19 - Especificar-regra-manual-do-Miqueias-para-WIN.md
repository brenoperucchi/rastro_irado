---
id: IRAI-19
title: Especificar regra manual do Miqueias para WIN
status: Done
assignee:
  - '@claude'
created_date: '2026-07-16 04:41'
updated_date: '2026-07-16 05:26'
labels:
  - tactical
  - win
  - miqueias
  - business-rules
dependencies: []
references:
  - 'backlog://task/IRAI-17'
documentation:
  - docs/plans/2026-07-14-divergence-strategy-vs-tactical-layer.md
priority: high
type: docs
ordinal: 19000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Transformar a leitura discricionária descrita pelo Miqueias em uma especificação determinística e revisável, sem implementar código nem continuar os itens estatísticos do NF-01. A regra deve separar regime do P, região GEX/MID, confirmação Pair/NWE, entrada, alvo, stop, invalidação, abstenção e dados ainda ausentes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A especificação contém uma tabela de decisão para compra, venda e NAO_OPERAR
- [x] #2 Cada condição informa se usa barra fechada e qual é o primeiro preço executável
- [x] #3 GEX, MID, Pair e NWE têm papéis separados e sem dupla contagem
- [x] #4 Alvo, stop, cooldown e invalidação são explicitados ou marcados como decisão pendente do Miqueias
- [x] #5 A especificação não promove setup nem altera código de produção
- [x] #6 Auditoria identifica a implementação GEX já existente no IRAI e registra que o repositório público miqueiasa1/wdowin_pairtrading, main 7fce5bc e histórico público, não contém código GEX localizável
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Especificação escrita em docs/plans/2026-07-16-regra-manual-miqueias-win.md a partir das
fontes disponíveis (vision doc IRAI, plano de divergência §2-§7, imagem explenation.jpeg,
os 3 indicadores em docs/indicadores/ — walls.txt/GEX, gaussiana.txt/NWE original,
hist_zscore.txt/fluxo institucional — e a implementação atual em backend/irai/).

Não existe transcrição literal do Miqueias além dessas fontes: a tabela de decisão (§5) é
uma reconstrução a partir da evidência disponível, não uma cópia de instruções dele.

7 ambiguidades identificadas e documentadas na §6, cada uma com os candidatos de resposta
listados (não inventados): threshold do regime P_up (55/45 produção vs 60/40 imagem),
definição de "região GEX válida" (walls.txt só dá a geometria, não uma regra de
proximidade), cooldown, alvo/stop/invalidação (nenhuma fonte especifica valores), papel
exato do NWE (o IRAI atual só expõe dados descritivos — direção/bandas/inclinação — não
tem o evento discreto de toque de banda que a fonte original do Miqueias tinha), critério
de desempate Pair vs Z, e fonte/atualização do GEX (não integrado ao IRAI hoje).

Achado técnico relevante: gaussiana.txt (fonte original do NWE do Miqueias) tem uma regra
de ENTRADA discreta por toque de banda (Close cruza de volta pra dentro -> BuyAtMarket/
SellShortAtMarket) que backend/irai/nwe.py NÃO implementa — o backend atual só calcula
região/direção/inclinação como dado descritivo, sem esse evento.

Nenhum código foi alterado. Nenhum item do NF-01 (docs/plans/2026-07-14-divergence-
strategy-vs-tactical-layer.md §11, itens 4-6) foi continuado nesta tarefa.

Correção factual concluída no commit b7cf047 e verificada: documento agora descreve o pipeline GEX integrado e registra a ausência de código GEX localizável no histórico público do repositório indicado.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @codex
created: 2026-07-16 05:03
---
Revisão encontrou erro factual nas §6.7/§7: `backend/workers/gex_worker.py` já integra GEX ponta a ponta desde os commits 4dd1273..39e6822. Para WIN, usa OI BDI/B3 + metadados/prêmio/spot/settle do MT5 XP, BSM/IV, netGEX call-put, flip cumulativo e conversão IBOV→WIN. O repo público indicado pelo Miqueias (`miqueiasa1/wdowin_pairtrading`, main 7fce5bc, tag e 17 commits) foi varrido sem encontrar GEX/Gamma/opções/strike/OI. Corrigir o documento, separar código confirmado do IRAI da alegação ainda não verificável sobre o repo externo e formular pedido de caminho/commit ao Miqueias.
---

created: 2026-07-16 05:20
---
Correção pós-implementação (@claude): §4.1/§6.7/§7 estavam desatualizadas.

GEX já está integrado desde backend/workers/gex_worker.py (commits
4dd1273..39e6822) — não é mais Input externo manual. Documentei a fonte
real: OI via BDI/B3 (API pública, paginada, sort=TckrSymb obrigatório),
strike/CP/vencimento/prêmio EOD via MT5 XP (symbols_get em lote, session_close
como prêmio), BSM/IV invertida por bisseção (fallback pra vol realizada
horizon-matched onde não há prêmio, ex. WDO$N via DOL sem cobertura de opção
no MT5), netGEX(K) = Σ[Γcall·OIcall − Γput·OIput], Gamma Flip por cruzamento
de zero do netGEX cumulativo, Gamma Max/Min por argmax/argmin com refino
parabólico, conversão IBOV→WIN via f=win_settle/spot (análogo DOL$N→WDO$N,
com sanity clamp só na perna WDO). Worker roda 1x/dia (EOD), não intraday —
níveis ficam fixos durante a sessão.

Verifiquei (não apenas assumi) que o repositório público
miqueiasa1/wdowin_pairtrading (main, HEAD 7fce5bc, 17 commits) não contém
código GEX localizável: gh search code retornou zero resultados para
GEX/gamma/netGEX/GammaFlip, e um clone completo + git log --all -p sobre
todo o histórico (não só o HEAD) também não encontrou nenhuma menção a
gamma/gex/netgex/gammaflip/open interest/black scholes/bsm em nenhum dos
17 commits. Registrado como fonte verificada no documento (§2).

Não toquei o worker GEX. Ambiguidades residuais da §6 continuam pendentes
do Miqueias (região de proximidade da wall, alvo/stop/cooldown/invalidação,
papel do NWE, desempate Pair×Z) — a única removida da lista foi "fonte do
GEX" (§6.7, agora resolvida com fatos, não mais pendência).
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Regra manual do Miqueias para WIN especificada de forma determinística, com lacunas de negócio explicitadas. Integração GEX corrigida e documentada a partir do worker real; repositório externo auditado. Nenhum código de produção alterado.
<!-- SECTION:FINAL_SUMMARY:END -->
