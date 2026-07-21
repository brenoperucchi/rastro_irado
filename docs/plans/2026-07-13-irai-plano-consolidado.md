# IRAI — Plano consolidado (estado + rota até o Tactical Layer)

**Projeto:** IRAI — Intraday Risk Appetite Index
**Criado:** 2026-07-13 · **Revisado e verificado:** 2026-07-15

**Autoridade:** fonte oficial de status, prioridade, escopo e sequência do projeto

**Consolida:** `2026-07-10-frontend-migration-status-and-forward-plan.md` (plano-mãe),
`2026-07-13-nwe-causal-backend-foundation.md` (Fundação NWE) e
`2026-07-13-irai-tactical-layer-win-wdo.md` (Tactical Layer) e
`2026-07-14-divergence-strategy-vs-tactical-layer.md` (decisões de negócio dos sinais)

## Sumário executivo para a Miqs

O IRAI já possui a base macro, os gráficos, o GEX, os sinais por barra e a Fundação NWE
causal no backend. A infraestrutura quantitativa principal foi corrigida e centralizada.
O projeto entra agora na fase de **validar economicamente as distorções atuais** e preparar
o Tactical Layer, inicialmente para `WIN$N` e, após validação própria, `WDO$N`.

**Onde estamos agora:** causalidade temporal, DST, geometria do calibrador, migrações e
NWE causal foram concluídos. Os próximos gates são unificar os thresholds entre backend e
gráfico, garantir eventos em barra fechada, backtestar Pair/Z com custos, auditar o fuso da
Axi e validar o WDO no ambiente real.

**Destino desta rota:** transformar contexto macro, distorções `P`/`Z` e regiões locais em
estados explicáveis — `AGUARDANDO_PULLBACK`, `ARMADO`, `CONFIRMADO`, `INVALIDADO` e
`NAO_OPERAR` — sem executar ordens. O `P_up` participa como contexto/regime, não como
confirmação direcional curta com edge presumido.

**Definição honesta do produto:** esta rota entrega um sistema causal de pesquisa e decisão,
não a promessa de um robô vencedor. Um resultado em que nenhuma regra supera custos e
baselines — e, portanto, o Tactical permanece em `NAO_OPERAR` — é uma conclusão válida e
útil. A engenharia de produção só avança para hipóteses que sobrevivam ao gate econômico.

### Como ler o plano

| Tipo | Significado neste projeto |
|---|---|
| **BUG** | O sistema atual viola um comportamento já esperado; precisa de correção. |
| **MELHORIA** | Aumenta confiabilidade, desempenho ou manutenção sem criar uma capacidade operacional nova. |
| **NOVA FUNCIONALIDADE** | Entrega uma capacidade que ainda não existe para o operador. |
| **VALIDAÇÃO** | Investigação ou gate necessário; não deve ser apresentado como bug antes de existir evidência. |

### Mapa executivo do escopo

| ID | Tipo | Entrega | Status | Por que importa |
|---|---|---|---|---|
| BUG-01 | **BUG** | Alinhar fatores B3 sem lookahead de 6h | ✅ Concluído | Remove contaminação do histórico e do backtest. |
| BUG-02 | **BUG** | Tornar o offset Tickmill/B3 sensível ao DST | ✅ Concluído | Evita desalinhamento sazonal no backend. |
| BUG-03 | **BUG** | Remover `-6h` fixo do eixo BRT no frontend/Firebase | ✅ Concluído (caminho local) | Evita rótulo incorreto a partir de 01/11/2026. Firebase/mobile segue como Fase 7. |
| BUG-04 | **BUG** | Eliminar NWE não causal e divergência browser/backend | ✅ Concluído | NWE causal é calculado no backend e consumido pela API/UI. |
| MEL-01 | **MELHORIA** | NWE/VWAP/ATR causal como fonte única no backend | ✅ Concluído | Replay, API, Firebase e UI compartilham a mesma fonte. |
| MEL-02 | **MELHORIA** | Bootstrap idempotente das migrações do banco | ✅ Concluído | `migrate_to_head()` roda no boot da API e collectors. |
| MEL-03 | **MELHORIA** | Threadpool, single-flight e cache tático | ⏳ Planejado | Mantém API e mobile responsivos com os cálculos futuros. |
| MEL-04 | **MELHORIA** | Governança de artefatos, drift e despromoção | ⏳ Planejado | Impede que uma regra aprovada permaneça ativa depois de perder validade. |
| NF-01 | **NOVA FUNCIONALIDADE** | Backtester point-in-time de distorções `P`/`Z` | 🚧 NF-01A e braço executável concluídos | Markers atuais não têm edge promovível; challengers ainda em avaliação. |
| NF-02 | **NOVA FUNCIONALIDADE** | Regra/modelo micro de 15 minutos com walk-forward | ⏳ Condicionado ao NF-01 | Só existe se acrescentar valor OOS sobre regras simples. |
| NF-03 | **NOVA FUNCIONALIDADE** | Máquina de estados e eventos táticos | ⏳ Condicionado ao gate | Traduz somente hipótese aprovada em estados compreensíveis. |
| NF-04 | **NOVA FUNCIONALIDADE** | API, Firebase e UI Tactical por feature flag | ⏳ Condicionado ao NF-03 | Leva o sinal validado ao operador sem ativação prematura. |
| VAL-01 | **VALIDAÇÃO** | Recalcular métricas de WIN/WDO após BUG-01 | ✅ Concluído (§3.6/§3.7) | Contaminação real (7–17 pp); macro não agrega valor tático em h=3/h=6. |
| VAL-02 | **VALIDAÇÃO** | Medir fuso Axi e conferir modelo macro do WDO | ⚠️ Pendente | Decide se o piloto pode avançar de WIN para WDO. |
| VAL-03 | **VALIDAÇÃO** | Replay/live final no Windows com MT5 | ⚠️ Gate final | Linux não valida coleta nem comportamento live dos terminais. |
| VAL-04 | **VALIDAÇÃO** | Realismo econômico do NF-01 | ⏳ Gate de aceite | Exige preço executável, custos, baselines, rollover declarado e controle de múltiplos testes. |
| VAL-05 | **VALIDAÇÃO** | Shadow live com ledger de decisões | ⏳ Após NF-04 | Mede o desvio entre backtest, publicação e preço realmente disponível sem enviar ordens. |
| VAL-06 | **VALIDAÇÃO** | Comparar e avaliar o `P_up` do WIN (Miqueias × v1 × v2) | 🚧 Em execução | Separa paridade visual de qualidade OOS antes de usar o `P_up` como gate. |

### Escopo da primeira entrega tática

**Entra na v1:** piloto inicial em WIN; backtest de Pair Spread e divergência macro-preço;
`P_up` como contexto/regime; NWE/VWAP/ATR como regiões; regra ou modelo micro de 15 minutos
somente se aprovado; estados e eventos em barras fechadas; API/Firebase e interface sob
feature flag; validação do WDO e shadow live antes de ativá-lo.

**Fica para depois:** `P_micro_30m`, zonas estatísticas de realização e payload tático em
cada barra da série; expansão aos 20 ativos; polimento secundário de frontend.

**Continua fora do produto:** execução automática de ordens, lote, copy trading, Volume
Profile/book e qualquer promessa de resultado operacional.

### Sequência de entrega

```text
Base temporal + migrações + NWE causal concluídos
        ↓
Thresholds canônicos + regra de barra fechada concluídos
        ↓
NF-01: extrator/backtester Pair/Z point-in-time
        ↓
VAL-06: capturar e comparar Miqueias × v1 × v2 no WIN
        ↓
VAL-04: executabilidade, custos, baselines e auditoria econômica
        ↓
Gate: parar | aprovar regra simples | autorizar NF-02
        ↓
Validação do WDO + decisão sobre modelo micro 15m
        ↓
Estados/eventos + API/Firebase/UI desligada
        ↓
Gate histórico → replay/live Windows → shadow live → ativação individual
```

### Regras de negócio aprovadas para o Tactical

1. `P_up` é **contexto ou gate de regime** no horizonte tático; não é confirmação
   direcional curta por si só.
2. Marker `P` representa distorção entre o ativo e seu hedge pairwise ativo.
3. Marker `Z` representa divergência entre preço e contexto macro multivariado.
4. Markers atuais são **observações de distorção**, não estados `CONFIRMADO`.
5. Threshold operacional e linha desenhada devem vir da mesma configuração do backend.
6. Estados e eventos persistidos só avançam em barra M5 determinísticamente fechada.
7. Estratégia ou modelo reprovado fica em `NAO_OPERAR`; diagnósticos podem continuar
   visíveis, mas não chegam a `ARMADO` ou `CONFIRMADO`.
8. `P_micro_30m` e zonas estatísticas de realização ficam fora da v1.
9. Nenhum setup é promovido sem resultado OOS líquido de custos e superior a baselines
   simples.
10. O instante de decisão e o preço de entrada precisam ser negociáveis: o preço usado para
    formar ou confirmar um sinal não pode ser reutilizado como fill se já não estava
    disponível quando o sinal ficou pronto.
11. Regra transparente aprovada pode seguir diretamente para NF-03; NF-02 não é etapa
    obrigatória e só existe se demonstrar ganho incremental OOS.
12. Hipóteses exploratórias não promovem produção no mesmo conjunto em que foram descobertas;
    precisam ser registradas e reavaliadas em período intocado.
13. Semelhança com a curva do Miqueias é diagnóstico de paridade, não critério de promoção.
    A versão do `P_up` deve vencer por qualidade OOS no objetivo diário e, separadamente,
    demonstrar utilidade econômica quando usada como gate da regra tática.

### Resultado esperado e saídas legítimas do gate econômico

O valor do projeto não depende de o NF-01 encontrar uma estratégia aprovada. O gate abre
três rotas explícitas:

```text
NF-01 + VAL-04
  ├─ nenhuma regra supera custos/baselines
  │    → manter diagnóstico P/Z
  │    → Tactical permanece NAO_OPERAR
  │    → não implementar NF-02/NF-03 para essa hipótese
  │
  ├─ regra transparente demonstra edge robusto
  │    → pular NF-02
  │    → implementar NF-03 com a regra simples
  │
  └─ existe hipótese econômica e modelo acrescenta valor OOS
       → executar NF-02
       → implementar NF-03 somente após aprovação
```

### Quadro de execução vigente

| Ordem | Bloco | Estado | Gate de saída |
|---:|---|---|---|
| 1 | Fundação temporal, NWE e migrações | ✅ Concluído | Regressões causais e contrato único |
| 2 | Thresholds canônicos e barra fechada | ✅ Concluído | Runtime/gráfico iguais; barra mutável sem evento |
| 3 | NF-01A — extrator e backtester | ✅ Concluído e revisado | Replay point-in-time reproduzível |
| 4 | VAL-06 — paridade e qualidade do `P_up` no WIN | 🚧 Em execução | Captura comum e avaliação OOS sem declarar vencedor por semelhança |
| 5 | NF-01B / VAL-04 — contrato econômico | 🚧 Braço executável concluído | Pair fixo comparável e regra local com/sem IRAI pendentes |
| 6 | Decisão regra simples versus NF-02 | ⏳ Condicionado | Evidência OOS líquida e estável |
| 7 | NF-03 — estados/eventos | ⏳ Condicionado | Somente hipótese aprovada chega a `CONFIRMADO` |
| 8 | NF-04 — distribuição com flag desligada | ⏳ Condicionado | Paridade API/Firebase/UI e observabilidade |
| 9 | VAL-05 — shadow live | ⏳ Condicionado | Backtest versus decisão/preço live reconciliados |
| 10 | Ativação individual WIN, depois WDO | ⏳ Gate final | Windows/live e governança aprovados |

### Princípios econômicos e de produto

1. **Evidência antes de superfície de produto.** Persistência, UI tática e integrações não
   devem amadurecer uma hipótese que ainda não pagou custos no OOS.
2. **Abstenção é uma decisão.** `NAO_OPERAR` é resultado de primeira classe, não falha do
   modelo.
3. **Simplicidade vence por padrão.** Se regra e modelo entregarem resultado equivalente,
   promover a regra.
4. **Contexto pode valer como filtro.** O IRAI deve ser testado não só como previsor, mas
   como gate capaz de evitar operações ruins de uma regra local já definida.
5. **Backtest e live compartilham semântica.** `bar_end`, disponibilidade, publicação,
   primeiro preço negociável e fill são instantes diferentes e precisam ser registrados.
6. **Aprovação é reversível.** Drift, degradação live ou mudança de custo podem devolver uma
   regra/modelo a `experimental` e o runtime a `NAO_OPERAR`.

Detalhes e evidências: [`2026-07-14-divergence-strategy-vs-tactical-layer.md`](./2026-07-14-divergence-strategy-vs-tactical-layer.md).

**Método desta versão:** três revisores independentes (`deep-reasoner`, `fable-reasoner`,
`codex`) analisaram o plano em paralelo, cegos entre si; depois **cada achado foi
verificado contra o código e contra o banco de produção**. Todo achado abaixo carrega um
**selo de confiança** — a versão anterior deste documento era união sem verificação e
apresentava palpite não-checado com a mesma cara de fato.

> **Como ler a auditoria histórica:** as seções 1–3 preservam a descoberta e a evidência
> dos problemas no momento da revisão. Quando um achado já foi absorvido ou resolvido, seu
> título informa isso. Para situação e próxima ação, prevalecem o mapa executivo acima e a
> sequência da seção 4.

| Selo | Significado |
|---|---|
| **CONFIRMADO** | Verificado por mim no código ou no banco real. É fato. |
| **CONSENSO** | ≥2 revisores, evidência `arquivo:linha`, não re-verificado por mim. |
| **SOLO** | 1 revisor, ninguém contestou nem confirmou. **Não vira ação sem checar.** |
| **REFUTADO** | Verificado e derrubado. Registrado para não voltar. |

> **Leitura técnica em 30 segundos.** A tri-review encontrou um lookahead de 6 horas nos
> fatores B3 (D1) e um shift incompatível com o DST (A6). Os dois bugs já foram corrigidos
> no backend pelo commit `16d4661`. A medição walk-forward, o eixo BRT local, as migrações
> e a Fundação NWE também foram concluídos. Permanecem como gates a auditoria do relógio
> Axi, o caminho Firebase/mobile, a validação própria do WDO e o replay/live no Windows.

---

## 1. Onde estamos

### 1.1 Concluído (verificado no código)

| Bloco | Status | Evidência |
|---|---|---|
| **Parte 1 — migração de charts** (Recharts → lightweight-charts, 5/5) | ✅ | bundle 752→419 kB; `recharts` removido (`eae208e`) |
| **GEX — gamma walls IBOV → WIN$N e WDO$N** | ✅ | WIN usa bundle oficial B3/BCB causal com paridade live↔backfill; WDO preserva BDI/MT5; timer 09:10 após primeira M5 (`3155c98`) |
| **Pacote A — destravar o V2** (Fases 2, 3, 4 + B1) | ✅ **3/3** | `01e0b9b`, `28ecf2a`, `e5f513f`, `82e3727`, `ceec25d` |
| **Pacote B — markers de sinal por barra** | ✅ | `e235c03` + fixes `0236f19`, `97f2cb7` |
| **Fase 8.2 — corridas de request** | ✅ *(bônus)* | guard de `reqId` em `fetchSeries`, dentro de `39e6822` |

### 1.2 Em aberto

Tudo o que restava do Pacote C do plano-mãe (Fases 1, 5, 6, 7) e o item "cesta do WDO"
do Pacote B foi absorvido como pré-requisito dos planos novos. **Este documento é a rota
única.** Somaram-se a eles os achados da tri-review, abaixo.

---

## 2. As quatro frentes (ordem corrigida)

```
  [ FRENTE 0 ]  ✅ CONCLUÍDA
  Causalidade do eixo temporal (engine.py:471-473)
  D1 (lookahead 6h nos fatores B3) + A6 (DST) — a MESMA função
        |
        v
  [ FRENTE 1 ] ✅             [ FRENTE 2 ] ⇉               [ FRENTE 3 ] ⏳
  Fundação NWE causal  ─────> Bloqueios de ambiente ─────> Tactical Layer
  concluída                   parcialmente concluídos       WIN primeiro, WDO após gate
```

A Frente 0 não existia na versão anterior do plano e foi executada primeiro porque
**consertar o NWE e deixar o `P_up` com lookahead produziria exatamente a métrica inflada
que o plano diz querer evitar** — por um caminho que o achado C1 não cobria.

---

## 3. Achados verificados

### 3.0 FRENTE 0 — os dois bugs do eixo temporal (mesma função, consertar juntos)

#### D1 — Lookahead de 6 horas nos fatores domésticos · **CORRIGIDO** · era CRÍTICO

> Diagnóstico histórico abaixo: descreve o comportamento anterior ao commit `16d4661`.

**Afirmação.** `engine.py:472` aplica o shift de +6h **apenas ao símbolo do target**
(`if is_b3 and d["symbol"] == data_target`). Um fator que também seja da B3 permanece no
eixo BRT e passa a ser lido **6 horas à frente** do target.

**Como verifiquei.** A cesta viva (query em `asset_models`, banco de produção) contém o
par cruzado nos dois sentidos:
- `WIN$N` ← `WDO$N`, `DI1$N` (+ 6 fatores globais) — **2 de 8 são B3**
- `WDO$N` ← `WIN$N`, `DI1$N` (+ 6 fatores globais) — **2 de 8 são B3**

Simulei o cursor da engine (`engine.py:678-684`) sobre a sessão real de 2026-07-10:

| eixo | WIN real | WDO consumido | defasagem |
|---|---|---|---|
| 15:00 | 09:00 BRT | 15:00 BRT | **+6h — futuro** |
| 18:00 | 12:00 BRT | 18:00 BRT | **+6h — futuro** |
| 19:00 → fim | 13:00 BRT → | **18:25 (close)** | congelado no fechamento da sessão |

**Nuance decisiva (não estava em nenhum revisor).** O vazamento depende de quais barras
já existem no banco no momento do compute:
- **Barra corrente / borda direita: CORRETA.** As barras futuras do fator ainda não
  existem, o cursor para no lugar certo. **O sinal ao vivo, agora, não está errado.**
- **Histórico da curva: CONTAMINADO.** Cada barra passada lê o fator no tempo presente.
- **Sessões completas — o que um backtest replaya: TOTALMENTE contaminadas**, com o fator
  congelado no close a partir de ~13:00 BRT.

**Impacto.** Acurácia histórica inflada; `price_diverge_z` e os markers de par do WIN
gerados sobre um fator adiantado; e, se não for corrigido, o Tactical Layer treinaria
`P_up` sobre uma feature que vê o futuro — o gate aprovaria um modelo que desaba ao vivo.

**Emenda.** Trocar o guard por shift **por origem do símbolo** (todo símbolo XP/B3 → +6h;
Tickmill → 0), não por `== data_target`. Regressão: para target B3 com fator B3, a barra
do fator consumida no eixo `t` tem de ter o mesmo instante de parede que a do target.

#### A6 — DST: o shift fixo de +6h era cego a horário de verão · **CORRIGIDO NO BACKEND** · era CRÍTICO

> Diagnóstico histórico abaixo: o backend já usa offset sensível à data; a pendência atual
> ficou restrita à confirmação do contrato Firebase/mobile, pois o frontend local também
> já usa o offset dinâmico.

**Confirmado empiricamente no banco de produção** (o `data/irai.db` local está **vazio** —
0 tabelas —, o que fez dois revisores marcarem isto como "não verificável"). Método:
correlacionei retornos M5 de WIN$N (XP/B3, BRT fixo — o Brasil não tem DST) contra US500
(Tickmill), buscando mês a mês o lag que maximiza a correlação. O relógio do servidor se
revela sozinho:

| Período | Lag medido | Servidor Tickmill |
|---|---|---|
| jun–out/2025 | 6h | UTC+3 |
| **nov/2025 – fev/2026** | **5h** | **UTC+2** |
| mar–jul/2026 | 6h | UTC+3 |

Transições exatas (granularidade semanal): **02/11/2025** e **08/03/2026**.

**O plano anterior errava duas coisas:**
1. **As datas.** Dizia "~26/10/2025 a ~29/03/2026" — as datas do DST **europeu**. As reais
   são as do DST **americano**. Quem implementasse seguindo o plano erraria uma semana em
   cada ponta.
2. **O enquadramento.** Tratava isso como contaminação da janela de treino. **É bug vivo:**
   hoje (jul/2026) o `+6h` está correto — por isso ninguém notou — mas a **próxima virada é
   01/11/2026**, daqui a ~3,5 meses, provavelmente no meio do rollout do Tactical.

**Emenda.** Shift **date-aware** por tabela de offsets derivada do dado (não por calendário
europeu hardcoded), na **mesma função** do D1. Regressão cobrindo uma data de inverno e uma
de verão.

### 3.1 CRÍTICOS restantes

#### C1 — O backtest tático não é point-in-time para o macro · **CONSENSO** (3/3) · CRÍTICO
Duas pernas, ambas com evidência: (a) `calibrate_universal.py:49-52` carrega `market_bars`
**sem filtro de data** e não existe parâmetro de cutoff/split — o `P_up` replayado é
in-sample em qualquer partição do walk-forward; (b) `engine.py:618-622` só restaura o
estado do Kalman se `state_ts < session_start` → replay de sessão antiga parte **frio**, a
sessão viva parte **quente**. Regimes diferentes.
**Emenda.** Replay cronológico com estado Kalman em memória atravessando sessões; cada fold
usando um artefato macro **imutável** com cutoff anterior à janela de teste. ⚠️ Versionar só
`model_params` **não basta** — `calibrate_universal.py:337` sobrescreve `factors`/
`factor_labels` in-place em `asset_models` (SOLO/codex, não re-verificado).
⚠️ Com o D1 vivo, "declarar o viés e endurecer o gate" **não é saída aceitável**: o viés não
é de magnitude desconhecida, é um lookahead de 6h.

#### C2 — Máquina de estados contraditória · **RESOLVIDO NA ESPECIFICAÇÃO** · era ALTO
Confirmado na versão anterior dos docs: o resumo permitia `ARMADO` a modelo reprovado, o
JSON usava `ARMED`, `CONFIRMADO` não tinha transição de saída e o cooldown não tinha
precedência.
**Severidade rebaixada** por 1 revisor e aceito: é ambiguidade de documento (custo de
reescrita), não dado corrompido. Um sub-item foi **corrigido**: "thresholds por classe não
são espelhos" → o certo é "**não estão especificados**" (é omissão, não assimetria provada).
**Resolução documental de 2026-07-14.** O Tactical agora usa enums canônicos em português,
modelo reprovado retorna `NAO_OPERAR`, as prioridades e saídas estão tabeladas e o cooldown
exige três barras fechadas **e** saída/reentrada na região. A regressão de implementação
continua pendente porque a máquina ainda não foi codificada.

#### X3 — Eventos táticos podem nascer de barra em formação · **CONFIRMADO** · CRÍTICO
`collector.py:98-101` usa `INSERT OR REPLACE` na barra mais recente, reescrevendo seu OHLCV a
cada ciclo. Sem flag de fechamento, uma transição de estado pode ser persistida a partir de
uma barra que ainda vai mudar — e a chave idempotente `(target, timestamp, model_version,
new_state)` **preserva o evento obsoleto**.
**Emenda.** Estados e eventos só avançam em **barra fechada**. Persistir `bar_closed` ou
excluir deterministicamente a última barra até a abertura da próxima.

### 3.2 ALTOS

#### A1 — Fórmula do envelope NWE ambígua · **CONFIRMADO** · ALTO
Verifiquei cada sub-afirmação: o MAE usa o **centro contemporâneo de cada barra**
(`App.jsx:375` → `Math.abs(allPrices[t-i] - center[t-i])`) — um segundo implementador
naturalmente usaria `center[t]`. "Barra válida" **tem** definição no código, só não no doc
(`App.jsx:347` → `!d.is_ghost`, distância do kernel em índice de barra). O warm-up vive **no
endpoint** (`main.py:387-392`, `LIMIT 95`) e a engine **não o carrega** (grep vazio) — mover
o NWE para a engine sem levar a carga quebra a paridade. A âncora é `win_open` em %
(`App.jsx:401/415/431`). O número **"~188 closes" confere** (95 + 94: cada resíduo depende do
próprio centro).
**Emenda.** Declarar o `computeNWE` como referência normativa — **com a ressalva do D2**.

#### D2 — A emenda do A1, como escrita, canonizaria um lookahead · **SOLO** (deep) · ALTO
O `computeNWE` **lê a barra `t+1`** (`App.jsx:444-445`: `nextSlope`/`isTransition`), e o doc
da Fundação **proíbe** exatamente isso ("nenhuma inspeção da inclinação de `t+1`").
Declará-lo "normativo fórmula a fórmula" faria a regressão de causalidade testar contra uma
referência que viola a própria regra.
**Emenda.** Normativo **para** centro/envelope/âncora (`App.jsx:355-380, 397-441`);
explicitamente **não-normativo** para `isTransition`/`wasTransition`/`nwe_up`/`nwe_down`
(`:444-450, :471-472`), que devem ser reescritos causalmente ou descartados.

#### A2 — `nwe_slope` colide com campo existente · **CONFIRMADO, e é colisão TRIPLA** · ALTO
Não são duas semânticas, são **três**, todas com o mesmo nome:
- `main.py:299` → float, espaço de **retorno %**
- `App.jsx:421` → **booleano** (ramo ghost)
- `App.jsx:469` → float, espaço de **preço**

**Correção de número:** o plano dizia "~1e-6" — **falso**. Isso é a precisão do `round(...,6)`,
não a magnitude. Sai do documento.
**Emenda.** Campo novo `nwe_slope_price`; e a migração do frontend elimina o booleano de
`App.jsx:421` na mesma passada, senão o campo do backend colide com o local no mesmo objeto.

#### A5 — Purge de 6 barras insuficiente · **RESOLVIDO NA ESPECIFICAÇÃO** · era ALTO
Para o *label* (3 e 6 barras), purge=6 basta. O furo é específico: MFE/MAE são medidos até
**20 barras** (`tactical:94`) e alimentam as zonas de realização (`:199-200`) → entram na
seleção. Falta também a regra "labels nunca cruzam a fronteira da sessão" (**ausência
confirmada** no doc).
**Resolução documental de 2026-07-14.** Purge = `max(horizonte_label, horizonte_MFE)` =
**20**; label truncado na fronteira da sessão. Zonas de realização ficaram fora da v1.

#### A7 — Gate estatístico frágil · **RESOLVIDO NA ESPECIFICAÇÃO** · era ALTO
ECE ≤ 0,10 sobre ~100 confirmações é enviesado por seleção (a amostra é truncada pelo próprio
threshold), e o baseline do Brier **nunca é definido**. "Ridge multinomial" não identifica um
modelo: `RidgeClassifier` não emite probabilidades nativamente.
**Resolução documental de 2026-07-14.** O Tactical exige regra transparente como baseline,
define regressão logística L2 como modelo probabilístico inicial e separa calibração OOS
bar-a-bar do gate econômico de eventos.

#### D4 — Skew treino/serviço: calibrador e engine ancoram o fator em pontos diferentes · **CONFIRMADO E CORRIGIDO** · era ALTO
Para alvos B3, o calibrador recortava os fatores globais em 09:00–18:00 **EEST**
(`calibrate_universal.py:56-67` na versão antiga), enquanto a engine ancorava o fator na
**00:00** da janela (`engine.py:566-570`). Pesos e σ eram ajustados sobre uma variável e
aplicados sobre outra. Atingia **exclusivamente WIN$N e WDO$N** — os dois ativos do piloto.
✅ **Corrigido no commit `fd6ec34`** — `load_daily_returns` do calibrador agora delega a
`serving_daily_returns` em `backend/irai/market_geometry.py`, o mesmo módulo que a engine
usa (`align_market_bars`/`return_from_open`), fechando o skew. **Verificado**: `fd6ec34`
precede `bcab7a1`/`03cc4ce`/`b93cbfe`/`4684797` na história — os Gates 2/3/3b e o
walk-forward já rodaram com a geometria unificada, não estão contaminados por este item.

#### D3 — A Frente 1 não é executável no Linux: o banco local está vazio · **CONFIRMADO** · ALTO
`data/irai.db` local tem **0 bytes / 0 tabelas** (está no `.gitignore`). Paridade, ghost, gap,
fuso e a auditoria DST precisam de barras reais.
**Contornado nesta revisão:** usei o banco de **produção** via SSH (host WSL, 806 MB) — é o
caminho. **Emenda:** Etapa 0.5 — obter um snapshot do `irai.db` de produção antes da Frente 1.

### 3.3 MÉDIOS · **CONSENSO** salvo indicação

- **Curvatura do NWE** — o Tactical lista como feature; a Fundação (fonte única) não a emite.
- **Cache não cobre o tático** — chaves são `(target,date,version)` (`main.py:36-37`); aprovar
  modelo ou trocar threshold devolveria payload velho.
- **Payload Firebase cresce** — `firebase_sync.py:83-86` faz PUT do JSON **inteiro** a cada 30s
  com timeout de 15s. Propagar tactical só no overview + `tactical_events`.
- **`/api/irai/current` default `v1`** · **CONFIRMADO** (`main.py:428`) — contrato ambíguo nos
  caminhos que o Tactical usará.
- **`kalman_state` já está no SCHEMA** · **CONFIRMADO** (`db.py:91`) — a "correção factual" do
  plano estava certa: quem falta é só `divergence_config` (`db.py:150`, só via `__main__`).
- **VWAP/ATR subespecificados** (A3) · severidade **rebaixada para MÉDIO** por 2 revisores: é
  lacuna de doc, não bug vivo, e o piloto (B3) tem `real_volume`.
- **API não-bloqueante** (A4) · **CONFIRMADO** (`main.py:243-248`, laço síncrono dentro de
  `async def`; grep por threadpool = zero) · severidade **MÉDIO** — degrada latência, não
  corrompe número.
- **O(n²) acidental** — **não é bug vivo** (hoje `get_center` é chamado 2×). Vira **requisito**
  sobre o código futuro, não achado.
- **`real_volume=0` no histórico antigo** (F3) · **SOLO** (fable) — a coluna entrou por migração
  com `DEFAULT 0`; barras antigas podem ter 0, encolhendo a janela de features. **Não verificado.**
- **Paridade Firebase** (F5) · **SOLO** (fable) — o app lê `data.history[target]` que o
  `firebase_sync` nunca envia → o mobile computa NWE **sem warm-up hoje**. A regressão de
  paridade deve incluir o caminho Firebase.

### 3.4 REFUTADOS (registrados para não voltarem)

- **"Pode não haver 160 sessões para o walk-forward"** (fable) — **FALSO.** O banco de produção
  tem **138.398 barras de WIN$N desde 2021-07-12** (~5 anos) e 300.444 de US500. O que limita a
  janela é a contaminação (D1/A6), **não** a profundidade.
- **"~1e-6" como magnitude do `nwe_slope`** — falso (é a precisão do arredondamento).
- **"O(n²) é um bug"** — falso; é uma restrição a impor no código novo.

### 3.5 Cortes para a v1 do Tactical (mantidos — os três revisores concordaram)

Zonas de realização (sem amostra condicional no momento do gate), `P_micro_30m` (dobra a
superfície de calibração e pode estrangular as confirmações) e `tactical` por barra na série
(reconstrói-se de `tactical_events`). **Ressalva não admitida pelo plano anterior:** com a
conferência da cesta do WDO bloqueada por ambiente, **a v1 é de facto WIN-only** — dizer isso
evita planejar uma calibração de WDO que não pode ser aprovada.

### 3.6 VAL-01 — quanto o D1 contaminava o sinal · **MEDIDO** (`scripts/measure_d1_inflation.py`)

Replay A/B das mesmas 120 sessões (jan–jul/2026) sobre o banco de produção: braço A com o
bug restaurado por monkeypatch, braço B com o HEAD corrigido. Duas rodadas — a primeira foi
**reprovada na revisão** (media v1, e a métrica terminal é cega ao bug por construção); a
segunda mede v2, que é o que a produção serve.

**O `P_up` que o operador via estava errado** — |P_up^A − P_up^B|, em pontos percentuais:

| Alvo | Faixa | Média | p95 | Máx |
|---|---|---|---|---|
| WIN$N | todas as barras | 7,3 | 21,7 | 55,4 |
| WIN$N | antes das 13h BRT | 10,1 | 26,3 | 55,4 |
| WDO$N | todas as barras | 11,5 | 38,4 | **92,0** |
| WDO$N | antes das 13h BRT | **17,0** | 46,4 | 92,0 |

**A acurácia forward estava inflada** (bootstrap pareado por sessão; todos os IC95% acima de
zero — significantes):

| Alvo | h=3 | h=6 | h=20 |
|---|---|---|---|
| WIN$N | +2,94 pp | +4,17 pp | **+7,25 pp** |
| WDO$N | +2,74 pp | +3,74 pp | **+7,16 pp** |

**Rótulo honesto do que isto mede:** D1 em isolamento, no código de hoje, com v2 **sem
memória entre sessões** (o `kalman_state` do snapshot é posterior à janela e nunca é
restaurado), em dias completos. Por isso é provavelmente um **PISO** da contaminação real —
no live o estado encadeia dia após dia, também contaminado.

#### ⚠️ O que este resultado NÃO autoriza concluir

O braço corrigido tem acurácia forward de **49–52%** — o que parece dizer que, sem o
vazamento, o `P_up` não prevê nada. **Não conclua isso.** A revisão derrubou o salto:

1. **Não é o objetivo de treino.** O `P_up` é um *nowcast* da direção da **sessão**
   (open→close); medi-lo contra o retorno das próximas 3–20 barras é a pergunta errada.
   ~50% é o esperado para um sinal contemporâneo.
2. **Os pesos do braço B são da era do vazamento** — e geometricamente desalinhados com o
   serving (o skew do D4). B é um **piso** do que um modelo recalibrado alcançaria.
3. **As features que o Tactical realmente propõe não foram medidas** (ΔP_up, persistência,
   divergência). Podem ter valor na cauda mesmo com o nível a 50% incondicional.
4. **"B ≈ 50%" nem foi testado** contra a taxa-base (empates contam como baixa, então a
   taxa-base não é 50%).

**O que MORREU:** o uso ingênuo — "nível de `P_up` binarizado em 50 como viés direcional
forward". Todo o edge aparente disso era vazamento.

#### Gate executado antes da revisão do Tactical

A Frente 3 foi condicionada às três medições abaixo, executadas antes da especificação
revisada de 2026-07-14:
1. **Recalibrar** WIN/WDO pós-fix (e corrigir o skew de relógio do D4 no calibrador).
2. **Brier / log-loss + curva de calibração** do nowcast contra o rótulo de sessão, por hora
   do dia, vs. um baseline trivial ("sinal do retorno de sessão até agora"). Se não bater o
   baseline nem nisso, aí sim o macro layer está em apuros.
3. **IC (Spearman) e análise de decis** de `(P_up−50)`, `ΔP_up(k)` e da divergência contra o
   retorno forward, com IC clusterizado por sessão; e um **modelo aninhado** (momentum
   próprio vs. + features de P_up). Se ΔAUC ≈ 0, **aí** o macro sai do plano.

### 3.7 VAL-01 (item 3) — walk-forward ancorado, veredito final · **MEDIDO E CONCLUÍDO**
(`scripts/run_walkforward_macro.sh`, `scripts/measure_tactical_gate3.py`,
`scripts/aggregate_walkforward_macro.py`)

O Gate 3b (`b93cbfe`) tinha só 49 sessões OOS — sem poder estatístico para detectar
ΔAUC=+0,02 a 80% (são precisas ~690 sessões em h=3). Cestas incumbentes nunca chegariam lá
(o `iSharesCurrencyBond+` do WDO só existe desde 2025-05-27). A saída foi uma cesta de
história longa (só fatores com ≥1000 sessões, sem iShares/USDCAD/USDCHF) com 8 folds
ancorados (cutoffs avançam; treino rolante fixo de 120 sessões por fold — não cresce, ver
achado A#2 da tri-review de 2026-07-14 — vs. o default de 252 sessões do Gate 3 legado,
caminho de medição não usado neste walk-forward), acumulando o modelo aninhado (momentum
próprio vs. features de P_up) via cross-fit.

Uma primeira rodada tinha um bug real: `candidate_sessions()` exigia fechamento às 17:55,
mas grandes trechos de `WIN$N` fecham às 17:50 entre 2021-2023 — sessões completas eram
descartadas e as janelas de cada fold chegavam mutiladas ao Gate 3b (ex.: o fold de
2023-10-25 usava `2022-01-24..2023-03-10` em vez de `2023-05-08..2023-10-25`). Corrigido;
151 testes de regressão cobrindo a seleção de sessões, incluindo o caso 17:50 vs. 17:55.
**Verificado: `03cc4ce` (Gate 2) não passa por esse código, e as sessões do Gate 3b
(`b93cbfe`) coincidem exatamente entre a lógica antiga e a corrigida — nenhum resultado já
commitado foi contaminado por este bug.**

**Resultado final**, bootstrap pareado por sessão sobre o OOS acumulado dos 8 folds
(2000 draws), 673 sessões comuns a WDO$N e 674 a WIN$N — pela primeira vez perto do poder
estatístico necessário:

| Alvo | Braço | h | ΔAUC | IC95% |
|---|---|---:|---:|---|
| WDO$N | v1 | 3 | -0,0026 | [-0,0103; +0,0044] |
| WDO$N | v2 | 3 | -0,0023 | [-0,0102; +0,0058] |
| WIN$N | v1 | 3 | +0,0067 | [-0,0016; +0,0157] |
| WIN$N | v2 | 3 | +0,0057 | [-0,0029; +0,0146] |
| WDO$N | v1 | 6 | -0,0101 | [-0,0225; +0,0016] |
| WDO$N | v2 | 6 | -0,0072 | [-0,0196; +0,0058] |
| WIN$N | v1 | 6 | -0,0123 | [-0,0245; +0,0009] |
| WIN$N | v2 | 6 | -0,0115 | [-0,0239; +0,0006] |
| WDO$N | v1 | 20 | +0,0012 | [-0,0208; +0,0229] |
| WDO$N | v2 | 20 | +0,0026 | [-0,0186; +0,0238] |
| WIN$N | v1 | 20 | -0,0033 | [-0,0273; +0,0202] |
| WIN$N | v2 | 20 | -0,0038 | [-0,0292; +0,0200] |

**Veredito: o macro não agrega valor tático útil em h=3/h=6 nesta cesta.** Todos os IC95%
incluem zero e, mais importante, o teto superior fica abaixo do mínimo operacional
pré-fixado de ΔAUC=+0,02 em praticamente todos os braços/horizontes — o único ponto
positivo (WIN$N v1 h=3, +0,0067) fica longe do limiar. **O item 3 do gate acima está
satisfeito nesta cesta e neste escopo: o macro sai do plano como fonte de sinal tático de
médio prazo (h=3/h=6) para a cesta de história longa medida no escopo OPEN_20 (abertura da
sessão, ~09:00–10:40 BRT) — ver qualificadores abaixo.** h=20 continua estruturalmente
inconclusivo (o IC ainda permite ~+0,02, e está fora do horizonte decisório tático de
qualquer forma — precisaria de ~22 anos de histórico para ter poder).

**Implicação para a Etapa 3:** a Frente 3 (Tactical Layer, modelo micro de 15 minutos) não
deve tratar o macro `P_up` **desta cesta, neste escopo,** como *feature* de médio prazo com
edge preditivo próprio — com dois limites de escopo que a Frente 3 deve carregar
explicitamente ao desenhar NF-02/NF-03, para não generalizar a conclusão além do que foi
medido:
- **(a) Composição da cesta:** este resultado vale para a cesta de história longa deste
  experimento (fatores com ≥1000 sessões — ver início da §3.7), que **exclui iShares e os
  pares FX** (USDCAD/USDCHF). Não é a mesma cesta incumbente calibrada em produção, que
  inclui iShares; o veredito não se estende a ela.
- **(b) Escopo temporal:** o resultado decisivo foi medido só no escopo `OPEN_20`
  (`scripts/measure_tactical_gate3.py`, janela de abertura, ~09:00–10:40 BRT) — nenhuma
  conclusão foi extraída para o resto da sessão.

Continua fazendo sentido tratar o macro como **contexto/filtro de regime** (ex.: gating de
operar/não operar), não como sinal direcional aditivo — mas essa distinção deve entrar no
desenho do NF-02/NF-03 escopada às duas condições acima, não generalizada para "o macro"
como um todo.

Artefato completo: `aggregate.json` (8 folds, 673/674 sessões, 2000 draws) —
implementação: `codex` (worktree isolada, sem gravação em produção,
`persist_state=False`).

---

## 4. Ordem de execução (reordenada pela tri-review)

**Etapa 0 — Emendar os planos-fonte** *(as emendas do §3 viram edição nos dois docs)*

**Etapa 0.5 — FRENTE 0: causalidade do eixo temporal** ✅ **CONCLUÍDA** (commit `16d4661`)
1. ~~Snapshot do `irai.db` de produção~~ — dispensado: usei o banco de produção via SSH.
2. ✅ **`engine.py` corrigido:** shift por **origem do símbolo** (`source == 'br'`, mata o D1)
   **e** offset **date-aware** via `backend/irai/timezones.py` (mata o A6).
3. ✅ Regressões em `tests/test_engine_timezone.py` (14): fator B3 alinhado ao mesmo instante
   de parede; target global intocado; sessão de **inverno** no nível do engine (sem ela, um
   `timedelta(hours=6)` literal reintroduzido passaria despercebido — no verão os dois
   comportamentos são idênticos). Suíte: 95 passed, 8 skipped.
   Implementação: `codex` · Revisão: `fable-reasoner` (merge liberado).

**Itens abertos que a Frente 0 deixou** (nasceram da revisão, com prazo):
4. ✅ **VAL-01 — medido.** Ver §3.6 abaixo. Resumo: a contaminação era **real e grande**
   (o `P_up` da tela errava 7–17 pp em média), mas a frase anterior deste plano — "os 69,0%
   e 73,9% estão inflados por D1" — era **FALSA** e foi refutada: aqueles números vêm do
   calibrador, que nunca passa pelo caminho do shift. (Continuam sem servir de baseline, por
   outro motivo: são in-sample, diários, e têm o skew de relógio do D4.)
5. ✅ **Frontend local: eixo BRT dinâmico.** O caminho local consome o offset do payload e
   não depende mais de `-6h` fixo. ⚠️ Confirmar separadamente o contrato servido ao mobile
   via Firebase antes de considerar todos os clientes cobertos.
6. ⚠️ **O relógio do servidor Axi nunca foi medido.** Os iShares estão em cestas vivas; se o
   Axi estiver em outro fuso, é a **mesma classe de bug do D1, ainda aberta**. Medir com o
   mesmo método (correlação contra um símbolo de fuso conhecido).
7. **Follow-up (pré-existente, não urgente):** barras B3 ≥18:00 BRT no verão cruzam a
   meia-noite do eixo (+6h → 00:00 do dia seguinte), fazendo o restore do Kalman do dia
   seguinte rejeitar o prior — warm-start sazonal. Checar se produção tem barras nesse
   horário.

**Etapa 1 — Fundação NWE causal** ✅ **CONCLUÍDA NO ESCOPO FUNCIONAL**
1. ✅ Regressões permanentes de causalidade e contrato.
2. ✅ Módulo puro NWE/VWAP/ATR em passada única, com warm-up na engine.
3. ✅ Snapshots enriquecidos e overview reutilizando o resultado causal.
4. ✅ Propagação pela API/Firebase e consumo no frontend.
5. ✅ Remoção do `computeNWE` local e do lookahead visual em `t+1`.
6. ⏳ Threadpool/single-flight permanece como requisito de desempenho antes do rollout
   Tactical, não como bloqueio da Fundação NWE.

**Etapa 2 — Bloqueios de ambiente** ⇉ **PARCIALMENTE CONCLUÍDA**
1. ✅ `migrate_to_head()` executa a cadeia idempotente no boot da API e collectors.
2. ⚠️ Medir o relógio Axi contra uma fonte conhecida.
3. ⚠️ Conferir cesta, pesos, versão e comportamento do WDO contra produção.
4. ⚠️ Validar replay/live final no Windows; Linux não comprova coleta MT5.

**Etapa 3 — Tactical Layer** *(revisada pelas decisões de 2026-07-14)*
1. ✅ Unificar thresholds do backend, payload e gráfico; reenquadrar markers como distorções.
2. ✅ Definir e testar o fechamento determinístico da barra M5.
3. ✅ **NF-01A:** construir extrator único e backtester point-in-time.
4. ✅ Fazer revisão independente do núcleo do NF-01A.
5. 🚧 **NF-01B/VAL-04:** braço executável concluído sem edge promovível nos markers atuais;
   comparar o Pair fixo e avaliar a regra local com/sem IRAI, mantendo exploração separada
   do gate confirmatório.
6. 🚧 Auditar rollover: construção WIN/WDO e sensibilidade de calendário concluídas; manter o
   gate provisório até evidência do instante histórico do switch por liquidez, antes de parar,
   promover regra simples ou autorizar NF-02.
7. Executar NF-02 somente se houver hipótese incremental pré-registrada.
8. Criar migrações e implementar estados/eventos apenas para hipótese aprovada.
9. Implementar governança, drift, despromoção, API/Firebase e UI com flag desligada.
10. Validar histórico → Windows/live → shadow live → ativação individual WIN e depois WDO.

---

## 5. Riscos que decidem o resultado

1. **Markers ainda não são setup aprovado.** Nome e cor podem induzir ação antes de existir
   backtest econômico específico do Pair/Z.
2. **Threshold visual e operacional divergem.** O gráfico desenha `±2`, enquanto o runtime
   dispara por padrão em `±1,5`; isso quebra a explicabilidade até existir fonte única.
3. **Barra em formação.** Marker visual pode nascer na borda direita; evento Tactical só pode
   existir depois do fechamento determinístico.
4. **Ambiente.** O banco local está vazio. A revisão contornou isso por SSH, mas paridade e
   auditorias que dependem de barras reais continuam exigindo acesso controlado à produção.
5. **Escopo do Tactical.** Sem os cortes da §3.5, o gate pode nunca ter amostra para aprovar nada.
6. **Generalização do macro.** O veredito de ausência de edge vale para a cesta e janela
   `OPEN_20` medidas; não autoriza declarar o `P_up` inútil em todos os usos e horários.

---

## 6. Fora de escopo (reafirmado)

Execução automática de ordens, lote, copy trading — **o IRAI é suporte à decisão**. Volume
Profile / book na v1. Expansão imediata aos 20 ativos. Recalibrar o macro sem evidência.
Alegar validação live a partir do Linux. Polimento de frontend (Parte 3).

---

## 7. Lacunas — o que ninguém conseguiu verificar

Isto é resultado, não omissão:
- **F3** (`real_volume=0` histórico) segue **SOLO**, não verificado. Não deve virar código
  antes de uma checagem. O D4 já foi confirmado e corrigido; não pertence mais a esta lista.
- **Timestamp histórico do rollover por liquidez** — a captura MT5 confirmou que `WIN$N` e
  `WDO$N` são séries "Por Liquidez" sem ajustes, e a sensibilidade de calendário foi medida;
  ainda falta provar quando cada troca efetiva ocorreu. Isso requer contratos individuais ou
  agenda histórica do broker, não apenas o calendário de vencimento da B3.
- **Convenção de DST do broker** — medi o comportamento (regra dos EUA), não a política oficial
  documentada da Tickmill. A tabela de offsets deve ser **derivada do dado**, não do calendário.
- **Firebase de produção** — exige auth (401); o contrato real servido ao mobile não foi inspecionado.

---

*Documentos-fonte: [plano-mãe](./2026-07-10-frontend-migration-status-and-forward-plan.md) ·
[Fundação NWE](./2026-07-13-nwe-causal-backend-foundation.md) ·
[Tactical Layer](./2026-07-13-irai-tactical-layer-win-wdo.md)*
