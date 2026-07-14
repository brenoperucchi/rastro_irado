# IRAI — Fundação NWE causal no backend

**Projeto:** IRAI — Intraday Risk Appetite Index  
**Criado:** 2026-07-13  
**Status:** Planejado — pré-requisito do Tactical Layer  
**Ativos de validação:** `WIN$N`, `WDO$N` e um ativo global de controle

## 1. Objetivo

Tornar o backend a única fonte autoritativa do Nadaraya-Watson Envelope (NWE),
com cálculo estritamente causal e reproduzível barra a barra. O frontend deve
somente renderizar os valores recebidos.

Esta entrega precisa ser concluída antes de gerar features, calibrar ou avaliar
os modelos micro de 15 e 30 minutos do Tactical Layer. Treinar com o NWE atual
do overview contaminaria o backtest com informação posterior.

## 2. Problema confirmado

O cálculo resumido de `nwe_slope` em `backend/api/main.py` calcula o centro de
cada ponto percorrendo a série inteira:

```python
for j in range(n):
```

Ao calcular o centro da penúltima barra, a última barra já participa da média.
Consequentemente, acrescentar uma observação pode mudar valores históricos.
Esse comportamento é incompatível com replay, backtest e decisão ao vivo.

O `computeNWE` detalhado em `frontend/src/App.jsx` já usa somente lookback para
a linha central, mas ainda concentra no browser uma regra quantitativa que
também é necessária no overview, no Firebase e nos futuros modelos. Manter duas
implementações cria risco de divergência.

## 3. Especificação causal

Para cada barra real `t`, usar apenas observações `j <= t`:

- kernel gaussiano unilateral;
- `bandwidth = 8`;
- `lookback = 95` barras válidas;
- centro calculado sobre preço absoluto;
- largura do envelope igual ao MAE causal móvel multiplicado por `3`;
- inclinação igual a `center[t] - center[t-1]`;
- nenhuma inspeção da inclinação de `t+1` para gerar estado ou marker;
- valores normalizados em retorno apenas como campos derivados para a UI.

As barras anteriores à sessão, retornadas em `history_closes`, servem somente
como warm-up. Barras ghost não entram no kernel, não alteram o envelope e não
podem gerar transições; quando necessário para a visualização, repetem o último
valor causal conhecido.

### 3.1 Campos por barra

O snapshot/API deve expor, no mínimo:

```json
{
  "nwe_center_price": 132450.0,
  "nwe_upper_price": 132780.0,
  "nwe_lower_price": 132120.0,
  "nwe_center": 0.42,
  "nwe_upper": 0.67,
  "nwe_lower": 0.17,
  "nwe_slope": 18.5,
  "nwe_direction": "up"
}
```

O backend também deve calcular na mesma passagem os insumos que o Tactical
Layer usará junto ao NWE:

- `atr_14` causal;
- `session_vwap`, usando preço típico e `real_volume`, com fallback para
  `volume` quando válido;
- `distance_to_nwe_atr`;
- `distance_to_vwap_atr`;
- flag explícita de indisponibilidade quando volume ou ATR não forem válidos.

Não usar `delta` aproximado como substituto de fluxo real ou order book.

## 4. Arquitetura e contrato

Extrair o cálculo para um módulo puro do backend, sem dependência de FastAPI ou
estado global. A mesma função deve ser consumida por:

1. `IRAIEngine.compute_from_db()` para enriquecer snapshots históricos e live;
2. `/api/irai/series` para replay completo;
3. `/api/irai/overview` para o último estado, sem recalcular por outra fórmula;
4. calibrador/backtester tático para geração de features;
5. sincronização Firebase, preservando os mesmos campos.

O cache continua invalidado por `/api/internal/notify_update`. Não criar cache
paralelo específico para NWE.

O contrato deve permanecer aditivo: nenhum campo atual será removido nesta
fase. Adicionar `schema_version` ao payload Firebase quando a propagação dos
novos campos for ativada.

## 5. Migração do frontend

- Consumir os campos NWE do backend em `TVNweChart` e nos cards de overview.
- Remover `computeNWE` como fonte autoritativa depois de confirmar paridade.
- Durante uma janela curta de compatibilidade, aceitar cálculo local apenas se
  o payload antigo não possuir campos NWE; marcar esse caminho como legado e
  removê-lo após atualização do Firebase.
- Não usar lookahead visual para criar marker, badge, cor operacional ou
  transição tática.
- Preservar preço absoluto no chart de movimento e a regra atual de ghost bars
  como whitespace/repetição visual sem sinal.

## 6. Regressões permanentes

Os testes devem ser escritos antes da correção e falhar com a implementação
atual quando aplicável.

1. **Invariância de prefixo:** calcular `NWE(x[0:n])` e `NWE(x[0:n+k])`; os
   primeiros `n` resultados precisam ser idênticos.
2. **Sem futuro imediato:** alterar apenas a barra `t+1` não pode alterar centro,
   bandas ou inclinação em `t`.
3. **Warm-up:** histórico anterior pode alterar as primeiras barras da sessão,
   mas nunca usar observações posteriores ao timestamp calculado.
4. **Ghost bars:** não entram no kernel, não mudam a inclinação e não disparam
   eventos.
5. **Gap intrassessão:** ausência de barra não vira retorno zero nem observação
   artificial.
6. **Paridade:** a série backend deve reproduzir a implementação causal de
   referência com tolerância numérica definida.
7. **Overview/series:** o último NWE do overview deve ser exatamente o último da
   série para a mesma chave de cache.
8. **Fuso:** validar `WIN$N`/`WDO$N` no eixo EEST com reconstrução BRT e um ativo
   global sem o deslocamento de seis horas.
9. **VWAP/ATR indisponíveis:** divisão por zero ou volume ausente produz flag,
   nunca `NaN`/`Infinity` no JSON.

## 7. Validação e critérios de aceite

Executar, nesta ordem:

```bash
pytest tests/test_nwe_causality.py
pytest tests/
cd frontend && npm run lint
cd frontend && npm run build
```

Aceite somente quando:

- todos os testes de invariância causal passarem;
- overview e series retornarem o mesmo último NWE;
- nenhum valor histórico mudar ao anexar barras futuras;
- os charts preservarem a forma visual atual para WIN e WDO;
- ghost bars e pré-mercado permanecerem sem sinal;
- o payload Firebase transportar os valores sem recomputação no cliente;
- não houver alteração nos seis-horas de alinhamento B3/Tickmill.

A validação live final deve ocorrer no Windows com os terminais reais. Neste
Linux, validar somente cálculo histórico, API, regressões e frontend.

## 8. Ordem de entrega

1. Criar as regressões de causalidade e contrato.
2. Implementar o módulo puro de NWE/VWAP/ATR.
3. Enriquecer snapshots no engine.
4. Fazer overview reutilizar o último snapshot da série.
5. Propagar os campos pela API e pelo Firebase.
6. Migrar os charts e badges para os campos do backend.
7. Remover o cálculo local legado.
8. Rodar validação completa.
9. Liberar o NWE como fonte autorizada para o Tactical Layer.

## 9. Fora de escopo

- Alterar os pesos ou fatores do IRAI macro.
- Recalibrar `WIN$N` ou `WDO$N`.
- Volume Profile ou leitura de book.
- Execução de ordens, stops ou loteamento.
- Polimento de crosshair, touch/mobile ou eixo visual além do necessário para
  preservar o comportamento atual.

