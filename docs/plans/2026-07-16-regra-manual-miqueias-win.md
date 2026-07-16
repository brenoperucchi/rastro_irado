# Especificação da regra manual do Miqueias — WIN

**Projeto:** IRAI — Intraday Risk Appetite Index
**Criado:** 2026-07-16
**Revisado:** 2026-07-16 — corrige §4.1/§6.7/§7: GEX já está integrado (não é mais
`Input` externo), fonte documentada; provenança verificada contra o repositório público
`miqueiasa1/wdowin_pairtrading`.
**Status:** Rascunho para revisão do Miqueias — nenhuma parte foi promovida a `CONFIRMADO`
**Referência de tarefa:** backlog IRAI-19 (assignee `@claude`), refs `IRAI-17`
**Papel documental:** especificação determinística e revisável de uma leitura hoje
discricionária. Não implementa código, não altera runtime de produção, não continua os
itens estatísticos do NF-01 (`docs/plans/2026-07-14-divergence-strategy-vs-tactical-layer.md`
§11).

## 1. Objetivo e escopo

Transformar a leitura discricionária do Miqueias — GEX/walls/mid-walls, Pair Spread, NWE e
regime macro `P_up` — numa tabela de decisão determinística para WIN, com barra fechada,
primeiro preço executável, alvo, stop e invalidação explícitos ou marcados como decisão
pendente.

Fora de escopo deste documento:
- qualquer implementação (backend, frontend, script de medição);
- qualquer promoção de setup a `CONFIRMADO` (decisão 9 das regras de negócio, §8 abaixo);
- continuação dos itens 4-6 do backtest NF-01 (regime `P_up`, NWE, baselines) — esses ficam
  congelados enquanto este documento e o ledger diário (IRAI-18) avançam em paralelo.

## 2. Fontes usadas

- `docs/plans/2026-07-15-irai-visao-negocio-miqueias.md` — visão de negócio e as 10 regras.
- `docs/plans/2026-07-14-divergence-strategy-vs-tactical-layer.md` §2-§7 — vocabulário
  canônico, comparação imagem/código/plano, conflitos de threshold já identificados.
- `docs/explenation.jpeg` — imagem anotada "Marcações de Compra e Venda" (Pair Spread +
  macro), 4 eventos de exemplo.
- `docs/indicadores/walls.txt` — fonte ORIGINAL do indicador visual de GEX (formato
  ProfitChart/NTSL) — hoje só a parte de PLOTAGEM das 17 walls/16 mid-walls a partir de
  `GammaMax`/`GammaMin`/`GammaFlip` já calculados; a integração real (§4.1) substituiu o
  cálculo desses três valores.
- `docs/indicadores/gaussiana.txt` — fonte do NWE original do Miqueias (envoltória
  gaussiana + regra de toque de banda).
- `docs/indicadores/hist_zscore.txt` — histograma de fluxo institucional, protótipo.
- `backend/irai/nwe.py`, `backend/irai/zscore.py`, `backend/irai/engine.py` — implementação
  atual no IRAI (para confirmar o que já existe versus o que a leitura manual assume).
- `backend/workers/gex_worker.py` (commits `4dd1273..39e6822`) — worker EOD que calcula
  GEX de verdade (BDI/B3 + MT5 XP, BSM/IV, netGEX call-put, Gamma Flip, conversão
  IBOV→WIN) e grava em `gex_levels`. Ver §4.1 — substitui integralmente a suposição
  anterior deste documento de que GEX não estava integrado ao IRAI (§6.7/§7, corrigidas
  nesta revisão).
- Repositório público `miqueiasa1/wdowin_pairtrading` (branch `main`, HEAD `7fce5bc`, 17
  commits) — verificado nesta revisão via `gh search code` (GEX/gamma/netGEX/GammaFlip:
  zero resultados) e via `git log --all -p` sobre um clone completo do histórico (mesmo
  resultado, zero ocorrências de "gamma"/"gex"/"netgex"/"gammaflip"/"open interest"/
  "black scholes"/"bsm" em qualquer commit). **Não contém código GEX localizável** — o
  worker de GEX deste projeto não deriva desse repositório público.

Não existe, até este documento, uma transcrição direta do Miqueias descrevendo passo a passo
sua leitura discricionária além do que essas fontes cobrem — a tabela de decisão abaixo é uma
**reconstrução a partir da evidência disponível**, com toda lacuna marcada explicitamente na
§6, não uma transcrição literal.

## 3. Papéis separados dos componentes (sem dupla contagem — regra de negócio 6)

| Componente | Papel | O que NÃO é |
|---|---|---|
| `P_up` (regime macro) | Gate de regime: permite ou bloqueia a FAMÍLIA de setups (comprador/vendedor/neutro). | Não é sinal de entrada nem confirma direção de curto prazo (walk-forward do macro, §8 do plano consolidado). |
| GEX (walls/mid-walls) | Mapa estrutural de região candidata — onde o preço pode reagir. Calculado 1x/dia (EOD, pré-abertura), FIXO durante toda a sessão — não recalculado intrabar (§4.1). | Não é sinal automático (regra 5); tocar uma wall não confirma operação. |
| Pair Spread (`z_pair`) | Confirmação/gatilho de distorção relativa contra o fator ativo do Kalman. | Não mede distância contra o `P_up` diretamente (§2 do plano de divergência). |
| Divergência macro-preço (`Z`) | Confirmação/gatilho alternativo: preço não acompanhou o extremo do `P_up`. | Não é o mesmo que o Pair Spread — os dois podem discordar (achado C1-a/NF-01 item 3). |
| NWE | Região/direção local do preço (linha central, bandas, inclinação). | Hoje só expõe dados descritivos (`nwe_direction`, `nwe_upper_price`, `nwe_lower_price`, `nwe_slope_price`) — **não existe no backend atual um evento discreto de "toque de banda"** como o `BuyAtMarket`/`SellShortAtMarket` de `gaussiana.txt` (ver §6.5). |
| ATR | Escala de distância, risco e invalidação. | Não decide direção. |
| Qualidade dos dados | Gate binário: barra fechada, não-ghost, não-stale, dentro do pregão, rollover correto. | Não é um componente de decisão direcional — é pré-requisito para QUALQUER decisão. |
| Fluxo real de agressão | Candidato futuro (`hist_zscore.txt`). | **Fora do gate agora** — o delta atual do IRAI é aproximado, não é fluxo institucional real (vision doc, confirmado). |

## 4. Definições formais

### 4.1 GEX — Gamma Max/Min/Flip, walls e mid-walls (integração real)

**Correção desta revisão**: as versões anteriores deste documento (e a leitura inicial de
`docs/indicadores/walls.txt`) tratavam `GammaMax`/`GammaMin`/`GammaFlip` como valores
`Input` externos, sem fonte conhecida. Isso está desatualizado — `backend/workers/
gex_worker.py` (commits `4dd1273..39e6822`) já calcula os três a partir de dados reais e
grava em `gex_levels` (SQLite), rodando 1x/dia (EOD, pré-abertura) via serviço agendado.
`walls.txt` continua válido, mas só para a etapa final de PLOTAGEM (§4.1.3) — a fonte dos
três valores de entrada agora é o worker, não mais um `Input` manual.

#### 4.1.1 Fontes de dado

- **Open Interest (OI) por série de opção**: API pública do BDI/B3
  (`arquivos.b3.com.br/bdi/table/OpenPositionsEquities/{data}/{data}/{página}/1000`,
  paginada, `sort=TckrSymb` obrigatório — sem isso a paginação B3 duplica/perde linhas).
- **Strike, call/put, vencimento e prêmio EOD**: terminal MT5 XP — as ~2396 séries
  `IBOV*` existem só nesse terminal; `session_close` do símbolo é o prêmio EOD (fallback
  em barra D1 datada só para strikes próximos do ATM, ou em reprocessamento histórico via
  `--date`). Para WDO$N (extensão `39e6822`, via opções de DOL), não há cobertura de opção
  no MT5 — strike/CP/vencimento vêm do cadastro oficial B3 (`InstrumentsDerivatives`), e
  a IV usa volatilidade realizada horizon-matched em vez de prêmio invertido (§4.1.2).
- **Spot/settle**: fechamento D1 do MT5 — IBOV (spot) e WIN$N (settle) para a perna WIN;
  DOL$N (spot) e WDO$N (settle) para a perna WDO — join por ticker entre BDI (OI) e MT5
  (metadados), NUNCA symbol_select por série (uma chamada em lote `symbols_get('IBOV*')`
  evita o custo que levou até 3h37 nas primeiras execuções).

#### 4.1.2 Cálculo (BSM/IV, netGEX, Gamma Flip/Max/Min)

```text
Γ(K)      = BSM_gamma(spot, K, T, r, σ), com q=0 (sem dividendo — WIN$N; para WDO$N
            usa r=0, gamma sem termo de drift, equivalente a Black-76 — futuro sobre futuro)
σ(venc)   = mediana da IV invertida (bisseção sobre o prêmio EOD, 80 iterações) dos strikes
            até ±15% do spot (MONEYNESS_IV) daquele vencimento; se não houver prêmio
            invertível (ex.: WDO$N, sem cobertura de opção no MT5), usa volatilidade
            realizada anualizada (close-to-close, janela horizon-matched aos dias até o
            vencimento) — NUNCA um valor fixo tipo 0,20 (nível de índice, não de FX)
netGEX(K) = Σ_venc [ Γcall(K,venc)·OIcall(K,venc) − Γput(K,venc)·OIput(K,venc) ]
GammaFlip = cruzamento de zero do netGEX CUMULATIVO por strike (interpolação linear),
            escolhendo o cruzamento mais próximo do spot quando há mais de um
GammaMax/Min = argmax/argmin do netGEX por strike, com refino parabólico (vértice da
            parábola pelos 3 pontos vizinhos, só quando os strikes vizinhos são
            equidistantes)
```

Gates de validade (calculados, não aparecem em `walls.txt`): `liquid` exige >= 8 strikes
com `netGEX != 0` dentro de ±5 grades do spot; `valid` exige `liquid`, Gamma Flip
existente e o Flip a menos de 15 grades do spot. A posição do Flip acumulado em relação
aos extremos pontuais GammaMax/GammaMin é alerta diagnóstico, não bloqueio. Um GEX marcado `valid=false`
**não deve alimentar a tabela de decisão** (mesma lógica do gate de qualidade de dados,
regra de negócio 4).

Correção validada em 16/07/2026 (`a13a547`/`7f12db0`): o gate antigo confundia o zero
do GEX acumulado com os extremos do netGEX pontual. A reclassificação causal, sem alterar
níveis ou fontes, promoveu 46 sessões e mudou a cobertura de 73/200 para 119/200 válidas;
81 permanecem inválidas por Flip ausente (50) ou distante demais do spot (31).

#### 4.1.3 Conversão IBOV→WIN e plotagem (walls/mid-walls)

```text
f (basis)  = win_settle / spot                      (IBOV→WIN; análogo DOL$N→WDO$N)
Centro     = round(GammaFlip × f / Espacamento) × Espacamento     (Espacamento inferido
             pela mediana do gap entre strikes líquidos perto do spot p/ WDO$N; fixo em
             1000 pontos p/ WIN$N, validado em produção)
Wall_i     = (Centro + i × Espacamento) × f,        i ∈ {-8..+8}
Mid_i      = (Centro + (i + 0.5) × Espacamento) × f, i ∈ {-8..+7}
```

17 walls e 16 mid-walls resultam, exatamente como `walls.txt` já descrevia — a diferença é
que `GammaFlip`/`Centro` agora vêm do cálculo real (§4.1.2), não de um `Input` manual. Cor/
espessura codificam só a posição relativa ao `GammaFlip` (acima = alta/CALL, abaixo =
baixa/PUT) e a distância ao centro (mais espessa perto do ATM) — isso é visualização, não
regra de decisão.

Para WDO$N especificamente, há um `f_sanity_clamp`: se o fator de conversão `f` se afastar
de 1,0 além de 0,5% (dólar futuro cheio negocia esparso e pode gerar um last-trade não
representativo), o worker usa `f=1.0` e registra aviso — não existe esse clamp para
WIN$N/IBOV, cujo basis reflete carry real, não ruído.

**Pendente (§6.2):** não há, em nenhuma fonte disponível, uma definição de "zona de
proximidade" (quantos pontos ou % de distância de uma wall contam como "preço na região").

### 4.2 Pair Spread (`z_pair`) — já implementado

Fonte: `backend/irai/zscore.py`, sem alterações necessárias.

```text
par_ativo = argmax |βᵢ(t)|                 (maior beta válido do Kalman, min_beta=PAIR_MIN_BETA)
resíduo_t = retorno_target_t − β_t × retorno_fator_t
z_pair_t  = (resíduo_t − média_rolling) / desvio_rolling     (janela PAIR_SIGMA_WINDOW=20 barras)
```

Threshold de produção: `PAIR_THRESHOLD` (±1,5 por padrão) — **diferente** do ±2 mostrado na
imagem `explenation.jpeg` (conflito já documentado em
`2026-07-14-divergence-strategy-vs-tactical-layer.md` §2 e §6).

### 4.3 Divergência macro-preço (`Z`) — já implementado

Fonte: `backend/irai/engine.py`. `P_up > p_up_gate_hi` (default 55) e retorno normalizado
abaixo de `-threshold` → `Z COMPRA`; `P_up < p_up_gate_lo` (default 45) e retorno acima de
`+threshold` → `Z VENDA`. **Diferente** do 60/40 mostrado na imagem (mesmo conflito).

### 4.4 NWE — parcialmente implementado

Fonte atual: `backend/irai/nwe.py` — linha central (média gaussiana causal), bandas
(`nwe_upper_price`/`nwe_lower_price`), inclinação (`nwe_slope_price`), direção
(`nwe_direction`: "up"/"down"/"flat"/None). **Não existe** hoje um campo equivalente ao
toque de banda discreto (`Close <= fLower and Close[1] > fLower` → compra) da fonte
original do Miqueias (`gaussiana.txt`).

### 4.5 Regime `P_up`

Contexto/regime, não sinal direcional de curto prazo (achado do walk-forward do macro,
`2026-07-14-divergence-strategy-vs-tactical-layer.md` §2 e §8). Threshold de produção:
`p_up_gate_hi=55` / `p_up_gate_lo=45` — **diferente** do 60/40 da imagem.

## 5. Tabela de decisão (rascunho — sujeito às pendências da §6)

Pré-requisitos válidos para QUALQUER linha (gate de qualidade, regra de negócio 1-2):

- barra fechada (nunca `bar_may_be_forming`, achado X3);
- não-ghost, não-pré-mercado;
- dentro do horário de pregão do WIN (09:00-18:00 BRT);
- rollover do contrato corrente confirmado (IRAI-5, ainda auditando);
- sem cooldown ativo de uma decisão anterior (duração: §6.3).

| # | Condição (regime × região × confirmação) | Direção | Barra fechada? | 1º preço executável |
|---|---|---|---|---|
| 1 | `P_up` em regime comprador **(threshold: §6.1)** E preço na região GEX de suporte **(definição: §6.2)** E (`z_pair <= -PAIR_THRESHOLD` OU `Z COMPRA` ativo) | CANDIDATO_COMPRA | Sim — nunca em barra em formação (achado X3) | Fechamento da barra SEGUINTE à do sinal (mesma convenção do NF-01 — nunca o preço da própria barra do sinal) |
| 2 | `P_up` em regime vendedor **(threshold: §6.1)** E preço na região GEX de resistência **(definição: §6.2)** E (`z_pair >= +PAIR_THRESHOLD` OU `Z VENDA` ativo) | CANDIDATO_VENDA | Sim | Idem |
| 3 | Regime `P_up` neutro, OU região GEX indefinida (sem dado do dia), OU nenhuma confirmação Pair/Z, OU gate de qualidade falha, OU cooldown ativo | NAO_OPERAR | — | — |
| 4 | Confirmação Pair/Z presente mas região GEX ausente/indefinida | NAO_OPERAR (região é pré-requisito, regra de negócio 5 — GEX não pode ser ignorado) | — | — |
| 5 | Pair e `Z` discordam em direção (um aponta compra, outro venda) | NAO_OPERAR — evidências correlacionadas não se cancelam nem se somam como votos (regra 6); tratar como sinal ambíguo até haver critério de desempate (§6.6) | — | — |

Nomenclatura deliberada: `CANDIDATO_COMPRA`/`CANDIDATO_VENDA`, não `COMPRA`/`VENDA` — nenhuma
linha desta tabela equivale a `CONFIRMADO` na máquina de estados do Tactical Layer (§3.5 do
plano de divergência). Confirmação, se um dia existir, é uma camada adicional (NWE como
gatilho de entrada fina, ainda não especificado — §6.5) sobre a região aqui definida.

## 6. Ambiguidades identificadas (decisão pendente do Miqueias)

### 6.1 Threshold do regime `P_up`: 55/45 ou 60/40?

Produção usa 55/45 (`DEFAULT_P_UP_GATE_HI`/`LO`, thresholds canônicos já unificados entre
backend e frontend nesta mesma linha de trabalho). A imagem `explenation.jpeg` mostra 60/40.
**Pendente:** qual threshold vale para a regra tática — adotar o canônico de produção, ou o
60/40 da leitura visual do Miqueias (e nesse caso, por que produção diverge)?

### 6.2 Definição de "região GEX válida"

Mesmo com a integração real (§4.1), `backend/workers/gex_worker.py` só define a GEOMETRIA
das 17 walls + 16 mid-walls e os gates de VALIDADE do cálculo inteiro (`liquid`/`valid`,
§4.1.2) — não define quão perto o preço precisa estar de uma wall/mid-wall individual para
contar como "região candidata" de uma operação. **Pendente:** distância em pontos, em ATR,
ou toque exato da linha? A cor de cada wall (`CorWallsAlta`/`CorWallsBaixa`, ou
`#84CC16`/`#EF4444` no worker) só indica o lado do `GammaFlip`, não serve como regra de
proximidade.

### 6.3 Cooldown

O backtest NF-01 usa `COOLDOWN_BARS=20` (mesmo tamanho do maior horizonte de medição, para
evitar sobreposição de janelas de medição — não é uma escolha de negócio, é uma escolha
estatística). **Pendente:** o Miqueias usa algum cooldown discricionário na prática (tempo
mínimo entre operações no mesmo sentido, ou após uma invalidação)?

### 6.4 Alvo, stop e invalidação

Nenhuma fonte disponível especifica:
- **Alvo:** candidatos possíveis não escolhidos — próxima wall/mid-wall na direção do
  movimento, múltiplo de ATR, ou retorno ao `GammaFlip`/Centro.
- **Stop:** candidatos não escolhidos — múltiplo de ATR, wall/mid-wall imediatamente
  contrária, ou nível fixo em pontos.
- **Invalidação:** candidatos não escolhidos — `z_pair` retorna a zero sem atingir o alvo,
  rompimento da wall contrária, ou tempo máximo em barras sem confirmação.

Regra de negócio 10 já estabelece que `NAO_OPERAR` é uma decisão válida e que a aprovação
pode ser revogada por drift — mas isso não substitui a definição destes três parâmetros.

### 6.5 Papel exato do NWE nesta regra

A fonte original (`gaussiana.txt`) tem uma regra de ENTRADA discreta (toque de banda com
retorno: `Close <= fLower and Close[1] > fLower` → compra). O IRAI atual só expõe dados
descritivos (direção, bandas, inclinação — §4.4), sem esse evento discreto implementado.
**Pendente:** o NWE deve ser (a) só um filtro de região/tendência (concordância de direção
com a candidata de compra/venda), ou (b) o gatilho de ENTRADA FINA depois que GEX+Pair/Z já
qualificaram uma região candidata (mais próximo do desenho original do Miqueias)? Essas são
duas arquiteturas de decisão diferentes.

### 6.6 Critério de desempate Pair vs. Z

Quando o Pair Spread e a divergência `Z` apontam direções diferentes na mesma barra (achado
do NF-01 item 3: a interseção dos dois é rara e, quando ambos concordam, ainda não há edge
confiável comprovado — `2026-07-14-divergence-strategy-vs-tactical-layer.md` §11.4/§11.6),
a tabela de decisão (linha 5) marca isso como `NAO_OPERAR` por padrão. **Pendente:**
confirmar com o Miqueias se essa é a leitura correta, ou se ele prioriza um dos dois
sinais nesse caso.

### 6.7 Fonte e atualização do GEX — RESOLVIDA nesta revisão

`GammaMax`/`GammaMin`/`GammaFlip` NÃO são mais `Input` externos manuais — vêm de
`backend/workers/gex_worker.py` (§4.1), que roda 1x/dia (EOD, pré-abertura, requer o
collector PARADO — MT5 aceita só 1 conexão por terminal/processo) e grava em `gex_levels`.
Fonte primária: BDI/B3 (open interest) + MT5 XP (strike/CP/vencimento/prêmio/spot/settle).
Não há atualização intraday — os níveis ficam FIXOS durante toda a sessão (§3).

Conexão com o gate de qualidade (regra de negócio 4): o worker já grava um campo `valid`
(§4.1.2) por sessão — um GEX com `valid=0` deve ser tratado como "GEX indisponível" pela
tabela de decisão (linha 3/4 da §5), não como "sem distorção". Isso ainda não está
explicitado como parte formal do gate de qualidade desta especificação — recomendação para
a próxima revisão, não uma pendência do Miqueias (é decisão de engenharia, não de negócio).

**Pendência residual, agora mais estreita**: o worker cobre WIN$N (via IBOV) e, desde
`39e6822`, WDO$N (via DOL) — mas este documento é escopado só a WIN (§1). Falta confirmar
se a leitura discricionária do Miqueias trata os walls do IBOV (índice) e do DOL (câmbio)
com o mesmo peso, ou se um pesa mais que o outro na decisão do WIN.

## 7. Dados ainda ausentes

- **Fluxo real de agressão** (`hist_zscore.txt`): protótipo externo, sem dado real de
  agressão no IRAI. Fica fora do gate até existir uma fonte de dado real (vision doc,
  confirmado).
- ~~GEX intraday: não integrado~~ **Corrigido nesta revisão** — GEX está integrado desde
  `backend/workers/gex_worker.py` (commits `4dd1273..39e6822`), rodando 1x/dia (EOD), não
  intraday. Ver §4.1/§6.7. O badge `GEX`/`MID` visível em `explenation.jpeg` já reflete
  esse worker, não um dado externo solto.

## 8. Conformidade com as regras de negócio (vision doc)

| Regra | Como esta especificação respeita |
|---|---|
| 1. Marker é observação, não entrada | Tabela usa `CANDIDATO_COMPRA/VENDA`, nunca `COMPRA/VENDA` confirmados. |
| 2. Nenhum evento nasce de candle aberto | §5, pré-requisitos: barra fechada sempre, mesma convenção causal do achado X3. |
| 3. Backtest e live usam a mesma regra/timestamps/1º preço | §5 especifica explicitamente o 1º preço executável (barra seguinte ao sinal), replicando a convenção já usada e revisada no NF-01. |
| 4. Custos/rollover/horário/qualidade fazem parte do resultado | §5 pré-requisitos inclui rollover e horário; custo fica para a fase de medição (fora de escopo deste doc). |
| 5. GEX é região, não sinal automático; toque não confirma | §3 e §5 tratam GEX como pré-requisito de região, nunca como gatilho isolado. |
| 6. Evidências correlacionadas não são somadas como votos | §5 linha 5 trata divergência Pair×Z como `NAO_OPERAR`, não soma. |
| 7. IRAI e GEX devem provar valor incremental | Fora de escopo deste documento — é o próximo passo de medição (§9), não desta especificação. |
| 8. Regra simples vence modelo complexo em empate | Nenhuma complexidade adicional foi introduzida além do que já existe implementado. |
| 9. Nada chega a `CONFIRMADO` sem OOS líquido de custo | Este documento não promove nada — é insumo para medição futura. |
| 10. `NAO_OPERAR` é decisão válida; aprovação pode ser revogada | §5 trata `NAO_OPERAR` como resultado de primeira classe, não uma ausência. |

## 9. Próximos passos

1. Miqueias resolve as ambiguidades da §6 (thresholds, região GEX, alvo/stop/cooldown/
   invalidação, papel do NWE, critério de desempate, peso relativo IBOV vs. DOL no GEX do
   WIN — §6.7). A fonte do GEX em si já está resolvida nesta revisão, não depende dele.
2. Só depois disso, a tabela de decisão desta especificação vira candidata a medição
   econômica (mesmo padrão rigoroso do NF-01: `--point-in-time`, gate de 100 eventos,
   correção para comparações múltiplas, replay causal) — nunca promovida direto.
3. Este documento não deve ser tratado como o mesmo trabalho do backtest NF-01 (itens 1-3,
   já medidos) nem do NF-01B/VAL-04 (realismo econômico) — é uma peça nova, paralela,
   focada em capturar a leitura discricionária antes de medi-la.
