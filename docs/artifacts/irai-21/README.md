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
| **pair_fixo** (toda a base) | −10,89 *** | −43,24 | −0,84 *** | −3,36 |
| **pair_fixo_windowed** (janela PIT) | −11,02 *** | −44,26 | −0,72 *** | −2,90 |
| pair (dinâmico, PIT) | −7,47 | −30,04 | −1,00 *** | −4,18 |
| baseline_momentum | −12,39 *** | −33,61 | −0,90 *** | −2,50 |
| baseline_reversao | −7,61 | −20,66 | −1,10 *** | −3,04 |

`exp/sessão` = retorno médio/evento × eventos/sessão (frequência equivalente).
`pair_fixo_windowed` recorta o challenger na MESMA janela do dinâmico PIT
(`session_date > 1º cutoff`) — ranking apples-to-apples (achado do /fable-reasoner).

## Conclusão
**Todos os sinais são NEGATIVOS em ambos os alvos** — nenhum tem edge positivo.
Fixar o par WIN–WDO **não recupera edge nenhum**. O ranking sobrevive à
comparação de mesma janela: em WIN$N o challenger é *mais* negativo que o
dinâmico **mesmo apples-to-apples** (−11,02 windowed vs −7,47/evento) — a
diferença de janela NÃO explicava o gap. Em WDO$N os dois são parecidos e ambos
adversos (−0,72 windowed vs −1,00). A "regra simples" (par fixo) não vence a
complexa aqui — **as duas perdem**, e os baselines também. Reforça, com um
controle limpo (sem C1-a), o achado central do NF-01: os markers de distorção
Pair/Z não são setups com valor econômico como estão.

## Diagnóstico de alinhamento (data_quality no artefato)
Barras presentes em só um dos dois símbolos são descartadas. Na base real o
descarte é NEGLIGÍVEL: WIN$N 32/138646 (~0,02%), WDO$N 1279/139790 (~0,9%),
0 sessões vazias. A ressalva de alinhamento fica quantificada, não hipotética.

## Ressalvas
- Janelas de medição: o challenger (linha `pair_fixo`) mede toda a base (~1250
  sessões, 2021+); o dinâmico é PIT (~880 sessões, 2022-12+). Use
  `pair_fixo_windowed` para o ranking na mesma janela (o `pair_fixo` de base fica
  para ver a estabilidade histórica).
- Um `***` isolado não é confirmatório (múltiplas comparações). Custos aproximados
  (ADR-002); MFE/MAE sem ordem intrabar. β OLS sem o guarda de σ-quase-nula do
  dinâmico (risco baixo, pernas líquidas). Fill executável completo e sensibilidade
  detalhada são IRAI-4/VAL-04.
