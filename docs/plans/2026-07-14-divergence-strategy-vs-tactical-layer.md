# IRAI — Estratégia de divergência atual versus Tactical Layer

**Projeto:** IRAI — Intraday Risk Appetite Index  
**Criado:** 2026-07-14  
**Status:** Registro de decisão — decisões promovidas aos planos oficiais em 2026-07-14

**Papel documental:** evidência e justificativa; não substitui o status do plano consolidado
nem a especificação normativa do Tactical Layer

**Revisão independente:** Claude Code 2.1.209, somente leitura, 2026-07-14  
**Imagem analisada:** [`docs/explenation.jpeg`](../explenation.jpeg)  
**Plano vigente:** [`2026-07-13-irai-plano-consolidado.md`](./2026-07-13-irai-plano-consolidado.md)

## 1. Objetivo

Documentar o que a explicação visual de “Marcações de Compra e Venda” representa,
compará-la com o código que existe hoje e posicioná-la corretamente dentro da rota até o
Tactical Layer.

Este documento responde a quatro perguntas:

1. O que as marcações da imagem realmente significam?
2. Quanto dessa estratégia já está implementado?
3. O que ainda não possui validação econômica ou confirmação tática?
4. Como essa camada deve alimentar o Tactical Layer sem prometer mais do que a evidência
   estatística permite?

Ele não substitui o plano consolidado. É um documento de alinhamento entre estratégia,
produto, estatística e implementação.

## 2. Resumo executivo

A imagem descreve principalmente a camada de **detecção de divergências** já existente no
IRAI. Ela combina visualmente:

- o contexto macro multivariado (`P_up`);
- o preço do ativo;
- o Z-Score de um spread pairwise dinâmico;
- markers de compra e venda quando surgem distorções relativas.

Boa parte dessa camada já está implementada: Pair Z-Score, divergência macro-preço,
markers por transição, NWE causal, API e renderização. Entretanto, a imagem apresenta os
markers como sinais mais maduros do que eles são. Hoje eles identificam uma **condição de
afastamento**, mas ainda não possuem máquina de estados, confirmação micro, invalidação
estrutural, persistência tática nem gate econômico próprio.

Além disso, a explicação visual contém três incompatibilidades com o runtime:

1. o gráfico desenha regiões em `±2`, mas o backend dispara Pair Signal em `±1,5` por
   padrão;
2. a imagem mostra macro em `60/40`, enquanto a divergência macro-preço usa `55/45`;
3. o Pair Spread não mede diretamente a distância contra o `P_up`: mede o residual entre
   o ativo e **um fator ativo** escolhido pelo Kalman.

O walk-forward mais recente também alterou o desenho futuro: `P_up` não demonstrou valor
direcional aditivo nos horizontes de 15 e 30 minutos, equivalentes a três e seis barras M5.
Portanto, deve entrar no Tactical como **contexto ou filtro de regime**, não como prova de
direção de curto prazo.

## 3. Vocabulário canônico

### 3.1 `P_up` — contexto macro multivariado

Probabilidade de alta estimada pelo modelo macro do IRAI a partir de uma cesta de fatores
cross-asset. É um nowcast da condição/direção da sessão, não uma previsão validada do
retorno das próximas três ou seis barras.

### 3.2 Divergência macro-preço — marker `Z`

Compara o viés do `P_up` com o retorno normalizado do próprio ativo:

- `P_up > 55` e retorno anormalmente negativo: `Z COMPRA`;
- `P_up < 45` e retorno anormalmente positivo: `Z VENDA`.

É uma divergência entre o consenso multivariado e o preço observado.

### 3.3 Pair Spread — marker `P`

Compara o ativo com um fator selecionado dinamicamente pelo maior `|β|` válido do Kalman.
A relação pode mudar ao longo da sessão.

```text
par_ativo = argmax |βᵢ(t)|
resíduo_t = retorno_target_t − β_t × retorno_fator_t
z_pair_t  = (resíduo_t − média_rolling) / desvio_rolling
```

O runtime usa uma janela rolling de 20 **barras**, reseta o histórico quando o par ativo
muda e exclui fatores com volatilidade quase nula.

### 3.4 NWE — direção e região local

O Nadaraya-Watson Envelope não é o sinal macro nem o Pair Spread. Ele descreve a estrutura
local do preço por uma linha central, bandas, inclinação e distâncias normalizadas por ATR.
Atualmente é calculado causalmente no backend.

### 3.5 Tactical Layer — decisão explicável

Camada futura que deve transformar observações e regiões em estados como:

```text
NEUTRO → AGUARDANDO_PULLBACK → ARMADO → CONFIRMADO
                                      ↘ INVALIDADO
                                      ↘ NAO_OPERAR
```

Um marker atual não equivale a `CONFIRMADO`.

## 4. Como interpretar a imagem

A imagem apresenta uma sequência de quatro eventos:

### 4.1 Compra original

- contexto macro comprador;
- ativo cai sem acompanhamento equivalente do contexto;
- Pair Spread entra em região negativa extrema;
- hipótese: ativo relativamente barato e candidato a convergência para cima.

### 4.2 Venda 1

- contexto macro começa a perder força;
- ativo sobe ou sustenta alta de forma relativamente isolada;
- Pair Spread entra em região positiva extrema;
- hipótese: ativo relativamente caro e candidato a convergência para baixo.

### 4.3 Venda 2

- disparada forte do ativo;
- fator de referência não acompanha na mesma intensidade;
- residual pairwise se desloca para cima;
- hipótese: excesso relativo após a impulsão.

### 4.4 Venda 3

- o spread normaliza parcialmente;
- depois surge um novo afastamento positivo;
- ocorre uma nova transição para venda.

Essa repetição é compatível com o código: o marker aparece quando o sinal muda. Depois de
retornar a `neutral`, um novo extremo pode emitir outro marker.

## 5. O que existe hoje

| Componente | Estado | Evidência principal |
|---|---|---|
| `P_up` macro multivariado | Implementado | `backend/irai/engine.py` |
| Pair ativo pelo maior `|β|` | Implementado | `backend/irai/zscore.py::select_active_pair()`, chamado pelo engine |
| Residual hedgeado | Implementado | `pairwise_residual()` |
| Z-Score rolling do residual | Implementado | `pair_zscore()` |
| Sinal `buy/sell/neutral` | Implementado | `pair_signal()` |
| Direção independente do sinal de β | Corrigida e testada | `tests/test_pair_zscore.py` |
| Marker `P COMPRA/VENDA` | Implementado | engine + `TVNweChart.jsx` |
| Marker `Z COMPRA/VENDA` | Implementado | engine + `TVNweChart.jsx` |
| Marker somente na transição | Implementado | `prev_pair_sig` / `prev_div_dir` |
| Ghost/pré-mercado sem marker | Implementado e testado | `tests/test_premarket.py` |
| NWE/VWAP/ATR causal no backend | Implementado | `backend/irai/nwe.py` |
| NWE propagado à API/frontend | Implementado | API + commits `a93a510`, `9a280b1` |
| Migrações idempotentes no boot | Implementado | `migrate_to_head()` |
| Evento garantidamente em barra fechada | Não implementado | apenas barra real é verificada |
| Backtest econômico específico do Pair Signal | Pendente | tarefa de validação ainda aberta |
| Máquina de estados Tactical | Não implementada | sem runtime tático |
| `tactical_models` / `tactical_events` | Não implementado | tabelas ausentes |
| Modelo micro aprovado | Não implementado | ainda sem artefato/gate |
| Feature flag Tactical | Não implementada | rollout futuro |

## 6. Comparação: imagem, código e plano

| Tema | Explicação visual | Código atual | Tactical planejado |
|---|---|---|---|
| Macro | Rastro apresentado como SP500 | `P_up` usa cesta multivariada | contexto/filtro de regime |
| Preço | linha branca com markers | preço real + NWE + GEX | região, pullback e invalidação |
| Pair Spread | distância entre “ativo e macro” | target versus um fator ativo | evidência candidata |
| Compra | Z abaixo de `-2` | Pair Signal abaixo de `-1,5` por padrão | threshold calibrado |
| Venda | Z acima de `+2` | Pair Signal acima de `+1,5` por padrão | threshold calibrado |
| Macro forte/fraco | níveis `60/40` | divergência usa `55/45` | regra configurável/gate |
| Repetição | nova venda após normalização | marker reaparece após `neutral` | cooldown explícito |
| Confirmação | marker parece operacional | marker indica afastamento | estado `CONFIRMADO` separado |
| Invalidação | descrita apenas como retorno à média | ausente | macro, micro e estrutural |
| Persistência | não mostrada | snapshot recalculado | eventos idempotentes |
| Barra fechada | não explicitada | barra real, possivelmente em formação | obrigatória |

## 7. Conflitos que precisam ser resolvidos

### 7.1 Threshold operacional versus threshold desenhado

`TVPairwiseZScoreChart` desenha linhas fixas em `±2`, mas `pair_signal()` usa
`PAIR_THRESHOLD = 1.5`, salvo configuração no banco.

Consequência: o backend pode emitir `P VENDA` em `z=+1,6` enquanto a curva ainda não
alcançou visualmente a linha marcada como “venda +2”. Isso quebra a explicabilidade.

**Decisão recomendada:** o backend deve expor `pair_threshold` no contrato e o gráfico
deve desenhar o valor efetivamente usado pelo sinal. A documentação não deve fixar `±2`
se o runtime estiver configurado com outro valor.

### 7.2 Threshold macro da imagem versus divergência real

A imagem usa `60/40`; o engine usa `55/45` para determinar divergência macro-preço.

**Decisão recomendada:** separar visualmente “zona de convicção do P_up” de “threshold da
divergência” ou usar uma configuração única enviada pelo backend.

### 7.3 “Macro” multivariado versus par ativo

O Pair Spread não é calculado contra a linha `P_up`. O target é hedgeado contra apenas um
fator dinâmico. Para WIN, pode ser WDO; para WDO, pode ser DI1; para ativos globais, pode
ser outro índice, moeda ou fator.

**Decisão recomendada:** trocar a frase “Pair Spread mede a distância entre o ativo e o
macro” por “Pair Spread mede a distância entre o ativo e seu hedge ativo; o `P_up` fornece
um contexto macro separado”.

### 7.4 Barra real versus barra fechada

O engine evita markers em ghost e pré-mercado, mas isso não prova que a barra real esteja
fechada. O collector pode reescrever a barra mais recente durante sua formação.

**Decisão recomendada:** o Tactical só deve avançar estado e persistir evento após o
fechamento determinístico da barra. Para a interface atual, deve-se declarar que markers
da borda direita podem ser provisórios até o candle seguinte.

### 7.5 Dívida documental do Pair Z-Score

A nota de design ainda contém fórmula obsoleta com `√t` e chama a janela de “20 sessões”
em um ponto, embora o runtime use Z centrado, sem `√t`, sobre 20 barras.

**Decisão recomendada:** tornar `backend/irai/zscore.py` a referência normativa e corrigir
a nota/tarefa histórica.

Existe uma dívida semelhante no frontend: o comentário de markers em
`TVNweChart.jsx` ainda afirma que os campos estão ausentes, embora a API já propague
`pair_compra`, `pair_venda`, `z_compra_val` e `z_venda_val`. O comentário não altera o
runtime, mas deve ser corrigido para não induzir futuras revisões ao erro.

## 8. Resultado estatístico que altera o plano

O walk-forward ancorado acumulou aproximadamente 673/674 sessões OOS e comparou:

```text
modelo-base: momentum próprio
modelo-aninhado: momentum próprio + features de P_up
```

Nos horizontes de três e seis barras M5 — respectivamente 15 e 30 minutos —, a adição do
macro não atingiu o ganho mínimo operacional de `ΔAUC = +0,02`. Os intervalos de confiança
incluíram zero e vários pontos estimados foram negativos.

Conclusão suportada pela medição:

> O `P_up` não demonstrou edge direcional aditivo de curto/médio prazo nesta cesta e
> nesses horizontes. Ele pode permanecer como contexto ou filtro de regime, mas não deve
> ser tratado como confirmação direcional independente.

Conclusões que a medição **não** suporta:

- que o `P_up` seja inútil como nowcast da sessão;
- que o Pair Spread não possua edge;
- que NWE/VWAP/ATR não possuam valor tático;
- que nenhuma interação condicional entre regime e distorção possa funcionar.

O Pair Signal precisa de avaliação própria, com custos e eventos causais. O teste do macro
não substitui esse backtest.

## 9. Relação correta com o Tactical Layer

A explicação visual deve ser posicionada como uma camada de **detecção e preparação**:

```text
P_up macro
    ↓
contexto/regime: permite, restringe ou bloqueia famílias de setup
    ↓
Pair Spread + divergência macro-preço
    ↓
detectam afastamento relativo; ainda não confirmam uma operação
    ↓
NWE + VWAP + ATR + estrutura do preço
    ↓
definem região, pullback e invalidação
    ↓
regra/modelo micro validado fora da amostra
    ↓
AGUARDANDO → ARMADO → CONFIRMADO ou INVALIDADO/NAO_OPERAR
```

### 9.1 Papel recomendado de cada informação

| Informação | Papel no Tactical |
|---|---|
| `P_up` | contexto ou gate de regime |
| `pair_z` | intensidade/direção do afastamento pairwise |
| `pair_factor` / `pair_beta` | identidade e qualidade do hedge ativo |
| `price_diverge_z` | divergência do preço contra o contexto macro |
| NWE | direção e região local |
| VWAP | referência intrassessão |
| ATR | escala de distância, invalidação e custo |
| preço/retornos | confirmação ou rejeição local |
| stale/ghost/barra aberta | bloqueio de qualidade |

## 10. Reenquadramento dos markers

### Nomenclatura atual

- `P COMPRA` / `P VENDA`: transição do Pair Signal;
- `Z COMPRA` / `Z VENDA`: transição da divergência macro-preço.

### Nomenclatura conceitual recomendada

Enquanto não houver gate econômico e máquina de estados:

- `P DISTORÇÃO −` / `P DISTORÇÃO +`; ou
- manter `P COMPRA/VENDA`, mas adicionar o rótulo explícito “observação, não confirmação”.

A interface não deve usar a mesma força visual de um futuro `CONFIRMADO` para um marker de
afastamento bruto.

## 11. Backtest necessário para a estratégia da imagem

O teste deve reconstruir eventos exclusivamente em barras fechadas e avaliar pelo menos:

1. Pair Signal isolado (`pair_z` cruzando o threshold).
2. Divergência macro-preço isolada.
3. Interseção Pair + divergência macro.
4. Pair condicionado ao regime de `P_up`, sem assumir edge aditivo linear.
5. Pair condicionado à direção/região do NWE.
6. Pair + NWE + VWAP/ATR versus baselines simples de momentum e reversão.

Para cada regra:

- retorno após 3, 6, 10 e 20 barras;
- MFE e MAE;
- custo conservador de WIN/WDO;
- uma entrada por transição, com cooldown definido;
- labels sem atravessar a sessão;
- intervalos de confiança clusterizados por sessão;
- comparação contra taxa-base e regras mais simples;
- análise separada por ativo, hora e identidade do par ativo.

Não otimizar threshold no período final. O threshold escolhido precisa vir de partições
anteriores e ser aplicado de forma imutável no OOS.

### 11.1 Resultado do item 1 (Pair Signal isolado) — 2026-07-15

`scripts/measure_pair_signal_value.py` (NF-01, escopo mínimo) mediu o item 1 da lista acima:
eventos causais (achado X3, só barra fechada) de transição `pair_compra`/`pair_venda`,
entrada no fechamento da barra seguinte à transição (não a própria barra do sinal), custo
de `TARGET_COST_POINTS`, IC95% bootstrap clusterizado por sessão, ~295 sessões OOS por
ativo (~14 meses), Kalman encadeado cronologicamente entre sessões (achado C1-b) com 5
sessões de burn-in excluídas da medição. Passou por 2 rodadas de `/codex-r` antes da
execução — ver commit `496f739`.

```text
WIN$N — nenhum horizonte (h=3/6/10/20, compra/venda/geral) significante: IC95% sempre
        inclui zero, win-rate sempre ~48-50%. MFE/MAE médios (~±350 pts) muito maiores
        que o retorno líquido (~-10 pts): ruído domina.
WDO$N — edge NEGATIVO e estatisticamente significante: compra h=3/6/10/20 e venda h=3,
        todos com IC95% excluindo zero, -0,57 a -1,70 pts líquidos, win-rate 38-42%.
```

Conclusão suportada pela medição:

> O marker Pair Signal isolado, seguido sem filtro adicional, não demonstra edge
> econômico em WIN$N (resultado neutro) e demonstra edge NEGATIVO estatisticamente
> significante em WDO$N. Isso confirma o risco que motivou o NF-01: o marker `P
> COMPRA`/`P VENDA` não é um setup validado — é uma observação de distorção, como já
> tratado na decisão 5 da seção 13.

A limitação C1-a (calibração in-sample, ver LIMITAÇÕES no relatório de saída do script)
tende a viesar o resultado a *favor* do sinal parecer mais mean-reverting do que seria em
tempo real — o resultado neutro/negativo encontrado *apesar* desse viés otimista torna a
conclusão "sem edge" mais robusta, não menos. Ainda assim é um resultado preliminar de uma
única rodada exploratória, sem calibração point-in-time, burn-in mínimo e MFE/MAE apenas
por fechamento de barra (ver seção LIMITAÇÕES completa no JSON/saída do script).

Conclusões que a medição **não** suporta:

- que a interseção Pair + divergência macro (item 3) também não teria edge;
- que o Pair condicionado a regime de `P_up` ou região do NWE (itens 4-5) não teria edge;
- que o custo assumido (`TARGET_COST_POINTS`, nunca derivado de P&L executável real) seja
  exatamente correto.

Itens 2-6 da lista do início desta seção continuam pendentes.

## 12. Estado verdadeiro do projeto em 2026-07-14

### Concluído

- causalidade do eixo temporal e DST do backend;
- correção do skew calibrador/serving;
- medição da contaminação D1;
- walk-forward ancorado do macro;
- migrações idempotentes no boot;
- NWE/VWAP/ATR causal no backend;
- contrato NWE na API e consumo no frontend;
- Pair Z-Score, sinal e markers por transição;
- regressões de fórmula, direção, ghost bars e contrato.

### Parcial ou pendente

- alinhar threshold visual e operacional;
- expor configuração efetiva ao frontend;
- garantir barra fechada para eventos;
- validar economicamente o Pair Signal (item 1/6 do backtest da seção 11 concluído em
  2026-07-15 — ver seção 11.1: sem edge em WIN$N, edge negativo significante em WDO$N;
  itens 2-6 pendentes);
- medir fuso da Axi;
- concluir o gate de WDO em ambiente real;
- atualizar os status desatualizados do plano NWE/consolidado;
- revisar nomenclatura e força visual dos markers.

### Ainda não implementado

- extrator tático compartilhado;
- backtester tático definitivo;
- artefato de modelo micro aprovado;
- máquina de estados canônica;
- persistência `tactical_models` / `tactical_events`;
- contrato Tactical na API/Firebase;
- UI tática sob feature flag;
- rollout individual em Windows/live.

## 13. Decisões aprovadas e promovidas antes da Frente 3

As oito decisões abaixo foram incorporadas ao plano consolidado e à especificação do
Tactical Layer em 2026-07-14:

1. [x] Atualizar o plano consolidado para marcar NWE e migrações como concluídos.
2. [x] Registrar explicitamente que `P_up` é contexto/regime no Tactical, não feature
   direcional aditiva com edge comprovado.
3. [x] Tornar os thresholds uma fonte única do backend e refletir os valores no gráfico.
4. [x] Corrigir a explicação visual para separar `P` pairwise de `Z` macro-preço.
5. [x] Tratar markers atuais como observações de distorção.
6. [x] Executar o backtest específico da estratégia da imagem antes de desenhar
   `CONFIRMADO`.
7. [x] Especificar barra fechada, histerese, cooldown e prioridades da máquina de estados.
8. [x] Só então implementar persistência, API Tactical e feature flag.

O `[x]` indica que a decisão entrou no plano, não que a implementação correspondente já
foi concluída. O andamento de cada entrega deve ser consultado no plano consolidado.

## 14. Critérios para promover a estratégia

A estratégia da imagem só deve ser promovida de “explicação de divergência” para “setup
tático” quando:

- fórmula, threshold e desenho visual forem idênticos;
- eventos forem causais e baseados em barras fechadas;
- o Pair Signal demonstrar valor OOS líquido de custos;
- o ganho superar baselines simples;
- houver amostra suficiente por ativo e regime;
- estados, invalidação e cooldown forem determinísticos;
- o replay histórico e o live usarem o mesmo extrator;
- a ativação ocorrer por ativo e por versão, sob feature flag.

## 15. Referências internas

- `docs/explenation.jpeg`
- `docs/plans/2026-07-13-irai-plano-consolidado.md`
- `docs/plans/2026-07-13-nwe-causal-backend-foundation.md`
- `docs/plans/2026-07-13-irai-tactical-layer-win-wdo.md`
- `.planning/notes/pair-zscore-signal.md`
- `.planning/todos/pending/implementar-pair-zscore-dots.md`
- `backend/irai/zscore.py`
- `backend/irai/engine.py`
- `backend/irai/nwe.py`
- `frontend/src/charts/TVNweChart.jsx`
- `frontend/src/charts/TVPairwiseZScoreChart.jsx`
- `tests/test_pair_zscore.py`
- `tests/test_markers.py`
- `tests/test_premarket.py`
- `tests/test_nwe_causality.py`
- `tests/test_api_nwe_contract.py`

## 16. Validação desta revisão

Antes deste documento, a suíte diretamente relacionada foi executada com:

```bash
pytest -q \
  tests/test_pair_zscore.py \
  tests/test_markers.py \
  tests/test_nwe_causality.py \
  tests/test_api_nwe_contract.py \
  tests/test_premarket.py
```

Resultado: **51 testes passaram e 2 foram ignorados**.

## 17. Revisão independente com Claude

O documento foi submetido ao Claude Sonnet via Claude Code 2.1.209 em modo somente leitura.
O revisor foi instruído a não confiar neste texto como fonte e a confrontar as afirmações
com o código, os testes e os planos relacionados.

### 17.1 Resultado

- nenhum erro factual foi encontrado no escopo verificado;
- a distinção entre `P_up`, divergência macro-preço e Pair Spread foi confirmada;
- foram confirmados o conflito `±2` visual versus `±1,5` operacional e o conflito `60/40`
  versus `55/45`;
- foram confirmados o Z pairwise centrado, sem `√t`, com janela de 20 barras, o reset na
  troca de par e a independência da direção em relação ao sinal de β;
- foram confirmados markers por transição e ausência de garantia de barra fechada;
- foram confirmados NWE causal, migrações idempotentes e ausência das tabelas Tactical;
- a suíte relacionada foi repetida pelo revisor com **51 passed, 2 skipped**;
- foi confirmado que o plano consolidado ainda apresenta status anteriores aos commits de
  NWE e migrações.

### 17.2 Limite declarado pelo revisor

O Claude não verificou diretamente os scripts e artefatos do walk-forward porque a revisão
foi deliberadamente limitada aos arquivos centrais. Os números estatísticos deste documento
continuam fundamentados na seção 3.7 do plano consolidado e nos commits de medição; a revisão
do Claude confirmou apenas que a interpretação não contradiz o plano fornecido. A unidade foi
explicitada como barras M5 para eliminar a ambiguidade entre 3/6 barras e 15/30 minutos.

### 17.3 Melhorias editoriais incorporadas

1. Explicitação dos horizontes em barras M5 e minutos.
2. Evidência precisa de onde `select_active_pair()` vive e onde é consumida.
3. Registro do comentário obsoleto de markers em `TVNweChart.jsx` como dívida documental.
