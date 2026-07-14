# IRAI — Plano de convergência de sinal (foco: **Parte 2**)

**Projeto:** IRAI — Intraday Risk Appetite Index
**Criado:** 2026-07-10 · **Reescrito com ênfase na Parte 2:** 2026-07-12 · **Reverificado e atualizado:** 2026-07-13
**Revisões:** `fable-reasoner` + `codex` (`/codex-r`) na v1; evidências de linha
reverificadas contra o código em 2026-07-12, e novamente em 2026-07-13 depois que
o Pacote A fechou.

> **Por que a ênfase mudou.** A v1 deste documento era um plano de *migração de
> frontend* com um apêndice sobre backend. A migração acabou (5/5 charts, GEX
> entregue, recharts removido). **O que sobrou — e o que separava o nosso
> dashboard da produção — era inteiramente a Parte 2: sinal e contrato de backend.**
> Por isso a Parte 2 virou o corpo do plano; a Parte 1 (entregue) foi comprimida
> para o fim, como registro.

> **Atualização 2026-07-13 — Pacote A fechado.** Fases 2, 3, 4 e B1 concluídas
> (3/3 do Pacote A). Pacote B em 2/3 (markers de sinal e GEX do dólar entregues;
> falta confirmar a cesta do WDO). Pacote C e Parte 3 seguem sem nenhum item
> iniciado. Detalhe item a item com commits em §2.2 e §2.3.

---

## PARTE 2 — Backend e sinal (FOCO DE TRABALHO)

### 2.0 A prova visual: Mini Dólar, nós vs. produção (medição de 2026-07-12)

Comparação lado a lado do mesmo ativo, mesma sessão — estado **antes** do Pacote A:

| Leitura | Nosso (local) | Produção | Diagnóstico em 12/07 |
|---|---|---|---|
| Chart de movimento/NWE | ✅ igual | ✅ | **convergido** (migração cumpriu o objetivo) |
| Sinal direcional | BAIXA | BAIXA | **convergido** |
| **P(↑) dinâmico** | **1,0% — linha reta** | **32,4% — oscilando** | ❌ **V1 estático vs. V2/Kalman** |
| Convicção | 59% | 21% | ❌ consequência do mesmo V1/V2 |
| PAR ATIVO / DIV Z | ausente | `isharescurrencybond+` β −1.061 | ❌ só existe no V2 |
| Markers P VENDA no chart | ausentes | presentes | ❌ engine não emitia eventos discretos |
| GEX / MID | só WIN | WIN **e DOL** | ❌ nosso worker só cobria IBOV |

**O chart não era mais o problema. O modelo servido à UI era.** Essa foi a tese
central deste plano — e é o que o Pacote A atacou.

### 2.0.1 Depois do Pacote A + parte do B (verificado no código em 2026-07-13)

Reverificação **estática do código-fonte**, não uma nova medição ao vivo lado a
lado com a produção — os valores específicos (1,0% vs 32,4%, etc.) não foram
remedidos hoje.

| Leitura | Situação em 13/07 |
|---|---|
| Chart de movimento/NWE | ✅ seguia convergido, sem mudanças |
| Sinal direcional | ✅ seguia convergido, sem mudanças |
| **P(↑) dinâmico** | ✅ mecanismo corrigido — `App.jsx` agora pede `version=v2` igual à produção (`e5f513f`); volta a oscilar bar-a-bar em vez de travado |
| Convicção | ✅ mesma correção, consequência direta |
| PAR ATIVO / DIV Z | ✅ passa a existir, porque o V2 roda por padrão agora |
| Markers P VENDA no chart | ✅ engine emite eventos discretos (`e235c03`; endurecido por `0236f19` e `97f2cb7`) |
| GEX / MID | ✅ WIN **e** WDO (`39e6822`) |

### 2.1 O bug-raiz ✅ RESOLVIDO 2026-07-13

*Diagnóstico original (12/07):* `frontend/src/App.jsx:648` pedia `version=both`,
que o engine resolvia como **V1 estático**. O `scripts/firebase_sync.py` (linhas
48/66) já pedia **`version=v2`**.

> **Localhost mostrava V1. Produção mostrava V2. Ambos rotulados "DINÂMICO (KALMAN)".**

Consequências em cascata (tabela 2.0): P(↑) travado, convicção deslocada, PAR
ATIVO/DIV Z ausentes, painel de pesos Kalman exibindo pesos **estáticos**.

**Estado em 13/07:** corrigido junto com a Fase 3, como o plano exigia (não
podiam ser separadas — ver 2.3). `App.jsx` agora pede `version=v2` explicitamente
nos dois ramos de `fetchSeries` (commit `e5f513f`), com um comentário no próprio
código documentando a troca.

### 2.2 Estado verificado das fases (evidência reconferida em 2026-07-13)

| Fase | Tema | Status | Evidência |
|---|---|---|---|
| **2** | Contrato `version=both` | ✅ **RESOLVIDO** | `App.jsx:656` pede `version=v2` nos dois ramos, igual ao `firebase_sync.py` — bug-raiz fechado. Commit `e5f513f` |
| **3** | Ghost bars / pré-mercado | ✅ **RESOLVIDO** | Gate de pré-mercado corrigido em `engine.py`. Commit `28ecf2a`; endurecido por `ceec25d` (review Codex: só a sessão viva persiste o Kalman, e o gap intra-sessão passa a ser tratado como observação ausente, não retorno falso) |
| **4** | Persistência Kalman monotônica | ✅ **RESOLVIDO** | Guard monotônico implementado. Commit `01e0b9b`; caso de borda do replay histórico fechado por `ceec25d` |
| **1** | Schema `divergence_config` | 🔶 Parcial (sem mudança) | `init_db()` (`db.py:112`) segue rodando só `conn.executescript(SCHEMA)`; `migrate_divergence_config()` só roda se `db.py` for executado como script (`__main__`). Confirmado ainda assim em 13/07 |
| **5** | NWE causal (backend) | ❌ Pendente (sem mudança) | `get_center` ainda soma sobre `range(n)` inteiro (`main.py:288-295`) → lookahead de 1 barra confirmado ainda presente em 13/07 |
| **6** | API não-bloqueante | ❌ Pendente (sem mudança) | Nenhum single-flight/threadpool encontrado em `main.py` |
| **7** | Contrato Firebase completo | ❌ Pendente (sem mudança) | `firebase_sync.py` segue sem `history_closes` nem `is_b3` |
| **8.1** | Acurácia no detalhe (**B1**) | ✅ **RESOLVIDO** | Commit `e5f513f`; fix adicional em `82e3727` (o card de overview rotulava P(up) como "v1" com valor hardcoded) |
| **8.2** | Corridas de request | ✅ **RESOLVIDO** (bônus) | Não estava no pacote de trabalho ativo — resolvido de graça dentro do commit `39e6822` (GEX do dólar): guard de `reqId`/sequence-id em `fetchSeries` descarta respostas fora de ordem |
| **10** | Modularização `App.jsx` | 🔶 Parcial (sem mudança de fundo) | 5 charts extraídos, mas `App.jsx` tem **1.254 linhas** hoje (era ~1.200 em 12/07) |

### 2.3 Ordem de execução (histórico de como foi feito)

**Pacote A — destravar o V2 (o coração do plano) — ✅ 3/3 concluído**

1. ✅ **Fase 4 — guard monotônico do Kalman.** Era corrupção ativa em produção.
   Implementado com `ON CONFLICT ... WHERE excluded.timestamp > ...` +
   `persist_state=False` para replay histórico. Commit `01e0b9b`, endurecido por
   `ceec25d`.
2. ✅ **Fase 3 + B2 juntos (não foram separadas).** Corrigido o `win_return=0` de
   pré-mercado e então trocada a UI para `version=v2`, na ordem que o plano exigia
   — fazer B2 sozinho teria exposto na UI principal o caminho de pré-mercado ainda
   bugado. Commits `28ecf2a` + `e5f513f`.
   *Resultado: P(↑) dinâmico, convicção, PAR ATIVO e DIV Z voltaram a convergir com
   a produção — resolveu 4 das 7 linhas da tabela 2.0 de uma vez.*
3. ✅ **B1 — acurácia no detalhe.** `summary.accuracy` propagado para `seriesInfo`
   nos dois ramos de `fetchSeries`. Commit `e5f513f` + fix `82e3727`.

**Pacote B — o que sobra da divergência — 🔶 2/3 concluído**

4. ✅ **Markers de sinal (pair/z) por barra.** O `TVNweChart` já suportava os
   markers (`pair_compra`/`pair_venda`/`z_compra_val`/`z_venda_val`); o engine
   passou a emitir os eventos discretos, sem derivar de thresholds contínuos
   (decisão de projeto preservada). Commit `e235c03`, com dois fixes de review:
   `0236f19` (pair_signal invertia compra/venda para β>0) e `97f2cb7` (gap
   intra-sessão contaminava o pair z-score).
5. ⏳ **WDO — verificar a cesta — ainda em aberto.** A produção mostrava PAR ATIVO
   `isharescurrencybond+` no Mini Dólar, e esse fator já estava na nossa cesta do
   WDO — ou seja, a hipótese "cesta velha" nunca foi confirmada; o V1/V2 (Fase 2)
   provavelmente explicava a divergência sozinho. O plano previa recalibrar **só
   se**, depois do Pacote A, o WDO ainda divergisse — e essa checagem **ainda não
   foi feita**. `.planning/docs/FACTOR_MAP.md` está desatualizado (gerado em
   10/07, antes da cesta citada aqui) e não deve ser usado como fonte até ser
   regenerado. O feed Firebase de produção também exige auth (**401**) — a
   verificação precisa de outra via. *Item absorvido pelo Tactical Layer
   ([`2026-07-13-irai-tactical-layer-win-wdo.md`](./2026-07-13-irai-tactical-layer-win-wdo.md)
   §2.3) como pré-requisito da calibração micro do WDO.*
6. ✅ **GEX do dólar.** `gex_worker.py` passou a cobrir WIN$N e WDO$N num único
   ciclo (registry de targets, isolamento de falha por leg). Commit `39e6822`.

**Pacote C — higiene quantitativa (sem impacto visual imediato) — ❌ 0/4, nada iniciado**

> **Nota (2026-07-13):** o Pacote C inteiro — e o item 5 do Pacote B (cesta do
> WDO) — foi absorvido como pré-requisito de dois planos novos, onde o trabalho
> vai efetivamente acontecer:
> - [`2026-07-13-nwe-causal-backend-foundation.md`](./2026-07-13-nwe-causal-backend-foundation.md)
>   — a Fase 5 elevada a plano próprio (spec completa do NWE causal + VWAP/ATR).
> - [`2026-07-13-irai-tactical-layer-win-wdo.md`](./2026-07-13-irai-tactical-layer-win-wdo.md)
>   — absorve a Fase 1 (§2.2 migrações idempotentes), a Fase 6 e a B3/Fase 7
>   (§9 API/Firebase), e a verificação da cesta do WDO (§2.3).

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

### 2.5 Riscos da Parte 2 (atualizado — os riscos do Pacote A já foram consumidos)

- ~~Fase 4 é corrupção ativa hoje~~ — **mitigado** pelo guard monotônico
  (`01e0b9b`/`ceec25d`).
- ~~B2 sem a Fase 3 exporia o pré-mercado bugado na UI principal~~ — **mitigado**:
  as duas entraram juntas, como o plano exigia.
- **"Corrigir o sinal, depois polir o visual"** — segue valendo para o que resta:
  inverter a ordem deixaria o operador lendo sinais errados enquanto se ajusta
  crosshair/eixo (relevante para a Parte 3).
- **Markers derivados de threshold contínuo** viram spam — decisão preservada na
  entrega do item 4 do Pacote B: só eventos discretos.
- **Recalibrar o WDO sem necessidade** (item 5) gastaria uma janela e mexeria num
  modelo que, pela última medição conhecida, tinha a **melhor acurácia do par**
  (73,9%) — por isso o item segue como verificação, não recalibração automática.

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

## PARTE 3 — Backlog secundário (polimento de frontend)

Só entra **depois** do Pacote A da Parte 2 — condição **agora satisfeita** — mas a
Trilha C ainda depende da Fase 5 (NWE causal, Pacote C, ainda pendente) para não
polir em cima de um sinal errado. Segue bloqueada por esse motivo.

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
- ~~Fase 8.2 · `AbortController` em `fetchSeries`~~ — **já resolvido**, ver §2.2
  (Fase 8.2).
- **UX da produção não replicada:** hint "[Y-AXIS DESLOCADO — DUPLO CLIQUE PARA RESETAR]".
