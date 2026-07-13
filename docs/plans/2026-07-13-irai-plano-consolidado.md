# IRAI — Plano consolidado (estado + rota até o Tactical Layer)

**Projeto:** IRAI — Intraday Risk Appetite Index
**Criado:** 2026-07-13 · **Revisado e verificado:** 2026-07-13 (tri-review)
**Consolida:** `2026-07-10-frontend-migration-status-and-forward-plan.md` (plano-mãe),
`2026-07-13-nwe-causal-backend-foundation.md` (Fundação NWE) e
`2026-07-13-irai-tactical-layer-win-wdo.md` (Tactical Layer)

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

> **Leitura em 30 segundos.** A tri-review encontrou um **lookahead de 6 horas vivo no
> modelo de produção** (D1) que nenhuma revisão anterior tinha visto — e que **reordena o
> plano**: ele precisa ser consertado *antes* da Fundação NWE, porque contamina o `P_up`
> que o Tactical Layer usaria como feature. O DST (A6) foi **confirmado empiricamente no
> banco**, mas o plano errava as datas *e* o mecanismo. Dos 16 achados originais, 13
> sobreviveram, 1 foi refutado e 2 tiveram a severidade corrigida.

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

## 2. As três frentes (ordem corrigida)

```
  [ FRENTE 0 ]  <-- NOVA, e vem antes de tudo
  Causalidade do eixo temporal (engine.py:471-473)
  D1 (lookahead 6h nos fatores B3) + A6 (DST) — a MESMA função
        |
        v
  [ FRENTE 1 ]                [ FRENTE 2 ]                 [ FRENTE 3 ]
  Fundação NWE causal  ─────> Bloqueios de ambiente ─────> Tactical Layer
  (Fases 5 + 6)               (Fase 1, cesta do WDO)        (WIN$N, WDO$N)
```

A Frente 0 não existia na versão anterior do plano. Ela vem primeiro porque **consertar o
NWE e deixar o `P_up` com lookahead produz exatamente a métrica inflada que o plano diz
querer evitar** — por um caminho que o achado C1 não cobria.

---

## 3. Achados verificados

### 3.0 FRENTE 0 — os dois bugs do eixo temporal (mesma função, consertar juntos)

#### D1 — Lookahead de 6 horas nos fatores domésticos · **CONFIRMADO** · CRÍTICO

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

#### A6 — DST: o shift fixo de +6h é cego a horário de verão · **CONFIRMADO** · CRÍTICO

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

---

## 4. Ordem de execução (reordenada pela tri-review)

**Etapa 0 — Emendar os planos-fonte** *(as emendas do §3 viram edição nos dois docs)*

**Etapa 0.5 — FRENTE 0: causalidade do eixo temporal** ⚠️ **NOVA — vem antes de tudo**
1. Snapshot do `irai.db` de produção para o Linux (D3).
2. **Corrigir `engine.py:471-473` de uma vez:** shift por **origem do símbolo** (D1) **e**
   **date-aware** por tabela de offsets derivada do dado (A6).
3. Regressões: (a) target B3 com fator B3 consome a barra do mesmo instante de parede;
   (b) uma data de inverno e uma de verão.
4. Recalcular a acurácia de WIN/WDO **depois** do conserto — os 69,0% e 73,9% atuais estão
   inflados por D1 e não devem ser usados como baseline.

**Etapa 1 — Fundação NWE causal**
5. Regressões de causalidade **antes** da correção. 6. Módulo puro NWE/VWAP/ATR (passada única,
warm-up carregado pela engine). 7. Snapshots enriquecidos; overview reutiliza o último da série.
8. Threadpool + single-flight. 9. Propagar por API e Firebase (`schema_version`,
`history_closes`, `is_b3`) — incluindo o caminho Firebase na regressão de paridade (F5).
10. Migrar os charts; remover o `computeNWE` local (com a ressalva do D2).

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

1. **D1 é lookahead vivo.** Toda métrica histórica de WIN/WDO está inflada. Treinar o Tactical
   antes de consertá-lo produz um modelo que passa no gate e desaba ao vivo — o risco mais caro
   de detectar depois.
2. **A6 tem prazo.** A próxima virada de DST é **01/11/2026**. Se o shift não virar date-aware
   até lá, a produção volta a desalinhar 1h — possivelmente durante o rollout do Tactical,
   invalidando o próprio gate "aprovado no live".
3. **Paridade do NWE.** Sem warm-up dentro da engine e sem a âncora `win_open`/% explícitas, a
   regressão de paridade falha e a divergência local-vs-prod volta.
4. **Ambiente.** O banco local está vazio; sem o snapshot de produção (Etapa 0.5, passo 1) a
   Frente 1 é tão bloqueada quanto a Frente 2.
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
