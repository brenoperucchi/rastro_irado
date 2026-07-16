# IRAI — handoff de contexto para retomada em outra sessão/máquina

**Data:** 2026-07-16
**Última atualização:** 2026-07-16, noite — pós-revert do GEX (ver §4).

**Objetivo:** permitir que uma nova sessão (Claude ou Codex, no Ryzen ou em qualquer outra
máquina) retome o projeto sem depender do histórico desta conversa.

**Autoridade de escopo e sequência:** `docs/plans/2026-07-13-irai-plano-consolidado.md`.
**Regras operacionais:** `AGENTS.md`, `CLAUDE.md` e Backlog.md.
**Fontes de verdade do GEX (leitura obrigatória antes de qualquer mudança em
`compute_gex`):** `docs/indicadores/walls.txt` (código-fonte original NTSL/ProfitChart do
indicador do Miqueias) e `docs/plans/2026-07-16-regra-manual-miqueias-win.md` (especificação
derivada, backlog IRAI-19, ainda rascunho pendente de revisão do Miqueias).

## 1. Ambiente e fluxo de trabalho

- Produção no Ryzen/WSL: `/home/brenoperucchi/Devs/rastro_irado`.
- **Nota resolvida:** o path `/mnt/c/Users/brenoperucchi/devs/rastro_irado` mencionado em
  handoffs anteriores como "clone possivelmente divergente" foi conferido em 2026-07-16 —
  mesmo `HEAD`, mesmo `git status --short` (mesmos untracked) que `~/Devs/rastro_irado`. É a
  mesma localização física (mount WSL do C:\, não um segundo clone desatualizado). Não é mais
  uma fonte de suspeita para "frontend mostrando dado velho".
- API: `rastro-irado-api.service`, porta 8888. Lê `gex_levels` direto do SQLite a cada
  request, **sem cache** — não precisa reiniciar a API depois de rodar o worker do GEX.
- Collector: `rastro-irado-collector.service`.
- Frontend dev: porta 5175.
- GEX: `rastro-irado-gex.timer` (systemd --user), agendado para 09:10 BRT dias úteis, dispara
  `rastro-irado-gex.service` (oneshot). Rodar manualmente com
  `systemctl --user start rastro-irado-gex.service`; acompanhar com
  `journalctl --user -u rastro-irado-gex.service -n 30 --no-pager`.
- MT5 é executado pelo Python do Windows (`py.exe -3.12 -X utf8`), ainda que os serviços
  sejam orquestrados pelo systemd do WSL.
- Não desenvolver diretamente no checkout de produção sem necessidade — mas nesta rodada o
  fix/revert do GEX foi aplicado direto em produção (`~/Devs/rastro_irado`) com autorização
  explícita do usuário, via commit + push + pull, não edição solta.

## 2. Estado do projeto

O IRAI é um painel pessoal de apoio à decisão. Ele não executa ordens. O plano busca separar:

- regime macro (`P_up`);
- estrutura de mercado (GEX/walls);
- confirmação tática (Pair/Z/NWE);
- executabilidade e resultado econômico;
- somente depois, distribuição, shadow live e eventual Execution Layer MT5.

Backlog relevante em 2026-07-16:

- IRAI-4 — NF-01B/VAL-04: em andamento.
- IRAI-5 — rollover WIN/WDO: em andamento; WIN medido, WDO pendente.
- IRAI-17 — comparação `P Dinâmico` Miqueias × v1 × v2: em Review.
- IRAI-19 — especificação da regra manual do Miqueias (`docs/plans/2026-07-16-regra-manual-miqueias-win.md`):
  rascunho, pendente de revisão do Miqueias nas ambiguidades da §6 (thresholds, região GEX
  válida, alvo/stop/cooldown/invalidação, papel do NWE, peso IBOV vs. DOL). **Foi este
  documento, junto com `docs/indicadores/walls.txt`, que invalidou o fix do GEX descrito
  abaixo** — releia antes de mexer em `compute_gex` de novo.
- IRAI-21 — challenger Pair fixo WIN–WDO: concluído e em Review (ver §6).
- IRAI-22 — histórico causal GEX: fonte oficial B3/BCB concluída; metodologia do Gamma Flip
  em aberto (ver §4.3).
- IRAI-7 — decisão de promoção econômica: bloqueado por IRAI-4 e IRAI-5.
- NF-02/03/04 e VAL-05 permanecem condicionados ao gate econômico.

## 3. Pipeline GEX — fonte de dados (já concluído e estável)

Commits publicados nesta frente (não tocados nesta rodada):

- `3155c98` — unifica o GEX WIN live com o bundle oficial causal (SPRE/PE/IR/SPRD B3 +
  Selic BCB SGS 1178).
- `d70f273` — fecha documentação e backlog do rollout.
- `39e6822` — estende GEX de WIN$N para WDO$N via opções DOL (cadastro B3, sem cobertura
  MT5 pra opção de dólar).

Isso está estável: live e backfill usam o mesmo cálculo, proveniência com SHA-256, bundle
ausente falha fechado, timer roda 09:10 BRT pós-primeira-M5. **Não é o que quebrou** nesta
rodada — o que quebrou foi a etapa de PLOTAGEM (§4).

## 4. O que aconteceu nesta sessão: fix errado do grid de walls, revert

### 4.1 O sintoma relatado

Usuário reportou que visualmente o GEX "ainda não deu certo" mesmo com a fonte de dados já
corrigida (§3). Diagnóstico confirmou a API correta (`active=true`, `gamma_flip=186364`
WIN, `spot=176011` IBOV, `f=1.010415`, 97 strikes) — o problema não era o cálculo dos 3
níveis reais (`gex_max`/`gex_flip`/`gex_min`), e sim que o **grid decorativo** de 17 "wall" +
16 "mid_wall" (linhas uniformes de referência, sem gamma próprio) ficava inteiro **acima**
do preço, porque era centrado no Flip e o Flip está ~5% acima do spot (IBOV é
estruturalmente put-heavy).

### 4.2 O fix que eu implementei e que estava ERRADO

Commit `2cca41d` mudou `centro` de `round(flip*f/(grid_step*f))*grid_step` (centrado no
Flip) para `round(spot/grid_step)*grid_step` (centrado no preço), pra garantir que o grid
sempre cobrisse o preço. Adicionei um teste de regressão (`94f9eca`) travando esse
comportamento. **Validei numericamente contra a API de produção e rodei um `/tri-review`
(deep-reasoner + codex — fable-reasoner falhou 3x por erro de infraestrutura 529
Overloaded, não por conteúdo) que confirmou ausência de bug de escala.**

O que meu processo não fez: **procurar se já existia uma especificação documentada da regra
de plotagem antes de mudar a fórmula.** Havia — e ela contradiz frontalmente o meu fix.

### 4.3 O achado que derrubou o fix: revisão externa NO-GO

Um revisor externo (rodado pelo usuário, fora desta sessão) devolveu **NO-GO** apontando,
entre outros pontos, que `docs/plans/2026-07-16-regra-manual-miqueias-win.md:132` já
documentava a regra ancorada no Flip, e que mudar pra spot era mudança de regra de negócio,
não correção geométrica, sem evidência que autorizasse a divergência.

Investiguei a fundo e confirmei que o achado está certo, com uma fonte ainda mais primária:
`docs/indicadores/walls.txt` é o **código-fonte original NTSL/ProfitChart do indicador do
Miqueias** (formato ProfitChart, linguagem NTSL). Linha 22, inequívoca:

```
Centro := Round(GammaFlip / (Espacamento * FatorConversao)) * Espacamento;
```

**Centra sempre no GammaFlip, nunca no spot.** Isso é exatamente a fórmula que eu tinha
substituído. `docs/plans/2026-07-16-regra-manual-miqueias-win.md` (criado no mesmo dia,
backlog IRAI-19, aparentemente por uma sessão paralela que eu não tinha lido) já
documentava essa regra em `§4.1.3`, junto com a interpretação de cor
(`CorWallsAlta`/`CorWallsBaixa` = lado do Flip, não tem nada a ver com o preço).

**Conclusão:** o grid ficar inteiro de um lado do preço num mercado put-heavy é o
comportamento CORRETO e FIEL do indicador original — é a informação (o zero-gamma mais
próximo está longe), não um bug de desenho a esconder recentrando no preço. Meu fix
"resolvia" o sintoma visual mascarando um sinal real.

### 4.4 O que foi revertido (estado atual, já implantado e verificado)

Commit `27ff077` reverte `2cca41d`: `centro` voltou a `round(flip*f/(grid_step*f))*grid_step`,
idêntico ao NTSL original. O teste `94f9eca` foi reescrito (não removido) pra travar o
comportamento CORRETO (centro em Flip) com o mesmo cenário put-heavy sintético — sob a
fórmula errada essa nova asserção falharia. Suíte: **75 passed, 8 skipped**.

Implantado no Ryzen (`~/Devs/rastro_irado`, `git pull` até `27ff077`), worker do GEX
re-executado via `systemctl --user start rastro-irado-gex.service`, e confirmado via API:

```
gamma_flip: 186364.05 | spot*f: 177844.0 | walls grid: 177833 .. 194000
```

Grid de volta ao lado do Flip, preço na borda inferior — fiel ao original.

### 4.5 Achado paralelo, já resolvido: o screenshot do usuário não era GEX

Durante a investigação, o usuário comparou um print do dashboard contra a leitura visual do
Miqueias. Achei que fossem walls de GEX com posições erradas — mas os valores do print
(`176700.12`/`176167.20`/`175361.03`/`175235.00`/`174554.85`) batiam EXATAMENTE com
`nwe_upper_price`/`nwe_center_price`/`win_current` da API de série (`/api/irai/series`), não
com `gamma_max`/`gamma_flip`/`gamma_min`. O toggle de exibição de GEX estava desligado; o
print mostrava as bandas do NWE + o preço no crosshair, nada relacionado a GEX. **Não
reabrir essa pista** — já foi confirmado e descartado com evidência direta do endpoint.

### 4.6 O que continua genuinamente em aberto (não resolvido por este revert)

O revert restaura fidelidade ao indicador original, mas **não valida se o nosso cálculo de
GammaFlip/netGEX está correto** — ou seja, se o Flip calculado por `compute_gex`
(`backend/workers/gex_worker.py`) bateria com o que o próprio Miqueias calcularia
externamente (ele usa os 3 valores como `Input` manual no NTSL — não há, no
`walls.txt`, nenhuma lógica de CÁLCULO do Flip, só de PLOTAGEM). Isso é a pendência real,
mais profunda, já sinalizada no handoff anterior (versão de hoje à tarde) e ainda não
atacada:

- netGEX strike a strike, sinal e convenção call/put;
- definição de Gamma Flip como zero do cumulativo vs. seleção quando há múltiplos
  cruzamentos;
- agregação por vencimento (BSM/IV por vencimento vs. único);
- unidades/escala de OI e gamma;
- conversão IBOV→WIN;
- comparação com valores brutos reais do Miqueias em sessões comuns (não há transcrição
  disponível — `docs/plans/2026-07-16-regra-manual-miqueias-win.md` §6.2 já marca isso como
  pendência sem fonte).

**Não escolher uma fórmula nova só porque produz um gráfico parecido.** A decisão precisa
ser reproduzível e economicamente defensável, e qualquer mudança futura em `compute_gex`
deve primeiro reler `docs/indicadores/walls.txt` e
`docs/plans/2026-07-16-regra-manual-miqueias-win.md` por inteiro.

## 5. Prompt de retomada recomendado

```text
Leia integralmente AGENTS.md, CLAUDE.md,
docs/handoffs/2026-07-16-irai-contexto-atual.md,
docs/plans/2026-07-13-irai-plano-consolidado.md,
docs/plans/2026-07-16-regra-manual-miqueias-win.md,
docs/indicadores/walls.txt,
e a tarefa relevante no Backlog.md (IRAI-19, IRAI-22).

O grid de walls do GEX (backend/workers/gex_worker.py::compute_gex) foi corrigido para
centrar no PREÇO (spot) em vez do Gamma Flip (commit 2cca41d), depois REVERTIDO (commit
27ff077) porque contradizia o indicador NTSL original (docs/indicadores/walls.txt:22) e a
especificação já registrada (docs/plans/2026-07-16-regra-manual-miqueias-win.md §4.1.3),
ambos ancorando sempre no Flip. Estado atual (27ff077) é o correto/fiel ao original — NÃO
reintroduzir o centro-no-spot sem evidência nova.

A pendência real e ainda não resolvida é se o CÁLCULO do GammaFlip/netGEX
(backend/workers/gex_worker.py, BSM/IV/netGEX/cruzamento de zero) é fiel ao que o Miqueias
calcularia externamente — o walls.txt só define a PLOTAGEM a partir de um Flip já dado como
Input, não como esse Flip é calculado. Investigar: sinal/convenção call-put, seleção entre
múltiplos zero-crossings, agregação por vencimento, unidades, conversão IBOV→WIN. Não
mudar a fórmula de plotagem de novo sem entender primeiro se o Flip calculado está certo.
```

## 6. Outras frentes desta sessão (contexto adicional, não bloqueante pro GEX)

- **IRAI-21 — challenger Pair fixo WIN–WDO**: concluído, artefato em `docs/artifacts/irai-21/`.
  Metodologia congelada em `docs/plans/2026-07-16-challenger-pair-fixo-win-wdo.md` antes dos
  resultados. Resultado: todos os sinais (fixo, dinâmico, baselines) são negativos líquidos
  de custo em ambos os alvos — fixar o par não recupera edge. Revisado por `/fable-reasoner`.
- Revisões `/fable` + `/deep` aplicadas a commits anteriores de NWE (achados de um
  `/tri-review` anterior, implementados no commit `5e1fe69` e leva seguinte).

## 7. Comandos operacionais úteis

```bash
# Estado de produção
ssh ryzen5wsl
cd /home/brenoperucchi/Devs/rastro_irado
git status --short
git rev-parse HEAD   # deve ser >= 27ff077
systemctl --user is-active rastro-irado-api rastro-irado-collector rastro-irado-gex.timer
curl -fsS 'http://127.0.0.1:8888/api/irai/gex?target=WIN%24N'

# Rodar o worker de GEX manualmente (após pull)
systemctl --user start rastro-irado-gex.service
journalctl --user -u rastro-irado-gex.service -n 30 --no-pager

# Testes GEX
pytest -q tests/test_gex_worker.py tests/test_api_gex_endpoint.py \
  tests/test_backfill_gex_history.py tests/test_gex_frontend_contract.py
```

Preservar bancos, logs, caches, artefatos e alterações do usuário. Nunca limpar o checkout
de produção com comandos destrutivos.
