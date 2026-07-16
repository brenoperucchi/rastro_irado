# Plano — IRAI Tactical Layer para WIN e WDO

**Projeto:** IRAI — Intraday Risk Appetite Index  
**Criado:** 2026-07-13  
**Revisado:** 2026-07-16

**Status:** Em execução — NF-01A concluído e revisado; braço executável do NF-01B/VAL-04
concluído sem edge promovível; challengers e regra local ainda em avaliação; produção
Tactical não iniciada

**Autoridade:** especificação normativa do Tactical; status e prioridade vêm do
[`plano consolidado`](./2026-07-13-irai-plano-consolidado.md)

**Piloto:** `WIN$N`; `WDO$N` somente após validação própria

## 1. Resultado de negócio

Construir uma camada tática causal, auditável e explicável que transforme contexto,
distorções e regiões de preço em estados operacionais, mantendo o IRAI como suporte à
decisão e sem executar ordens.

O Tactical não transforma automaticamente `P_up` em compra ou venda. Ele separa:

- `P_up`: contexto macro/nowcast da sessão;
- marker `P`: distorção entre o ativo e seu hedge pairwise ativo;
- marker `Z`: divergência entre preço e contexto macro multivariado;
- NWE, VWAP e abertura: regiões locais;
- ATR: escala de distância e invalidação;
- regra ou modelo micro de 15 minutos: confirmação candidata, somente se aprovada OOS;
- estados: `NEUTRO`, `AGUARDANDO_PULLBACK`, `ARMADO`, `CONFIRMADO`, `INVALIDADO` e
  `NAO_OPERAR`.

Markers atuais continuam visíveis como diagnóstico, mas não equivalem a `ARMADO` nem
`CONFIRMADO`. Estratégia/modelo não aprovado retorna `NAO_OPERAR` no contrato Tactical.

## 2. Decisões normativas

1. `P_up` participa como contexto ou gate de regime no horizonte tático. No escopo medido
   `OPEN_20`, não demonstrou edge direcional aditivo em três/seis barras M5. Qualquer uso
   diferente exige validação própria.
2. Pair Spread e divergência macro-preço são evidências diferentes e não podem ser
   apresentadas como a mesma coisa.
3. Marker é observação de distorção; confirmação é um estado posterior.
4. Threshold operacional, payload e linha do gráfico usam a mesma configuração do backend.
5. Estado e evento persistido só avançam em barra M5 determinísticamente fechada.
6. Evidências correlacionadas não são somadas como votos independentes.
7. Regra transparente é preferível a modelo quando entrega resultado equivalente.
8. `P_micro_30m`, zonas estatísticas de realização e payload tático por barra ficam fora da
   v1.
9. Feature flag permanece desligada até aprovação histórica e validação Windows/live.

Justificativa e evidências: [estratégia de divergência versus Tactical](./2026-07-14-divergence-strategy-vs-tactical-layer.md).

## 3. Pré-requisitos

### 3.1 Concluídos

- ✅ causalidade do eixo temporal e DST no backend;
- ✅ correção da geometria calibrador/serving;
- ✅ migrações idempotentes com `migrate_to_head()` no boot;
- ✅ NWE/VWAP/ATR causal como fonte única no backend;
- ✅ propagação dos campos NWE pela API/Firebase/frontend;
- ✅ Pair Z-Score, divergência macro-preço e markers por transição;
- ✅ medição walk-forward do papel tático do macro.
- ✅ NF-01A point-in-time com eventos Pair/Z/interseção, baselines e contrato temporal;
- ✅ revisão independente do NF-01A sem bloqueadores.

### 3.2 Bloqueios antes da ativação

1. Unificar `pair_threshold` servido e desenhado; remover `±2` fixo quando o runtime usar
   outro valor.
2. Expor a configuração efetiva necessária para explicar cada marker.
3. Definir a identificação de barra fechada e cobri-la por regressão.
4. Medir o relógio Axi contra uma fonte conhecida.
5. Conferir cesta, pesos, versão, `pair_factor` e comportamento do WDO em produção.
6. Validar o caminho Firebase/mobile e o replay/live final no Windows.

O framework pode ser desenvolvido no Linux. A coleta MT5 e o rollout live não podem ser
declarados validados neste ambiente.

## 4. Fonte única de observações

Criar um extrator puro e determinístico compartilhado por backtest, calibração, API
histórica e live. Cada linha usa somente informações conhecidas no fechamento da barra.

### 4.1 Observações iniciais

- **Distorção pairwise:** `pair_z`, `pair_signal`, `pair_factor`, `pair_beta`, idade do par
  ativo e distância ao threshold.
- **Divergência macro-preço:** `price_diverge_z`, direção `Z` e persistência.
- **Contexto macro:** `P_up`, faixa/regime, estabilidade e qualidade; não assumir edge
  direcional curto.
- **Preço:** retornos em 1/2/3 barras, range, corpo, pavios e posição na sessão.
- **NWE:** centro, bandas, direção, distância em ATR e disponibilidade.
- **Regiões:** distância à VWAP e à abertura da sessão.
- **Risco/qualidade:** ATR14, stale, ghost, pré-mercado, barra aberta/fechada e volume.

Não usar `delta` aproximado como fluxo institucional. D-P-Z-E e confluências correlacionadas
entram como variáveis identificadas, nunca como contagem ingênua de confirmações.

### 4.2 Barra fechada

A última barra mutável do collector não pode alterar estado nem persistir evento. O runtime
deve adotar uma das garantias abaixo e documentar a escolhida:

- flag persistida `bar_closed`; ou
- regra determinística: processar uma barra somente depois da chegada da próxima barra do
  mesmo símbolo/sessão.

Markers diagnósticos da borda direita podem ser provisórios, mas precisam ser visualmente
distintos e nunca gerar `tactical_events`.

## 5. Backtester de distorções

O backtester percorre cada sessão em ordem cronológica, sem smoothing, sem persistir estado
Kalman de um replay sobre produção e sem converter gaps em retorno zero.

### 5.1 Experimentos obrigatórios

1. Pair Signal isolado.
2. Divergência macro-preço isolada.
3. Interseção Pair + Z.
4. Pair condicionado ao regime de `P_up`, sem presumir contribuição linear.
5. Pair condicionado à direção/região do NWE.
6. Pair + NWE + VWAP/ATR.
7. Baselines de momentum e reversão simples com a mesma frequência de eventos.
8. Pair dinâmico atual versus um par fixo economicamente conhecido, com a mesma regra de
   entrada e custo.

Os experimentos são hierárquicos. Pair/Z e baselines formam o bloco **confirmatório**. Os
condicionamentos por `P_up`, NWE, VWAP/ATR e identidade/idade do par formam o bloco
**condicional** e só podem ser interpretados depois do bloco confirmatório. Novas features,
labels ou combinações descobertas durante a análise são **exploratórias**: geram uma nova
hipótese e não promovem produção no mesmo período em que foram escolhidas.

### 5.2 Registro por evento

- ativo, sessão, timestamp e barra fechada;
- lado e origem da distorção (`P`, `Z` ou ambos);
- par ativo, beta, threshold e Z-Score efetivos;
- regime macro e região local;
- preço de observação, armação, confirmação e invalidação;
- `observation_bar_end`, `confirmation_bar_end`, `signal_available_at`, `entry_at` e
  `entry_price`;
- retorno após 3, 6, 10 e 20 barras;
- MFE, MAE e tempo até saída;
- custos e resultado líquido;
- reason codes e versão da regra/modelo.

Custos conservadores iniciais:

- WIN: 10 pontos por evento confirmado;
- WDO: 1 ponto por evento confirmado.

Além do cenário principal, reportar `0,5×`, `1,0×`, `1,5×` e `2,0×` o custo conservador.
Um edge que desaparece com pequena variação de custo não é robusto para promoção.

Labels, MFE e MAE nunca atravessam a fronteira da sessão. Como MFE/MAE chegam a 20 barras,
o purge entre partições é `max(horizonte_label, horizonte_MFE) = 20` barras.

### 5.3 Instante executável e preço de entrada

O backtest deve representar quando a informação se tornou disponível, não apenas o horário
nominal da barra. O preço usado para formar ou confirmar o evento não pode ser reutilizado
como fill quando já não era negociável no instante da decisão.

Contrato mínimo:

```text
observation_bar_end
confirmation_bar_end
signal_available_at
entry_at
entry_price
```

`entry_price` é o primeiro preço realmente negociável depois de `signal_available_at`, com
a política de latência declarada. Se o fechamento determinístico da barra `t` só puder ser
conhecido com a chegada da barra `t+1`, a simulação não pode entrar retroativamente no close
de `t`. MFE, MAE e PnL começam em `entry_at`.

### 5.4 Contrato contínuo e status provisório

Resultados de WIN/WDO permanecem `provisional` enquanto não for verificado no Windows/MT5
se as séries `$N` são ajustadas, concatenadas cruas ou tratadas de outra forma. A auditoria
deve identificar rollovers relevantes e reportar sensibilidade com e sem suas janelas.
Gaps artificiais não podem ser interpretados como distorção econômica.

**Auditoria WIN de 2026-07-16.** O MT5/XP identifica `WIN$N` como série contínua por
liquidez **sem ajustes**. No ledger executável do NF-01B, excluir uma sessão antes, a sessão
de vencimento e uma sessão depois removeu 261/3.697 eventos Pair (7,06%), não criou edge
positivo e tornou o agregado `h=3` significativamente negativo. Portanto, o rollover
mascarava parte da perda em vez de fabricar a ausência de edge. O gate continua provisório
até completar o WDO.

### 5.5 Resultado do braço executável do NF-01B

O artefato PIT de 18.005 eventos usa entrada no `open` da M5 seguinte à confirmação,
horizontes por barras completas, MFE/MAE por OHLC e custos em `0,5×`, `1,0×`, `1,5×` e
`2,0×`. O Pair dinâmico do WIN não apresenta edge positivo robusto mesmo a `0,5×` do
custo. No WDO, ele permanece significativamente negativo em todos os horizontes inclusive
a `0,5×`. Z e Pair+Z não satisfazem evidência/amostra para promoção. O realismo econômico,
portanto, não resgatou os markers atuais; eles permanecem diagnósticos.

### 5.6 Hipótese principal, múltiplos testes e holdout

Antes de interpretar o relatório final, registrar por ativo:

- hipótese confirmatória principal;
- regra, threshold, entrada, saída/invalidação e custo;
- baseline primário e métrica econômica principal;
- período final intocado;
- número máximo de variações confirmatórias permitidas;
- critério de aprovação e de abandono.

O walk-forward reduz vazamento temporal, mas não elimina seleção por testar muitas regras,
horários, pares e thresholds. Resultados exploratórios precisam de novo período intocado ou
novo ciclo pré-registrado.

### 5.7 IRAI como filtro de abstenção

Além de perguntar se `P_up` prevê direção, medir se o contexto IRAI melhora uma regra local
ao **bloquear operações ruins**. Comparar a mesma regra com e sem o gate, reportando:

- expectativa líquida e drawdown;
- cobertura e percentual de eventos bloqueados;
- maus trades evitados;
- bons trades perdidos;
- resultado por faixa/regime, sem presumir relação linear.

Um gate que reduz risco ou cauda negativa pode ser útil mesmo sem ganho de AUC direcional.

## 6. Regra transparente antes do modelo

Antes de treinar classificador, avaliar uma regra causal simples:

```text
distorção válida
  + região causal tocada
  + reação do preço em barra fechada
  + qualidade disponível
  + regime macro não bloqueante
= candidato a CONFIRMADO
```

Se essa regra não superar baselines e custos no OOS, um modelo mais complexo não deve ser
usado para resgatá-la sem hipótese nova e pré-registrada.

Uma regra transparente aprovada pode seguir diretamente para a máquina de estados. O modelo
micro não é etapa obrigatória.

## 7. Modelo micro de 15 minutos — opcional e condicionado

Somente se o backtest justificar, treinar um classificador probabilístico para o horizonte
de três barras M5, com classes `UP`, `NEUTRAL` e `DOWN`.

- classe neutra quando o retorno futuro absoluto for menor que
  `max(custo, 0,10 × ATR14)`;
- regressão logística multinomial com regularização L2 como baseline modelado;
- estimador, solver, regularização e calibração precisam ser nomeados no artefato;
- `P_up` entra inicialmente como contexto/gate, não como feature direcional obrigatória;
- qualquer feature macro adicional precisa provar ganho aninhado sobre preço/Pair/NWE.

O label fixo de três barras permanece como baseline confirmatório. Outcomes por trajetória
— por exemplo, alvo antes da invalidação dentro de um limite de tempo — são pesquisa
exploratória separada e só podem substituir o label após nova pré-especificação e validação.

### 7.1 Walk-forward

- mínimo de 120 sessões de treino;
- 20 sessões para calibração;
- 20 sessões para teste;
- avanço de 20 sessões;
- purge de 20 barras;
- scaler, hiperparâmetros e thresholds ajustados apenas no passado;
- bootstrap clusterizado por sessão;
- nenhuma escolha baseada no período final.

### 7.2 Artefato reproduzível

Persistir JSON, nunca pickle:

- nomes e ordem das features;
- médias e escalas de treino;
- coeficientes, interceptos e classes;
- cutoff temporal;
- threshold efetivo;
- estimador/solver/calibração;
- versão, métricas e status `experimental|approved`.

O runtime recusa artefato incompatível, sem cutoff ou com cutoff posterior à barra de
replay.

### 7.3 Gate de aprovação

Exigir por ativo:

- calibração avaliada sobre **todas** as previsões OOS, não apenas confirmações filtradas;
- Brier/log-loss melhor que baseline definido e registrado;
- ECE reportado sobre todas as previsões OOS;
- pelo menos 100 eventos confirmados para o gate econômico;
- expectativa líquida positiva após custos;
- ganho sobre regra transparente e baselines simples;
- intervalo bootstrap por sessão;
- estabilidade mínima por fold e horário.

O ganho de um modelo é medido de forma incremental contra a regra transparente aprovada,
com a mesma política de entrada, custo e cobertura. Melhor calibração sem melhoria econômica
executável não autoriza promoção.

Modelo reprovado permanece `experimental`; o Tactical retorna `NAO_OPERAR` com
`MODEL_NOT_APPROVED`. Diagnósticos `P`/`Z` podem continuar visíveis fora da máquina de
estados.

### 7.4 Governança, validade e despromoção

`approved` não é permanente. Cada artefato aprovado deve registrar owner, cutoff, hash do
schema de features, data de revisão/expiração e política de monitoramento. O runtime ou o
processo de governança devolve o artefato a `experimental` quando critérios pré-definidos
detectarem, por exemplo:

- drift relevante das features ou da cobertura;
- mudança material de custos ou frequência de eventos;
- degradação live persistente fora do intervalo esperado;
- mudança de identidade/estabilidade do par;
- incompatibilidade de schema, fonte ou versão.

A despromoção força `NAO_OPERAR`, é auditada e exige gate explícito para recuperação.

## 8. Regiões causais

### 8.1 VWAP

Usar preço típico e `real_volume`, com `volume` somente quando válido. Se ambos forem
zero/ausentes, marcar VWAP indisponível sem inventar valor.

### 8.2 NWE

Consumir exclusivamente o NWE causal do backend. Centro e bandas são regiões diferentes e
devem ser identificadas no evento.

### 8.3 Abertura

Representar a abertura como zona com raio inicial de `0,20 ATR`. Região é tocada quando a
distância for `<= 0,20 ATR`. Em sobreposição, escolher a mais próxima e registrar as demais
como metadados, sem convertê-las automaticamente em votos.

## 9. Máquina de estados canônica

As regras são simétricas para `LONG` e `SHORT`. A avaliação ocorre uma vez por barra
fechada, nesta ordem de prioridade:

| Prioridade | Estado/ação | Regra de negócio |
|---:|---|---|
| 1 | `NAO_OPERAR` | Modelo/regra não aprovado, stale crítico, ghost, pré-mercado, barra aberta ou ATR/região indisponível. |
| 2 | `INVALIDADO` | Setup ativo perde a região, rompe `0,35 ATR` contra a hipótese ou recebe evidência contrária definida. |
| 3 | `CONFIRMADO` | Setup `ARMADO` recebe reação local aprovada no OOS. |
| 4 | `ARMADO` | Distorção válida toca região causal e regime macro não bloqueia. |
| 5 | `AGUARDANDO_PULLBACK` | Distorção válida existe, mas região/reação ainda não ocorreu. |
| 6 | `NEUTRO` | Nenhuma oportunidade ativa e dados válidos. |

### 9.1 Transições

```text
NEUTRO
  → AGUARDANDO_PULLBACK
  → ARMADO
  → CONFIRMADO
  → INVALIDADO

qualquer estado → NAO_OPERAR, quando qualidade/aprovação falhar
```

`CONFIRMADO` e `INVALIDADO` entram em cooldown mínimo de três barras fechadas. Um novo setup
na mesma região exige **ambos**: cooldown concluído e preço ter saído da região antes de
retornar. Cada transição emite no máximo um evento idempotente.

`CONFIRMADO` significa hipótese tática aprovada e vigente; não significa autorização de
ordem. Uma futura política de execução e risco deverá consumir o evento em outro layer.

Ignorar os três primeiros candles M5 da B3 para armação/confirmação; diagnósticos podem ser
exibidos sem promover estado.

## 10. Persistência e configuração

### 10.1 `tactical_models`

Guardar artefato JSON, ativo, horizonte, cutoff, versão, métricas, status e criação.

### 10.2 `tactical_events`

Guardar ativo, sessão, timestamp, `bar_closed`, estado anterior/novo, lado, origem da
distorção, região, preços, probabilidades quando existirem, modelo/regra e reason codes.
Guardar também o instante de disponibilidade do evento, identificador/ciclo de origem e os
campos executáveis definidos em §5.3 quando houver simulação de entrada.

Chave idempotente:

```text
(target, timestamp, strategy_version, new_state)
```

### 10.3 Configuração por ativo

Feature flag, custos, threshold Pair/Z efetivo, histerese, raios ATR, cooldown, versões e
regra/modelo ativo. Defaults mantêm Tactical desligado.

## 11. API e Firebase

Na v1, acrescentar `tactical` somente ao overview/current e servir a timeline por eventos.
Não duplicar um objeto Tactical completo em cada barra da série.

```json
{
  "state": "ARMADO",
  "side": "LONG",
  "observation": {
    "pair_signal": "buy",
    "pair_z": -1.72,
    "pair_threshold": 1.5,
    "z_divergence": false
  },
  "macro_context": "ALLOW_LONG",
  "region": {
    "type": "session_vwap",
    "price": 132450.0,
    "distance_atr": 0.08
  },
  "p_micro_15": null,
  "invalidation_price": 132180.0,
  "reason_codes": ["PAIR_DISTORTION_NEGATIVE", "REGION_TOUCHED"],
  "strategy_version": "win_tactical_v1",
  "approved": true,
  "bar_closed": true
}
```

- contrato aditivo, sem remover campos existentes;
- V2 explícito nos caminhos Tactical;
- cache por chave completa, incluindo `strategy_version`;
- invalidação continua via `notify_update`;
- threadpool e single-flight antes do rollout live;
- Firebase não recomputa regra quantitativa no cliente.

## 12. Interface

Separar visualmente duas camadas:

### Diagnóstico

- `P DISTORÇÃO −/+` ou `P COMPRA/VENDA` com rótulo “observação”;
- `Z DISTORÇÃO −/+` ou `Z COMPRA/VENDA` com rótulo “observação”;
- NWE, VWAP, GEX e contexto macro.

### Tactical

- `AGUARDAR`;
- `ARMADO`;
- `CONFIRMADO`;
- `INVALIDADO`;
- `NÃO OPERAR`.

Verde/vermelho forte é reservado a `CONFIRMADO`. `ARMADO` usa âmbar; observações usam
cores secundárias. Mostrar reason codes, região, invalidação e versão. Não exibir zonas de
realização na v1.

## 13. Regressões permanentes

1. Causalidade das observações e features.
2. Threshold servido igual ao threshold desenhado.
3. Barra aberta nunca altera estado nem persiste evento.
4. Ghost/pré-mercado sem evento.
5. Criação limpa e migração repetida do banco.
6. Splits temporais com purge de 20 barras.
7. Round-trip JSON do artefato, quando houver modelo.
8. Sequência sintética completa da máquina de estados.
9. Impossibilidade de `ARMADO`/`CONFIRMADO` sem aprovação.
10. Impossibilidade de confirmar sem distorção, região e reação.
11. Invalidação estrutural e de qualidade.
12. Cooldown + saída/retorno à região.
13. Idempotência de eventos.
14. API aditiva e paridade local/Firebase.
15. Alinhamento B3/Tickmill em verão e inverno, mais controle global.
16. Fill posterior à disponibilidade do sinal; impossibilidade de entrada retroativa no
    preço de confirmação.
17. Sensibilidade de custos e par fixo versus dinâmico com frequência comparável.
18. Separação entre relatório confirmatório e exploração; período final intocado.
19. Despromoção de artefato força `NAO_OPERAR`.
20. Shadow ledger não envia ordens e reconcilia preço teórico com preço disponível.

## 14. Validação e rollout

Ordem:

1. alinhar thresholds e marker semântico;
2. implementar regra/teste de barra fechada;
3. ✅ extrator e backtester de distorções;
4. ✅ revisão independente do núcleo do NF-01;
5. 🚧 NF-01B/VAL-04: instante executável, custos, baselines, rollover e controle de seleção;
6. gate explícito: parar, promover regra transparente ou autorizar modelo micro 15m;
7. migrações, estados e eventos somente para hipótese aprovada;
8. governança, despromoção e observabilidade;
9. API/Firebase e UI com flag desligada;
10. validação histórica no Linux;
11. validação do WDO e replay/live no Windows;
12. shadow live com ledger de decisões e fills hipotéticos;
13. ativação individual do Tactical: WIN primeiro, WDO depois.

### 14.1 Shadow live

Antes de qualquer projeto de execução automática, o pipeline Tactical completo opera sem
ordens e registra eventos confirmados, bloqueados, invalidados e near-misses. Para cada
intenção hipotética, registrar preço disponível, latência, slippage estimado, MFE/MAE e
resultado líquido. Comparar frequência, distribuição das features e expectativa entre
backtest e live. Divergências materiais bloqueiam ativação.

Comandos previstos quando os arquivos existirem:

```bash
pytest tests/test_tactical_features.py
pytest tests/test_tactical_state_machine.py
pytest tests/test_tactical_models.py
pytest tests/test_api_tactical_contract.py
pytest tests/
python -X utf8 scripts/backtest_tactical.py --target 'WIN$N'
cd frontend && npm run lint
cd frontend && npm run build
```

Não declarar comandos futuros como validação realizada.

## 15. Fora de escopo da v1

- execução automática de ordens;
- cálculo ou recomendação de lote;
- copy trading;
- `P_micro_30m`;
- zonas estatísticas de realização;
- objeto Tactical completo por barra na série;
- Volume Profile e book;
- expansão imediata aos 20 ativos;
- alegar validação live a partir do Linux;
- recalibrar o macro ou editar `FACTOR_MAP.md` sem evidência.

## 16. Fronteira futura com o Execution Layer MT5

Execução automática continua fora do Tactical v1 e deve nascer como projeto separado,
somente depois do shadow live aprovado:

```text
Tactical CONFIRMADO
    ↓ evento versionado; ainda não é ordem
Execution Policy
    ↓ valida freshness, idempotência, conta, símbolo e política de entrada
Risk Layer
    ↓ autoriza ou rejeita exposição, lote, stop e limites
EA / Broker
    ↓ envia, acompanha e reconcilia ordens/posições
```

O futuro EA inicia com `EnableTrading=false`, allowlist de conta/servidor, `MagicNumber`,
kill switch e estado persistente do último evento processado. O transporte local por
arquivo ou HTTP é detalhe desse projeto posterior; não altera a autoridade quantitativa do
backend Tactical.
