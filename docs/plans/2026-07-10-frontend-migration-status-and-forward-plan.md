# IRAI — Plano de convergência de sinal (foco: **Parte 2**)

**Projeto:** IRAI — Intraday Risk Appetite Index
**Criado:** 2026-07-10 · **Reescrito com ênfase na Parte 2:** 2026-07-12
**Revisões:** `fable-reasoner` + `codex` (`/codex-r`) na v1; evidências de linha
**reverificadas contra o código em 2026-07-12** (os números de `main.py` mudaram
com a entrada do endpoint de GEX).

> **Por que a ênfase mudou.** A v1 deste documento era um plano de *migração de
> frontend* com um apêndice sobre backend. A migração acabou (5/5 charts, GEX
> entregue, recharts removido). **O que sobrou — e o que hoje separa o nosso
> dashboard da produção — é inteiramente a Parte 2: sinal e contrato de backend.**
> Por isso a Parte 2 virou o corpo do plano; a Parte 1 (entregue) foi comprimida
> para o fim, como registro.

---

## PARTE 2 — Backend e sinal (FOCO DE TRABALHO)

### 2.0 A prova visual: Mini Dólar, nós vs. produção (2026-07-12)

Comparação lado a lado do mesmo ativo, mesma sessão:

| Leitura | Nosso (local) | Produção | Diagnóstico |
|---|---|---|---|
| Chart de movimento/NWE | ✅ igual | ✅ | **convergido** (migração cumpriu o objetivo) |
| Sinal direcional | BAIXA | BAIXA | **convergido** |
| **P(↑) dinâmico** | **1,0% — linha reta** | **32,4% — oscilando** | ❌ **V1 estático vs. V2/Kalman** |
| Convicção | 59% | 21% | ❌ consequência do mesmo V1/V2 |
| PAR ATIVO / DIV Z | ausente | `isharescurrencybond+` β −1.061 | ❌ só existe no V2 |
| Markers P VENDA no chart | ausentes | presentes | ❌ engine não emite eventos discretos |
| GEX / MID | só WIN | WIN **e DOL** | ❌ nosso worker só cobre IBOV |

**O chart não é mais o problema. O modelo servido à UI é.** Essa é a tese central
deste plano.

### 2.1 O bug-raiz — um único parâmetro explica a maior parte da divergência

`frontend/src/App.jsx:648` pede `version=both`, que o engine resolve como **V1
estático**. O `scripts/firebase_sync.py` (linhas 48/66) pede **`version=v2`**.

> **Localhost mostra V1. Produção mostra V2. Ambos rotulados "DINÂMICO (KALMAN)".**

Consequências em cascata (todas visíveis na tabela 2.0): P(↑) travado, convicção
deslocada, PAR ATIVO/DIV Z ausentes, painel de pesos Kalman exibindo pesos
**estáticos**. Corrigir isso é o maior salto de convergência por linha de código do
projeto — **mas não pode ser feito sozinho** (ver 2.3, o pacote Fase 3 + B2).

### 2.2 Estado verificado das fases (evidência revalidada em 2026-07-12)

| Fase | Tema | Status | Evidência (linha conferida hoje) |
|---|---|---|---|
| **2** | Contrato `version=both` | ❌ **P0 — o bug-raiz** | `App.jsx:648` pede `both` → V1 no local; `firebase_sync.py:48,66` pede `v2` → **divergência V1/V2 entre ambientes** |
| **3** | Ghost bars / pré-mercado | ❌ **P0 vivo** | `target_cursor = 0` (`engine.py:588`) → o gate `is_pre_market = (target_cursor < 0)` (`:655`) **nunca dispara** → `snap.win_return = 0.0` (`:768`) **é inalcançável**. Barras sintéticas usam `close` de ontem + `win_open` de hoje → **retorno falso em todo o pré-mercado**, que no V2 alimenta o Kalman por ~180 barras M5 e o `price_diverge_z` |
| **4** | Persistência Kalman monotônica | ❌ **Corrupção viva** | `INSERT OR REPLACE INTO kalman_state` **sem guard de timestamp** (`db.py:187`), chamado ao fim de todo compute v2. `irai_current` itera datas anteriores em v2 → **sobrescreve o estado live com estado antigo**, que vira prior da sessão seguinte |
| **1** | Schema `divergence_config` | 🔶 Parcial | `init_db()` roda só `conn.executescript(SCHEMA)` (`db.py:115`) e **não** chama `migrate_divergence_config()` → instalação limpa ainda pode cair em "0 models loaded" |
| **5** | NWE causal (backend) | ❌ Pendente | `get_center` soma sobre `range(n)` (`main.py:288-291`) → **lookahead de 1 barra** no `nwe_slope` dos cards do overview |
| **6** | API não-bloqueante | ❌ Pendente | Sem single-flight/threadpool em `main.py` |
| **7** | Contrato Firebase completo | ❌ Pendente | `firebase_sync` só conserva `series`/`summaries`; o frontend lê `data.history[safeTarget]`, que nunca existe → `history_closes` vazio em produção; falta `is_b3`. *(Nuance: `accuracy` já viaja em `summaries[target]` → **B1 não depende desta fase**)* |
| **8.1** | Acurácia no detalhe (**B1**) | 🔶 Quebrado, fix trivial | `App.jsx:978` lê `seriesInfo.accuracy ?? 80`, mas `fetchSeries` nunca seta `accuracy` → **sempre o fallback 80** |
| **8.2** | Corridas de request | ❌ Pendente | `fetchSeries` sem `AbortController`/sequence-id |
| **10** | Modularização `App.jsx` | 🔶 Parcial | 5 charts extraídos; `App.jsx` segue com **~1.200 linhas** |

### 2.3 Ordem de execução (a sequência importa — não reordenar sem motivo)

**Pacote A — destravar o V2 (o coração do plano)**

1. **Fase 4 — guard monotônico do Kalman.** *URGENTE e independente.* É corrupção
   ativa **agora, em produção**. `ON CONFLICT ... WHERE excluded.timestamp > ...` +
   `persist_state=False` para replay histórico. Remover a def morta de
   `compute_from_db`. **Antes de implantar**, snapshotar `kalman_state` + timestamps
   (para distinguir correção de mudança de continuidade).
2. **Fase 3 + B2 juntos (não separar).** Corrigir o `win_return=0` de pré-mercado
   **e então** trocar a UI para `version=v2`. Fazer B2 sozinho **pioraria** o
   localhost: exporia na UI principal o caminho de pré-mercado bugado que o
   `version=both` hoje esconde acidentalmente ao cair no V1.
   *Resultado esperado: P(↑) dinâmico, convicção, PAR ATIVO e DIV Z convergindo com
   a produção — resolve 4 das 7 linhas da tabela 2.0 de uma vez.*
3. **B1 — acurácia no detalhe.** Trivial, alto valor, independente. Propagar
   `summary.accuracy` para `seriesInfo` nos dois ramos de `fetchSeries`.

**Pacote B — o que sobra da divergência**

4. **Markers de sinal (pair/z) por barra.** O `TVNweChart` **já suporta** os markers
   (`pair_compra`/`pair_venda`/`z_compra_val`/`z_venda_val`); falta o engine emitir
   os **eventos discretos**. ⚠️ Manter a decisão de projeto: **não** derivar de
   thresholds contínuos (viraria spam, não os eventos por barra da produção).
5. **WDO — verificar a cesta (não assumir que está errada).** A produção mostra
   PAR ATIVO `isharescurrencybond+` no Mini Dólar, e **esse fator já está na nossa
   cesta do WDO**. Ou seja: **a hipótese "cesta velha" não está confirmada** — o
   V1/V2 (item 2) provavelmente explica a divergência sozinho. *Só recalibrar se,
   depois do Pacote A, o WDO ainda divergir.* Cestas atuais no DB:
   - `win`: `WDO$N, DI1$N, BRENT, BTCUSD, CADCHF, US30, USDMXN, iSharesTreasury1-3+` (acc 69,0% · R² 0,464 — **já convergida com a produção**)
   - `wdo`: `WIN$N, DI1$N, VIX, US500, USTEC, DE40, USDCHF, iSharesCurrencyBond+` (acc 73,9% · R² 0,499 — **a verificar**)
   - ⚠️ O feed Firebase agora exige auth (**401**) — a verificação precisa de outra via (credencial ou inspeção da UI de produção).
6. **GEX do dólar.** Nosso `gex_worker` cobre só as opções do **IBOV** (→ WIN$N). A
   produção plota GEX/MID também no Mini Dólar. Extensão: opções de **dólar** na
   mesma API BDI/B3 (`OpenPositionsEquities` é renda variável; DOL vive na trilha de
   derivativos — mapear a tabela) + join no MT5, reusando todo o pipeline de cálculo.

**Pacote C — higiene quantitativa (sem impacto visual imediato)**

7. **Fase 5 — NWE causal no backend** (kernel `j <= i`, lookback 95). Vem **antes**
   de qualquer polimento visual que dependa do NWE.
8. **Fase 1 — `init_db` chamando a migração** (fecha o modo de falha da instalação limpa).
9. **Fase 6 — API não-bloqueante** (single-flight por `(target,date,version)`, trabalho
   síncrono para threadpool). Isolada; pode ficar por último.
10. **B3 / Fase 7 — contrato Firebase** (`history_closes` + `is_b3` + `schema_version`).

### 2.4 Regra transversal (workflow do projeto)

Toda mudança de sinal/contrato (Fases 2, 3, 4, 5; B1; B2) entra com **regressão
permanente antes da correção**. Hoje `tests/` cobre só z-score/pair. Mínimo novo:
`test_engine_premarket`, `test_engine_kalman_state`, `test_api_contract`,
`test_nwe_causality`, `test_db_schema`.

### 2.5 Riscos da Parte 2

- **Fase 4 é corrupção ativa hoje** — snapshotar o estado antes do guard.
- **B2 sem a Fase 3** exporia o pré-mercado bugado na UI principal → manter o pacote junto.
- **"Corrigir o sinal, depois polir o visual"** — inverter deixa o operador lendo
  sinais errados enquanto se ajusta crosshair/eixo.
- **Markers derivados de threshold contínuo** viram spam — só eventos discretos.
- **Recalibrar o WDO sem necessidade** (item 5) gastaria uma janela e mexeria num
  modelo que hoje tem a **melhor acurácia do par** (73,9%).

---

## PARTE 1 — Entregue (registro, sem trabalho pendente)

### Migração de charts (Recharts → lightweight-charts v5) — ✅ 5/5

Reconstrução por engenharia reversa do bundle de produção (fonte perdida),
estratégia strangler-fig, um chart por vez com build/lint/deploy/`/codex-r`:

| Chart | Componente | Commit |
|---|---|---|
| Pair-spread z-score (piloto) | `TVPairwiseZScoreChart` | `b47e3b1` |
| Probabilidade P(↑) dinâmica | `TVProbabilityChart` | `87d7fd9` |
| Divergência-preço z-score | `TVPriceDivergeZScoreChart` | `1f43499` |
| Pesos dinâmicos (Kalman) | `TVKalmanWeightsChart` | `552f64f` |
| Movimento do índice (NWE) | `TVNweChart` | `9cc6066`, `2be2ecf` |

**Resultado:** bundle **752 kB → 419 kB** (gzip 227 → 131), **591 → 30 módulos**;
`recharts` removido do `package.json` (`eae208e`).

**Decisões preservadas:** fidelidade > melhoria; sem abstração prematura; chart de
movimento em **preço absoluto**; ghost bars como whitespace no centro NWE; eixo BRT
âmbar secundário omitido (uma timeScale por chart — ver C4 na Parte 3).

### GEX — gamma walls IBOV → WIN$N (task #8) — ✅ entregue 2026-07-12

Foi **além** do escopo original ("investigar fonte de dados"): virou serviço próprio,
de ponta a ponta, em ~40 s/dia.

- **Open interest:** API pública do BDI/B3 —
  `POST arquivos.b3.com.br/bdi/table/OpenPositionsEquities/{d}/{d}/{pág}/1000?sort=TckrSymb`
  (o `sort` é **obrigatório**: sem ele a paginação não é estável). Validado
  byte-a-byte contra a consulta em tela (778 séries, Σ 84.834.404).
- **Strike/CP/vencimento/prêmio + spot:** MT5 XP via `symbols_get("IBOV*")` **em lote**
  (`session_interest` do MT5 é sempre 0 → OI **precisa** vir do BDI).
- **Cálculo:** `netGEX(K) = Σ_venc [Γc·OIc − Γp·OIp]`; flip = zero do cumulativo
  (interp. linear); max/min = argmax/argmin com refino parabólico; conversão por basis
  dinâmico `f = WIN/IBOV`; gates de validade.
- **Entrega:** `backend/workers/gex_worker.py` + timer systemd (seg–sex 07:30, para/religa
  o collector) + `GET /api/irai/gex` (gate de frescor ≤4 d) + toggles **GEX/MID** no chart.
- Commits: `4dd1273` → `1236d45` → `d455105` → `7a4b5d9` → `6fc2639`.

### Outras entregas desde a v1 deste plano

- **Recalibração do WIN** para a cesta da produção + `--factors`/`--dry-run` no
  `calibrate_universal.py` (`69c312a`, `21f21fe`).
- **`pair_z` degenerado corrigido** (z=29 → faixa ±2): nova `pair_zscore` centrada na
  média, sem √t (`65321f7`, `44181f6`).
- **systemd** para API e collector (`5896f53`) + `--force` no collector.

---

## PARTE 3 — Backlog secundário (polimento de frontend; **não bloqueia a Parte 2**)

Só entra **depois** do Pacote A da Parte 2 — e a Trilha C depende da Fase 5 (NWE
correto) para não polir cima de um sinal errado.

- **C1 · Rede de testes primeiro.** Vitest sobre as funções puras: `computeNWE` causal,
  paridade %-vs-preço, `padSeriesToFullDay`, `toUnixTime`, convicção. **Trava o
  comportamento antes de refatorar.**
- **C2 · Validação visual do chart de movimento** (retorno % → preço absoluto; 180→320 px).
- **C3 · Touch/mobile.** Canvas captura gestos diferente de SVG; 5 charts empilhados podem
  sequestrar o scroll no celular (a produção é hospedada para mobile).
- **C4 · Reintroduzir o eixo BRT** via `tickMarkFormatter` de eixo único (−6h **só** para B3).
- **C5 · Sync de crosshair/time-axis** entre os 5 charts (`subscribeCrosshairMove`).
- **C6 · Concluir a modularização** (Fase 10): extrair de `App.jsx` o cliente de dados,
  timezone, `computeNWE`, `SignalGauge`.
- **Fase 8.2 · `AbortController`** em `fetchSeries`.
- **UX da produção não replicada:** hint "[Y-AXIS DESLOCADO — DUPLO CLIQUE PARA RESETAR]".
