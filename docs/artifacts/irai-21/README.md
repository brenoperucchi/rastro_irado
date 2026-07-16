# Challenger Pair fixo WIN–WDO (IRAI-21 / IRAI-4 AC#3)

Artefato do challenger **Pair fixo WIN↔WDO** e sua comparação contra o Pair
**dinâmico** e os baselines momentum/reversão. Metodologia CONGELADA antes dos
resultados em `docs/plans/2026-07-16-challenger-pair-fixo-win-wdo.md`.

## Arquivos
- **`pair_fixo_challenger.json.gz`** — completo (gzip, ~406 KB): metadata +
  comparação + `challenger` com os 10464 eventos individuais (4 timestamps
  causais cada).
- **`pair_fixo_challenger_summary.json`** — o mesmo SEM os eventos (legível):
  metadata + tabela de comparação + `event_counts`.

## O que é o challenger
Par SEMPRE WIN↔WDO (nunca escolhido pelo Kalman), computado INDEPENDENTE do
engine/calibração: lê `market_bars`, β por OLS rolling 20 (sem intercepto).
**Não sofre de C1-a** — régua limpa (regra de negócio 8: simples vs complexo).
Mesma entrada executável (open da barra seguinte), custos, MFE/MAE OHLC e
timestamps causais do Pair dinâmico.

## Reprodutível
`command`/`parameters`/`git` no artefato. `git.commit == git.origin_main`
(`5b67100`) — código publicado/localizável. Rodado no host de produção
(`ryzen5wsl`) com `--limit 2000`.

## Resultado (h=6, retorno líquido de custo por evento; `***` = IC95% exclui 0)

| Sinal | WIN$N méd/ev | WIN$N exp/sessão | WDO$N méd/ev | WDO$N exp/sessão |
|---|---|---|---|---|
| **pair_fixo** (challenger) | −10,89 *** | −43,24 | −0,84 *** | −3,36 |
| pair (dinâmico, PIT) | −7,47 | −30,04 | −1,00 *** | −4,18 |
| baseline_momentum | −12,39 *** | −33,61 | −0,90 *** | −2,50 |
| baseline_reversao | −7,61 | −20,66 | −1,10 *** | −3,04 |

`exp/sessão` = retorno médio/evento × eventos/sessão (frequência equivalente).

## Conclusão
**Todos os sinais são NEGATIVOS em ambos os alvos** — nenhum tem edge positivo.
Fixar o par WIN–WDO **não recupera edge nenhum**: em WIN$N o challenger é *mais*
negativo que o dinâmico (−10,89 vs −7,47/evento; −43 vs −30/sessão); em WDO$N os
dois são parecidos e ambos adversos. A "regra simples" (par fixo) não vence a
complexa aqui — **as duas perdem**, e os baselines também. Reforça, com um
controle limpo (sem C1-a), o achado central do NF-01: os markers de distorção
Pair/Z não são setups com valor econômico como estão.

## Ressalvas
- Janelas de medição não idênticas: o challenger mede toda a base (~1250 sessões,
  2021+); o Pair dinâmico do artefato de referência (`docs/artifacts/irai-4/`) é
  point-in-time (~880 sessões, 2022-12+). A expectativa por sessão normaliza a
  FREQUÊNCIA, não a janela temporal (ver metodologia §3).
- Um `***` isolado não é confirmatório (múltiplas comparações). Custos aproximados
  (ADR-002); MFE/MAE sem ordem intrabar. Fill executável completo e sensibilidade
  detalhada são IRAI-4/VAL-04.
