# IRAI — Plano consolidado (estado + rota até o Tactical Layer)

**Projeto:** IRAI — Intraday Risk Appetite Index
**Criado:** 2026-07-13 · **Revisado e verificado:** 2026-07-13 (tri-review)
**Consolida:** `2026-07-10-frontend-migration-status-and-forward-plan.md` (plano-mãe),
`2026-07-13-nwe-causal-backend-foundation.md` (Fundação NWE) e
`2026-07-13-irai-tactical-layer-win-wdo.md` (Tactical Layer)

## Sumário executivo para a Miqs

O IRAI já possui a base macro, os gráficos, o GEX e os sinais por barra. O projeto está
saindo da fase de **corrigir e tornar confiável a fundação quantitativa** e entrando na
fase de **centralizar o NWE no backend**. Somente depois disso começa a nova camada
Tactical, inicialmente para `WIN$N` e, após validação, `WDO$N`.

**Onde estamos agora:** a correção crítica de causalidade e horário de verão no backend
foi concluída. Os próximos gates são corrigir o rótulo BRT do frontend, medir os efeitos
da correção nas métricas históricas, auditar o fuso da Axi e entregar o NWE causal como
fonte única.

**Destino desta rota:** transformar o `P_up` macro em estados operacionais explicáveis —
como `AGUARDANDO_PULLBACK`, `ARMADO`, `CONFIRMADO` e `NAO_OPERAR` — sem executar ordens.

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
| BUG-03 | **BUG** | Remover `-6h` fixo do eixo BRT no frontend/Firebase | 🔜 Próximo | Evita rótulo incorreto a partir de 01/11/2026. |
| BUG-04 | **BUG** | Eliminar NWE não causal e divergência browser/backend | 🔜 Próximo | Impede que barras futuras alterem sinais históricos. |
| MEL-01 | **MELHORIA** | NWE/VWAP/ATR causal como fonte única no backend | 🔜 Próximo | Garante o mesmo número no replay, API, Firebase e UI. |
| MEL-02 | **MELHORIA** | Bootstrap idempotente das migrações do banco | ⏳ Planejado | Permite criar e atualizar o schema tático com segurança. |
| MEL-03 | **MELHORIA** | Threadpool, single-flight, cache e payload Firebase | ⏳ Planejado | Mantém API e mobile responsivos com os novos cálculos. |
| NF-01 | **NOVA FUNCIONALIDADE** | Extrator e backtester tático point-in-time | ⏳ Planejado | Cria uma base auditável para testar decisões barra a barra. |
| NF-02 | **NOVA FUNCIONALIDADE** | Modelo micro de 15 minutos com walk-forward | ⏳ Planejado | Adiciona confirmação de curto prazo ao contexto macro. |
| NF-03 | **NOVA FUNCIONALIDADE** | Máquina de estados e eventos táticos | ⏳ Planejado | Traduz probabilidades em estados compreensíveis. |
| NF-04 | **NOVA FUNCIONALIDADE** | API, Firebase e UI Tactical por feature flag | ⏳ Planejado | Leva o sinal validado ao operador sem ativação prematura. |
| VAL-01 | **VALIDAÇÃO** | Recalcular métricas de WIN/WDO após BUG-01 | ⚠️ Pendente | Os baselines históricos anteriores estão inflados. |
| VAL-02 | **VALIDAÇÃO** | Medir fuso Axi e conferir modelo macro do WDO | ⚠️ Pendente | Decide se o piloto pode avançar de WIN para WDO. |
| VAL-03 | **VALIDAÇÃO** | Replay/live final no Windows com MT5 | ⚠️ Gate final | Linux não valida coleta nem comportamento live dos terminais. |

### Escopo da primeira entrega tática

**Entra na v1:** fundação causal; NWE/VWAP/ATR no backend; piloto inicial em WIN; modelo
micro de 15 minutos; regiões causais; estados e eventos em barras fechadas; API/Firebase;
interface sob feature flag; validação do WDO antes de ativá-lo.

**Fica para depois:** `P_micro_30m`, zonas estatísticas de realização e payload tático em
cada barra da série; expansão aos 20 ativos; polimento secundário de frontend.

**Continua fora do produto:** execução automática de ordens, lote, copy trading, Volume
Profile/book e qualquer promessa de resultado operacional.

### Sequência de entrega

```text
Base temporal corrigida
        ↓
Pendências de fuso e métricas
        ↓
NWE causal no backend
        ↓
Migrações + validação do WDO
        ↓
Backtester e modelo micro 15m
        ↓
Estados/eventos + API/Firebase/UI desligada
        ↓
Gate histórico → replay/live Windows → ativação individual
```

**Método desta versão:** três revisores independentes (`deep-reasoner`, `fable-reasoner`,
`codex`) analisaram o plano em paralelo, cegos entre si; depois **cada achado foi
verificado contra o código e contra o banco de produção**. Todo achado abaixo carrega um
**selo de confiança** — a versão anterior deste documento era união sem verificação e
apresentava palpite não-checado com a mesma cara de fato.

| Selo | Significado |
|---|---|
| **CONFIRMADO** | Verificado por mim no código ou no banco real. É fato. |
| **CONSENSO** | ≥2 revisores, evidência `arquivo:linha`, não re-verificado por mim. |
| **SOLO** | 1 revisor, ninguém contestou nem confirmou. **Não vira ação sem checar.** |
| **REFUTADO** | Verificado e derrubado. Registrado para não voltar. |

> **Leitura técnica em 30 segundos.** A tri-review encontrou um lookahead de 6 horas nos
> fatores B3 (D1) e um shift incompatível com o DST (A6). Os dois bugs já foram corrigidos
> no backend pelo commit `16d4661`, o que liberou a entrada na Fundação NWE. Permanecem
> como gates imediatos o eixo BRT do frontend, a nova medição das métricas de WIN/WDO e a
> auditoria do relógio Axi. Dos 16 achados originais, 13 sobreviveram, 1 foi refutado e 2
> tiveram a severidade corrigida.

---

## 1. Onde estamos

### 1.1 Concluído (verificado no código)

| Bloco | Status | Evidência |
|---|---|---|
| **Parte 1 — migração de charts** (Recharts → lightweight-charts, 5/5) | ✅ | bundle 752→419 kB; `recharts` removido (`eae208e`) |
| **GEX — gamma walls IBOV → WIN$N e WDO$N** | ✅ | serviço ponta a ponta + timer systemd (`4dd1273`…`39e6822`) |
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
  [ FRENTE 1 ]                [ FRENTE 2 ]                 [ FRENTE 3 ]
  Fundação NWE causal  ─────> Bloqueios de ambiente ─────> Tactical Layer
  (Fases 5 + 6)               (Fase 1, cesta do WDO)        (WIN$N, WDO$N)
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
> está no rótulo BRT do frontend/Firebase.

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

#### C2 — Máquina de estados contraditória · **CONSENSO** (3/3) · ALTO *(era CRÍTICO)*
Confirmado nos docs: resumo permite `ARMADO` a modelo reprovado (`tactical:25`) vs §6 manda
para `NEUTRO` (`:179`); JSON usa `ARMED` (`:231`), estados usam `ARMADO`; `CONFIRMADO` sem
transição de saída; cooldown "3 barras **ou** até sair e voltar" sem precedência.
**Severidade rebaixada** por 1 revisor e aceito: é ambiguidade de documento (custo de
reescrita), não dado corrompido. Um sub-item foi **corrigido**: "thresholds por classe não
são espelhos" → o certo é "**não estão especificados**" (é omissão, não assimetria provada).
**Emenda.** Tabela `(estado, condição, prioridade, próximo estado, evento)` + enum canônico
único, antes de qualquer código.

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

#### A5 — Purge de 6 barras insuficiente · **CONSENSO** (3/3) · ALTO
Para o *label* (3 e 6 barras), purge=6 basta. O furo é específico: MFE/MAE são medidos até
**20 barras** (`tactical:94`) e alimentam as zonas de realização (`:199-200`) → entram na
seleção. Falta também a regra "labels nunca cruzam a fronteira da sessão" (**ausência
confirmada** no doc).
**Emenda.** Purge = `max(horizonte_label, horizonte_MFE)` = **20**; label truncado na
fronteira da sessão.

#### A7 — Gate estatístico frágil · **CONSENSO** (3/3) · ALTO
ECE ≤ 0,10 sobre ~100 confirmações é enviesado por seleção (a amostra é truncada pelo próprio
threshold), e o baseline do Brier **nunca é definido**. "Ridge multinomial" não identifica um
modelo: `RidgeClassifier` não emite probabilidades nativamente.
**Emenda.** Calibração medida sobre **todas** as previsões OOS bar-a-bar; as 100 confirmações
ficam só para o gate econômico; nomear estimador/solver/método de calibração e o baseline.

#### D4 — Skew treino/serviço: calibrador e engine ancoram o fator em pontos diferentes · **SOLO** (deep) · ALTO
Para alvos B3, o calibrador recorta os fatores globais em 09:00–18:00 **EEST**
(`calibrate_universal.py:56-67`), enquanto a engine ancora o fator na **00:00** da janela
(`engine.py:566-570`). Pesos e σ são ajustados sobre uma variável e aplicados sobre outra.
Atinge **exclusivamente WIN$N e WDO$N** — os dois ativos do piloto.
⚠️ **SOLO — não verifiquei.** Checar antes de agir.

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

#### Gate obrigatório antes de calibrar o Tactical (substitui "replanejar")

Não replaneje a Frente 3 ainda. Condicione-a a três medições, nesta ordem:
1. **Recalibrar** WIN/WDO pós-fix (e corrigir o skew de relógio do D4 no calibrador).
2. **Brier / log-loss + curva de calibração** do nowcast contra o rótulo de sessão, por hora
   do dia, vs. um baseline trivial ("sinal do retorno de sessão até agora"). Se não bater o
   baseline nem nisso, aí sim o macro layer está em apuros.
3. **IC (Spearman) e análise de decis** de `(P_up−50)`, `ΔP_up(k)` e da divergência contra o
   retorno forward, com IC clusterizado por sessão; e um **modelo aninhado** (momentum
   próprio vs. + features de P_up). Se ΔAUC ≈ 0, **aí** o macro sai do plano.

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
5. ⚠️ **Frontend: eixo BRT com `-6h` fixo — prazo 01/11/2026.** O JSON **muda** em datas de
   inverno (barras B3 saem em 14:00, não 15:00), então o eixo âmbar vai rotular a abertura
   como "08:00" a partir da próxima virada. Valores de sinal corretos; só os rótulos erram.
   Expor o offset no payload e consumi-lo em `App.jsx:514,668` no lugar do `-6`. **O app
   mobile via Firebase provavelmente tem o mesmo hardcode.**
6. ⚠️ **O relógio do servidor Axi nunca foi medido.** Os iShares estão em cestas vivas; se o
   Axi estiver em outro fuso, é a **mesma classe de bug do D1, ainda aberta**. Medir com o
   mesmo método (correlação contra um símbolo de fuso conhecido).
7. **Follow-up (pré-existente, não urgente):** barras B3 ≥18:00 BRT no verão cruzam a
   meia-noite do eixo (+6h → 00:00 do dia seguinte), fazendo o restore do Kalman do dia
   seguinte rejeitar o prior — warm-start sazonal. Checar se produção tem barras nesse
   horário.

**Etapa 1 — Fundação NWE causal**
1. Regressões de causalidade **antes** da correção.
2. Módulo puro NWE/VWAP/ATR em passada única, com warm-up carregado pela engine.
3. Snapshots enriquecidos; overview reutiliza o último da série.
4. Threadpool + single-flight.
5. Propagar por API e Firebase (`schema_version`, `history_closes`, `is_b3`) — incluindo o
   caminho Firebase na regressão de paridade (F5).
6. Migrar os charts; remover o `computeNWE` local com a ressalva do D2.

**Etapa 2 — Bloqueios de ambiente** ⇉ *(paralelo à Etapa 1)*
11. `init_db()` com a cadeia completa de migrações (é Python+SQLite puro — **roda no Linux
hoje**, não é bloqueado por Windows). 12. Conferir a cesta do WDO contra produção — **depois**
do D1/D4, senão a divergência é medida contra um modelo desalinhado.

**Etapa 3 — Tactical Layer** *(só depois de 0.5, 1 e 2)*
13. Migração de `tactical_models`/`tactical_events`/config **antes** da calibração.
14. Extrator único de features. 15. Backtester com replay point-in-time (C1). 16. Calibração
micro 15m + walk-forward + gate. 17. Máquina de estados + eventos **só em barra fechada** (X3).
18. API/Firebase aditivos; UI com feature flag desligada. 19. Validação histórica → Windows →
ativação individual, só após o gate.

---

## 5. Riscos que decidem o resultado

1. **O baseline histórico anterior ao D1 não é reutilizável.** O backend foi corrigido, mas
   as métricas antigas de WIN/WDO continuam infladas até serem refeitas pelo replay do engine.
2. **O frontend ainda tem prazo de DST.** O backend já é date-aware; o eixo âmbar ainda usa
   `-6h` fixo e rotulará a abertura como 08:00 a partir de **01/11/2026** se não for corrigido.
3. **Paridade do NWE.** Sem warm-up dentro da engine e sem a âncora `win_open`/% explícitas, a
   regressão de paridade falha e a divergência local-vs-prod volta.
4. **Ambiente.** O banco local está vazio. A revisão contornou isso por SSH, mas paridade e
   auditorias que dependem de barras reais continuam exigindo acesso controlado à produção.
5. **Escopo do Tactical.** Sem os cortes da §3.5, o gate pode nunca ter amostra para aprovar nada.

---

## 6. Fora de escopo (reafirmado)

Execução automática de ordens, lote, copy trading — **o IRAI é suporte à decisão**. Volume
Profile / book na v1. Expansão imediata aos 20 ativos. Recalibrar o macro sem evidência.
Alegar validação live a partir do Linux. Polimento de frontend (Parte 3).

---

## 7. Lacunas — o que ninguém conseguiu verificar

Isto é resultado, não omissão:
- **D4** (skew calibrador/engine) e **F3** (`real_volume=0` histórico) seguem **SOLO**, não
  verificados. Não devem virar código antes de uma checagem.
- **Rollover de contratos** — se a série `$N` é ajustada ou concatenada crua, e quantas rolagens
  caem na janela, exige inspeção no MT5.
- **Convenção de DST do broker** — medi o comportamento (regra dos EUA), não a política oficial
  documentada da Tickmill. A tabela de offsets deve ser **derivada do dado**, não do calendário.
- **Firebase de produção** — exige auth (401); o contrato real servido ao mobile não foi inspecionado.

---

*Documentos-fonte: [plano-mãe](./2026-07-10-frontend-migration-status-and-forward-plan.md) ·
[Fundação NWE](./2026-07-13-nwe-causal-backend-foundation.md) ·
[Tactical Layer](./2026-07-13-irai-tactical-layer-win-wdo.md)*
