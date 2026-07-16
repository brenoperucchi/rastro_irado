# Challenger Pair fixo WIN–WDO — metodologia (CONGELADA antes dos resultados)

**Projeto:** IRAI — backtester NF-01 (NF-01B / IRAI-4 AC#3)
**Criado:** 2026-07-16
**Status:** metodologia registrada ANTES de rodar contra dados (anti data-snooping).
Nenhum resultado deste challenger foi observado quando este documento foi escrito.
**Tarefa:** backlog IRAI-21, ref IRAI-4 AC#3.

## 1. Pergunta

O Pair Signal do dashboard é DINÂMICO: a cada barra escolhe como "par ativo" o fator de
maior |β| do Kalman (`backend/irai/zscore.py::select_active_pair`). Para WIN$N esse par é,
na maioria das barras, o WDO$N (ver `by_pair_factor` nos artefatos anteriores e o histórico
em `zscore.py:136`). Pergunta do challenger:

> Um par FIXO WIN–WDO, "burro" (sem Kalman, sem calibração, β por OLS rolling simples),
> entrega o mesmo edge — ou melhor — que o Pair dinâmico calibrado?

É o teste direto da regra de negócio 8 do plano ("regra simples vence modelo complexo
quando entrega resultado equivalente") e um controle para o valor real da escolha dinâmica
do par.

## 2. Definição do challenger (CONGELADA)

### 2.1 Par e escala
- Par SEMPRE WIN$N ↔ WDO$N. Para o target WIN$N o fator é WDO$N; para WDO$N o fator é WIN$N
  (simétrico). Nunca é escolhido dinamicamente.
- INDEPENDENTE do engine v2/Kalman/calibração: lê os preços dos DOIS símbolos direto de
  `market_bars` (M5), não passa pelo replay do engine. Consequência deliberada: **não sofre
  do achado C1-a** (calibração in-sample) — como os baselines momentum/reversão, é uma régua
  limpa. Não há modo `--point-in-time` porque não há nada calibrado para vazar do futuro.

### 2.2 Retornos, β e z (replicam a forma do Pair dinâmico)
Por barra `i` da sessão, ambos os retornos são **desde a abertura da sessão** (mesma base
`return_from_open` que o engine usa para `win_ret`/`f_ret`, `engine.py:820-829`):
```
ret_win[i]  = (close_win[i]  − open_win_sessão)  / open_win_sessão
ret_wdo[i]  = (close_wdo[i]  − open_wdo_sessão)  / open_wdo_sessão
β[i]        = OLS rolling de ret_win sobre ret_wdo nas últimas PAIR_SIGMA_WINDOW (20) barras
              (sem intercepto: β = Σ(ret_win·ret_wdo) / Σ(ret_wdo²) na janela; <2 pontos ⇒ β=0)
resíduo[i]  = pairwise_residual(ret_win[i], β[i], ret_wdo[i])   # = ret_win − β·ret_wdo
z_pair[i]   = pair_zscore(resíduos até i, janela 20)            # reusa zscore.py inalterado
sinal[i]    = pair_signal(z_pair[i], β[i], PAIR_THRESHOLD=1.5)  # reusa zscore.py inalterado
```
As duas ÚNICAS diferenças vs. o Pair dinâmico são: (a) par FIXO (WDO) em vez do maior-|β|;
(b) β por OLS rolling em vez do Kalman. Tudo o mais (janela, threshold, z centrado sem √t,
direção β-agnóstica) é o MESMO código de `zscore.py`.

### 2.3 Marker discreto (edge-triggered, causal)
O marker dispara só na TRANSIÇÃO do sinal (`neutral→buy` ⇒ compra; `neutral→sell` ⇒ venda),
igual ao Pair dinâmico. β[i], resíduo[i] e z[i] usam dados até o FECHAMENTO da barra i — o
marker nasce quando a barra i fecha (mesma garantia causal do achado X3).

### 2.4 Eixo temporal
As barras vêm do `market_bars` no eixo B3/BRT cru (UTC-3 — `CLAUDE.md`). Ao construir os
snapshots, o timestamp é DESLOCADO +offset sazonal (`brt_to_tickmill_offset_hours`) para o
eixo Tickmill — a MESMA convenção do engine — por dois motivos: (a) `run()` força
`is_b3=True` no `_hour_brt`, que espera o eixo Tickmill e desloca de volta para obter a hora
BRT correta; (b) deixa os 4 timestamps causais no MESMO eixo do Pair dinâmico, comparáveis
1:1. WIN$N e WDO$N vêm do MESMO terminal/coleta, então seus `timestamp_utc` crus são
idênticos por barra; o alinhamento é por igualdade exata (barras presentes em só um dos dois
símbolos são descartadas).

### 2.5 Entrada, saída, custo, MFE/MAE — MESMA metodologia do Pair dinâmico
Reusa `extract_trade_outcomes` inteiro (já revisado por dupla deep/fable):
- entrada = OPEN da barra SEGUINTE à do sinal (primeiro preço executável proxy); evento
  descartado se sem OHLC;
- saída forward em h∈{3,6,10,20} barras completas desde a entrada; MFE/MAE por HIGH/LOW;
- custo `TARGET_COST_POINTS` (WIN$N=10, WDO$N=1) debitado uma vez;
- cooldown 20 barras; 4 timestamps causais; bootstrap clusterizado por sessão (10k), IC95%;
- gate de amostra mínima 100 eventos (INCONCLUSIVO abaixo).

## 3. Comparações (CONGELADAS)

Contra o Pair DINÂMICO (do artefato `docs/artifacts/irai-4/` — braço executável do codex,
ou o `nf01_pit` do IRAI-2) e contra os baselines momentum/reversão, para WIN$N e WDO$N:

1. **Bruta** — retorno médio por evento (líquido de custo) por horizonte, IC95%, win-rate,
   com a frequência NATURAL de cada sinal.
2. **Frequência equivalente** — como os sinais disparam com frequências diferentes, reportar
   também a **expectativa por SESSÃO** = (retorno médio por evento) × (eventos por sessão),
   com `eventos por sessão = n_events / sessões medidas`. Isso normaliza a comparação pelo
   quanto cada sinal "ocupa" o tempo: um sinal que acerta pouco mas dispara muito pode render
   igual a um raro e certeiro. Reportar eventos totais e eventos/sessão de cada sinal.

RESSALVA DE JANELA (registrada antes dos resultados): o challenger é independente de
calibração, então mede TODAS as sessões elegíveis (~1250, 2021-2026). O Pair dinâmico do
artefato de comparação (`docs/artifacts/irai-4/`) roda em modo point-in-time, que só mede a
partir do 1º cutoff (~2022-12, ~880 sessões). A expectativa por sessão normaliza a
FREQUÊNCIA, mas as JANELAS temporais não são idênticas — o challenger inclui 2021-2022 que o
PIT não. Isso é documentado no artefato; a comparação é indicativa, não uma igualdade
perfeita de amostra. (Comparar contra um dinâmico retrospectivo na mesma janela seria a
igualdade perfeita, mas o artefato retrospectivo não é o braço executável de referência.)

Nenhuma comparação otimiza threshold/janela após ver os dados. Um `***` isolado NÃO é
confirmatório (até 24 combinações horizonte×direção por sinal).

## 4. Limitações herdadas + próprias
- Herda todas as `COMMON_LIMITATIONS` do NF-01 (custo aproximado ADR-002, MFE/MAE por
  HIGH/LOW sem ordem intrabar, comparações múltiplas, viés de OHLC ausente, etc.).
- PRÓPRIA: β por OLS rolling é uma escolha de convenção (janela 20, sem intercepto), não
  otimizada. Um β diferente daria outro resíduo — reportado como parâmetro fixo, não varrido.
- PRÓPRIA: o challenger NÃO reproduz o encadeamento do Kalman nem o warm-up de σ do par que o
  dinâmico tem no início da sessão; é intencional (o challenger é o braço "simples").

## 5. Entregáveis
- `scripts/measure_pair_fixed_value.py` + testes permanentes.
- Artefato SEPARADO `docs/artifacts/irai-21/` (comando, git hash, parâmetros, sessões,
  eventos, limitações, resultados + tabela comparativa bruta e por-sessão).
- IRAI-21 → Review.

## 6. Resultado (2026-07-16, após a metodologia congelada)

Rodado contra produção (`--limit 2000`, commit `5b67100`). Artefato:
`docs/artifacts/irai-21/` (README com a tabela completa). Resumo h=6, retorno
líquido de custo por evento (`***` = IC95% exclui zero):

```
                     WIN$N méd/ev   exp/sessão   WDO$N méd/ev  exp/sessão
pair_fixo (chall.)   -10.89 ***     -43.24       -0.84 ***     -3.36
pair (dinâmico PIT)   -7.47         -30.04       -1.00 ***     -4.18
baseline_momentum    -12.39 ***     -33.61       -0.90 ***     -2.50
baseline_reversao     -7.61         -20.66       -1.10 ***     -3.04
```

Conclusão: TODOS os sinais são negativos em ambos os alvos. Fixar o par WIN-WDO
NÃO recupera edge — em WIN$N é mais negativo que o dinâmico, em WDO$N são
parecidos e adversos. A regra simples (par fixo) não vence a complexa: as duas
perdem, e os baselines também. Reforça, com um controle limpo (sem C1-a), que os
markers de distorção não têm valor econômico como estão. Ver README do artefato
para a ressalva de janela (challenger mede toda a base; dinâmico é PIT ~2022-12+).
