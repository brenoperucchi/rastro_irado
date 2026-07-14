# Plano — IRAI Tactical Layer para WIN e WDO

**Projeto:** IRAI — Intraday Risk Appetite Index  
**Criado:** 2026-07-13  
**Status:** Planejado  
**Piloto:** `WIN$N` e `WDO$N`

## 1. Resumo

Construir uma camada tática causal, auditável e explicável que transforme o
`P_up` macro em estados operacionais, mantendo o IRAI como suporte à decisão e
sem executar ordens.

A camada separará explicitamente:

- `P_up`: probabilidade macro de fechamento da sessão;
- `P_micro_15m`: movimento provável nas próximas três barras M5;
- `P_micro_30m`: sustentação provável nas próximas seis barras M5;
- regiões causais: VWAP, NWE e abertura da sessão;
- estados: `NEUTRO`, `AGUARDANDO_PULLBACK`, `ARMADO`, `CONFIRMADO`,
  `INVALIDADO` e `NAO_OPERAR`;
- risco: invalidação e zonas estatísticas de realização, sem lote ou ordem.

O sistema será liberado primeiro para WIN e WDO por feature flag. Um modelo que
não satisfizer os critérios fora da amostra poderá chegar até `ARMADO`, mas não
emitirá `CONFIRMADO` na interface principal.

## 2. Pré-requisitos obrigatórios

### 2.1 NWE causal

Concluir integralmente
[`2026-07-13-nwe-causal-backend-foundation.md`](./2026-07-13-nwe-causal-backend-foundation.md)
antes de gerar features ou treinar os modelos micro. NWE, VWAP e ATR usados no
backtest devem ser os mesmos servidos ao vivo pela API.

### 2.2 Migrações idempotentes

Corrigir o bootstrap do banco antes de adicionar tabelas táticas:

- `init_db()` deve executar a cadeia completa de migrações;
- criação limpa e atualização de banco existente devem produzir o mesmo schema;
- `divergence_config` e `kalman_state` precisam existir sem depender da execução
  manual de `backend/db.py`;
- regressões devem cobrir execução repetida e preservação dos dados.

Depois dessa fundação, adicionar `tactical_models`, `tactical_events` e a
configuração tática por ativo pela mesma cadeia.

### 2.3 Confirmar o modelo macro do WDO

Antes da calibração micro definitiva:

1. comparar WDO local V2 e referência de produção na mesma sessão;
2. confirmar cesta, pesos, `pair_factor`, acurácia e versão carregada;
3. manter a cesta se a divergência V1/V2 já explicar a diferença observada;
4. recalibrar somente mediante divergência real e documentada;
5. regenerar `FACTOR_MAP.md` apenas se houver recalibração.

Essa validação depende do banco/ambiente real do Windows. O framework pode ser
desenvolvido no Linux, mas o modelo WDO não será aprovado sem essa conferência.

## 3. Fundação causal e backtester

### 3.1 Extrator único de features

Criar um extrator puro e determinístico, compartilhado por calibração, backtest,
API histórica e live. Cada linha deve conter somente informações disponíveis no
fechamento da barra correspondente.

Features iniciais:

- macro: `P_up`, logit, score, variações em 1/2/3 barras e persistência;
- preço: retornos em 1/2/3 barras, ATR14, range, corpo e pavios;
- NWE: centro, bandas, inclinação, curvatura e distâncias normalizadas por ATR;
- regiões: distância à VWAP e à abertura da sessão;
- tempo: fração causal da sessão;
- qualidade: stale flags, ghost, pré-mercado e disponibilidade de volume/ATR.

Não usar `delta` aproximado como fluxo institucional. D-P-Z-E não serão somados
como evidências independentes porque derivam de variáveis correlacionadas.

### 3.2 Replay e avaliação

O backtester deve percorrer cada sessão barra a barra, sem persistir Kalman ao
reproduzir sessões históricas e sem transformar gaps em retornos zero.

Registrar por evento:

- timestamp e estado anterior/novo;
- macro e probabilidades micro;
- região tocada e distância em ATR;
- preço de confirmação e invalidação;
- retorno após 3, 6, 10 e 20 barras;
- MFE, MAE e tempo até invalidação/realização;
- resultado líquido com custos configurados.

Custos conservadores iniciais:

- WIN: 10 pontos por operação;
- WDO: 1 ponto por operação.

Comparar contra direção aleatória pela frequência, `P_up` isolado e NWE isolado.

## 4. Modelos micro de 15 e 30 minutos

Treinar dois classificadores Ridge multinomiais por ativo:

- horizonte de três barras: gatilho de curto prazo;
- horizonte de seis barras: confirmação de sustentação;
- classes `UP`, `NEUTRAL` e `DOWN`;
- classe neutra quando o retorno futuro absoluto for menor que
  `max(custo, 0,10 × ATR14)`.

### 4.1 Walk-forward

- mínimo de 120 sessões de treino;
- 20 sessões para calibração;
- 20 sessões para teste;
- avanço de 20 sessões;
- purge de seis barras entre partições;
- seleção de regularização e thresholds somente nas partições internas;
- bootstrap por sessão para intervalos de confiança.

Não usar validação aleatória, smoothing futuro ou parâmetros escolhidos sobre o
período final.

### 4.2 Artefato reproduzível

Persistir em JSON, nunca pickle:

- nomes e ordem das features;
- médias e escalas de treino;
- coeficientes, interceptos e classes;
- cutoff temporal;
- thresholds de confirmação;
- versão, métricas e status `experimental|approved`.

Criar `tactical_models` com uma versão por ativo/horizonte. O runtime deve
recusar artefatos com features incompatíveis ou cutoff posterior à barra de
replay.

### 4.3 Gate de aprovação

Exigir por ativo:

- pelo menos 100 confirmações fora da amostra;
- Brier Score melhor que o baseline;
- Expected Calibration Error `<= 0,10`;
- expectativa líquida positiva;
- intervalo bootstrap reportado por sessão.

Se reprovado, manter o modelo experimental e bloquear `CONFIRMADO` no live.

## 5. Regiões causais

### 5.1 VWAP

Calcular com preço típico e `real_volume`. Usar `volume` somente quando válido;
se ambos forem zero/ausentes, marcar VWAP indisponível sem inventar fallback.

### 5.2 NWE

Consumir exclusivamente os valores causais do backend. Centro e bandas são
regiões diferentes, identificadas no evento.

### 5.3 Abertura

Representar como uma zona com raio inicial de `0,20 ATR`.

Uma região é tocada quando a distância do preço for `<= 0,20 ATR`. Se houver
sobreposição, escolher a mais próxima e registrar todas as confluências como
metadados, sem transformá-las automaticamente em votos.

## 6. Máquina de estados

Regras simétricas para `LONG` e `SHORT`:

1. **NEUTRO:** macro insuficiente ou modelo sem aprovação.
2. **AGUARDANDO_PULLBACK:** `P_up` ultrapassa o threshold calibrado por duas
   barras consecutivas.
3. **ARMADO:** preço move contra o macro e toca região válida sem o macro perder
   o threshold de histerese.
4. **CONFIRMADO:** probabilidade de 15 minutos volta a favorecer o macro e a de
   30 minutos não o contradiz.
5. **INVALIDADO:** macro perde validade, preço rompe a região em mais de
   `0,35 ATR` ou o modelo micro vira contra.
6. **NAO_OPERAR:** stale crítico, conflito 15/30, região/ATR indisponível, ghost
   bar ou janela inicial da sessão.

Ignorar os três primeiros candles M5 da B3. Após confirmação/invalidação,
aplicar cooldown de três barras ou até o preço sair e retornar à região.
Cada transição deve emitir no máximo um evento.

## 7. Invalidação e zonas de realização

- Invalidação estrutural: limite da região acrescido de `0,35 ATR` contra a
  direção do setup, além das invalidações macro/micro.
- Zona de realização 1: mediana de MFE fora da amostra para o setup/ativo.
- Zona de realização 2: percentil 75 de MFE fora da amostra.
- Exibir faixas e condições, não ordens, lote ou promessa de execução.
- Se a amostra condicional for insuficiente, omitir as zonas e retornar reason
  code explícito.

## 8. Persistência e configuração

### 8.1 `tactical_models`

Guardar artefato JSON, ativo, horizonte, cutoff, versão, métricas, status e
timestamp de criação.

### 8.2 `tactical_events`

Guardar ativo, timestamp, sessão, estado anterior/novo, lado, probabilidades,
região, preços, zonas, modelo e reason codes. Usar chave idempotente por
`(target, timestamp, model_version, new_state)` para impedir duplicatas.

### 8.3 Configuração por ativo

Adicionar configuração JSON com feature flag, custos, thresholds aprovados,
histerese, raios ATR, cooldown e versões ativas. Defaults devem manter o
Tactical Layer desligado até calibração aprovada.

## 9. API e Firebase

Acrescentar, sem remover campos existentes, `tactical` ao overview e a cada
barra da série:

```json
{
  "state": "ARMED",
  "side": "LONG",
  "p_micro_15": {"up": 0.58, "neutral": 0.25, "down": 0.17},
  "p_micro_30": {"up": 0.51, "neutral": 0.31, "down": 0.18},
  "region": {
    "type": "session_vwap",
    "price": 132450.0,
    "distance_atr": 0.08
  },
  "invalidation_price": 132180.0,
  "realization_zones": [132780.0, 133020.0],
  "reason_codes": ["MACRO_LONG", "PULLBACK", "WAITING_MICRO"],
  "model_version": "win_tactical_2026_01",
  "approved": true
}
```

- Propagar `schema_version`, `history_closes`, `is_b3`, NWE/VWAP/ATR e tactical
  pelo Firebase, sem recomputação quantitativa no cliente.
- Tornar V2 explícito em todos os caminhos usados pelo Tactical Layer; revisar o
  default ainda V1 de `/api/irai/current` para evitar contrato ambíguo.
- Manter cache por chave completa e invalidação via `notify_update`.
- Antes do rollout live, executar engine/modelos em threadpool e implementar
  single-flight por `(target, date, version, tactical_model_version)`.

## 10. Interface

Na overview, priorizar um badge textual:

- `AGUARDAR`;
- `PULLBACK`;
- `ARMADO`;
- `CONFIRMADO`;
- `INVALIDADO`;
- `NÃO OPERAR`.

Usar âmbar para `ARMADO`; verde/vermelho forte somente para `CONFIRMADO`. Na
tela detalhada, mostrar timeline de estados, região, probabilidades 15/30,
invalidação, zonas e reason codes.

Manter D-P-Z-E como diagnóstico secundário. Antes de ampliar a tela principal,
extrair de `App.jsx` o cliente local/Firebase, helpers de timezone e componentes
táticos. Crosshair, touch/mobile e demais polimentos não bloqueiam o rollout.

## 11. Regressões permanentes

1. Causalidade/invariância do NWE e das features.
2. Criação limpa e migração repetida do banco.
3. Splits temporais purgados e scaler treinado apenas no passado.
4. Round-trip JSON dos dois modelos.
5. Sequência sintética completa de estados.
6. Impossibilidade de confirmar sem pullback e região.
7. Conflito 15/30, stale ou ausência de ATR resultando em `NAO_OPERAR`.
8. Ghost/pré-mercado sem evento.
9. Invalidação macro, micro e estrutural.
10. Cooldown e idempotência de eventos.
11. API aditiva e paridade local/Firebase.
12. Alinhamento de seis horas para WIN/WDO e controle global.

## 12. Validação e rollout

Executar:

```bash
pytest tests/test_nwe_causality.py
pytest tests/test_tactical_features.py
pytest tests/test_tactical_state_machine.py
pytest tests/test_tactical_models.py
pytest tests/test_api_tactical_contract.py
pytest tests/
python -X utf8 scripts/calibrate_tactical.py --target 'WIN$N'
python -X utf8 scripts/calibrate_tactical.py --target 'WDO$N'
python -X utf8 scripts/backtest_tactical.py --target 'WIN$N'
python -X utf8 scripts/backtest_tactical.py --target 'WDO$N'
cd frontend && npm run lint
cd frontend && npm run build
```

Ordem de ativação:

1. regressões e migrações;
2. NWE causal no backend;
3. conferência macro do WDO;
4. backtester e calibração micro;
5. máquina de estados e eventos;
6. API/Firebase não bloqueantes;
7. UI com feature flag desligada;
8. validação histórica no Linux;
9. replay/live no Windows;
10. ativação individual de WIN e WDO somente após aprovação.

## 13. Fora de escopo

- execução automática de ordens;
- cálculo ou recomendação de lotes;
- copy trading;
- Volume Profile e book de ofertas na primeira versão;
- expansão imediata aos 20 ativos;
- alegar validação live a partir do ambiente Linux;
- recalibrar o fator macro ou editar `FACTOR_MAP.md` sem evidência.

