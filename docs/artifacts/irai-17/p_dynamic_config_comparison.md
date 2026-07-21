# P Dinâmico WIN — Configuração versionada Miqueias × Local (IRAI-17)

**Status:** documento de comparação/diagnóstico. **Não altera** `asset_models`/`model_params`
nem qualquer caminho de cálculo do engine — o P_up de produção do WIN$N continua vindo
exclusivamente da calibração local descrita abaixo. Este artefato existe para consolidar,
em um único lugar versionado, o que já foi apurado nas tasks IRAI-17 e IRAI-21 antes da
próxima fase (captura de sessões fechadas até o gate de 60 + avaliação OOS).

Fontes: disclosure completo de pesos, sigmas, `alpha` e `intercept` repassado
pelo Miqueias em 2026-07-17; comentário `@codex` de 2026-07-16 12:29 em
`backlog/tasks/irai-17 - Medir-paridade-do-P-Dinâmico-do-Miqueias-para-WIN.md`;
e consulta direta a `data/irai.db` (`asset_models`/`model_params`,
`target='WIN$N'`, verificada em 2026-07-16, todas as linhas `win_%` datadas
`effective_from=2026-07-10T19:53:35Z`).

## 1. Cesta de fatores — idêntica (confirmado)

Ambas as configurações usam os mesmos 8 fatores para WIN$N, mesma ordem de disclosure:
`WDO$N, DI1$N, BRENT, BTCUSD, US30, USDMXN, CADCHF, iSharesTreasury1-3+`.

A divergência entre as curvas **não vem da cesta** — vem de calibração (pesos/alpha/intercept)
e, possivelmente, do estado/dados do Kalman (v2). Ver §4.

## 2. Configuração LOCAL (produção atual — completa)

| Campo | Valor |
|---|---|
| slug | `win` |
| display_name | Mini Índice |
| calibrated_at / effective_from | `2026-07-10T19:53:35Z` |
| alpha | `0.7365663101514398` |
| intercept | `0.000309814377547085` |
| accuracy (histórica, calibração) | 69.048% |
| R² | 0.464007 |
| n_sessions (calibração) | 252 |

| Fator | peso (w) | sigma |
|---|---|---|
| WDO$N | −0.428164 | 0.004551 |
| DI1$N | −0.431176 | 0.004843 |
| BRENT | −0.009650 | 0.019925 |
| BTCUSD | +0.028140 | 0.018425 |
| US30 | +0.111251 | 0.006491 |
| USDMXN | +0.037873 | 0.003771 |
| CADCHF | +0.110682 | 0.002741 |
| iSharesTreasury1-3+ | −0.800422 | 0.000486 |

## 3. Configuração MIQUEIAS — completa para o challenger estático

Em 2026-07-17 foram divulgados os oito pesos e sigmas. A versão canônica,
consumida pelo dashboard e pelo comparador, está em
`backend/irai/config/miqueias_static_win_2026-06-23.json`.

| Campo | Valor |
|---|---|
| effective_from | `2026-06-23` |
| alpha | `1.918606` |
| intercept | `-0.25` |
| normalização | `ret/(100*sigma*sqrt(t_frac))` (`ret` serializado em %) |

| Fator | peso (w) | sigma diário |
|---|---:|---:|
| WDO$N | −0.604859 | 0.006909 |
| DI1$N | −0.315301 | 0.008131 |
| BRENT | −0.005800 | 0.020946 |
| BTCUSD | 0.000000 | 0.014342 |
| US30 | +0.076299 | 0.006229 |
| USDMXN | −0.303354 | 0.004309 |
| CADCHF | +0.084927 | 0.002972 |
| iSharesTreasury1-3+ | +0.257738 | 0.000360 |

O challenger usa retornos causais do IRAI e aplica a normalização temporal
`sqrt(t)` já usada pelo motor local. É uma curva diagnóstica, não troca o
`P_up` ativo e não afirma reproduzir o Kalman do Miqueias.

## 4. Diffs observados na calibração estática

| Item | Local | Miqueias | Observação |
|---|---:|---:|---|
| alpha | 0.736566 | 1.918606 | Miqueias ~2.6× maior |
| intercept | ~0.0003 | −0.25 | score zero: ~50.0% local vs ~43.8% Miqueias |
| WDO (w) | −0.428164 | −0.604859 | mesmo sinal, magnitude maior |
| DI (w) | −0.431176 | −0.315301 | mesmo sinal, magnitude menor |
| Treasury (w) | −0.800422 | +0.257738 | **sinal invertido** |
| USDMXN (w) | +0.037873 | −0.303354 | **sinal invertido** |

As inversões de Treasury e USDMXN, além dos demais pesos/sigmas diferentes,
explicam uma parcela material da divergência visual sem atribuí-la
indevidamente ao Kalman.

## 5. O que ainda impede paridade v2 exata (item 2)

Mesmo com a calibração estática completa, os itens abaixo continuam bloqueando
paridade v2 exata — são estado e infraestrutura do motor causal:

1. **Q/R do Kalman** (matrizes de ruído de processo/observação) — não disclosed para o
   modelo do Miqueias. Sem elas, a trajetória dinâmica de pesos não é reproduzível mesmo
   com a mesma cesta.
2. **Estado (state mean / covariance) no corte** — o v2 é causal e path-dependent
   (`backend/irai/kalman.py`, `filter_update`, sem lookahead); sem o estado exato no
   instante de corte, qualquer réplica parte de um ponto inicial diferente e diverge.
3. **Fontes e relógios das barras** — não confirmado se o feed do Miqueias usa o mesmo
   broker/terminal (Tickmill vs outro), a mesma base de horário (ver
   `.planning/docs/TIMEZONE_ARCHITECTURE.md`) e a mesma barra M5 que `market_bars` local.
4. **Campo/versão pública renderizada** — não confirmado se o `p_up` exposto por
   `https://rastromacro.web.app/` / `/series/WIN_N.json` é v1, v2, ou uma variante própria
   do Miqueias. A lógica de fallback do comparador (`p_up_v1` → `p_up`) é uma convenção do
   *parser* do IRAI-17 para ler qualquer série pública genérica — **não** é confirmação do
   que o deploy do Miqueias efetivamente calcula e publica.

Nenhum destes quatro gaps é resolvido por pesos/sigmas: são perguntas distintas
sobre estado do filtro, infraestrutura de dados e contrato público.

## 6. Item 3 — revisão independente do challenger IRAI-21: JÁ CONCLUÍDA

**Nota de desambiguação:** o "challenger" desta seção **não é o mesmo objeto** do
"challenger estático" de P Dinâmico mencionado no §3/§5. IRAI-21 (Pair fixo WIN-WDO) é um
sinal de pairs-trading (par fixo vs. par dinâmico do Kalman) — não tem relação com a
fórmula de `P_up` do Miqueias nem com os pesos/sigmas comparados neste documento. O
"challenger estático" de §3 **foi construído** para visualização, usando a
configuração completa e retornos locais. Os dois compartilham a palavra
"challenger" e o mesmo par WIN$N/WDO$N como contexto, mas são avaliações
distintas, com métodos e conclusões independentes.

Antes de dispatchar uma nova revisão, verifiquei o histórico da task IRAI-21
(`status: Done`, comentários e Implementation Notes). O challenger Pair fixo WIN-WDO já
passou por **duas rodadas independentes de revisão**, não uma:

1. **`/fable-reasoner`** (comentário 2026-07-16 15:39): GO nos itens A, B, C, D, F, G;
   **NO-GO parcial no item E** (ranking comparava janelas diferentes — challenger mede toda
   a base ~1250 sessões, dinâmico é PIT ~880 sessões). Correção aplicada e re-rodada:
   `pair_fixo_windowed`, recortando o challenger na mesma janela do PIT. O ranking se
   confirmou apples-to-apples (WIN$N: −11.02 windowed vs −7.47 dinâmico — a diferença de
   janela **não** explicava o gap). Suíte subiu para 288 passed após os testes adicionais
   (anti-lookahead por prefixo, isolamento entre sessões, offset de inverno, data_quality).
2. **`codex`** (comentário 2026-07-16 15:43), revisão **independente da revisão do fable**:
   GO. Conferiu especificamente o recorte `pair_fixo_windowed`, o re-bootstrap por sessão, a
   causalidade por prefixo, o isolamento entre sessões e o diagnóstico de alinhamento;
   rodou `pytest tests/test_measure_pair_fixed_value.py tests/test_build_challenger_artifact.py`
   → 19 passed localmente. Conclusão econômica preservada: Pair fixo não recupera edge.

Resultado final (h=6, líquido de custo, `pair_fixo_windowed` = comparação válida
apples-to-apples):

| Sinal | WIN$N méd/ev | WDO$N méd/ev |
|---|---|---|
| pair_fixo_windowed | −11.02 *** | −0.72 *** |
| pair (dinâmico, PIT) | −7.47 | −1.00 *** |

Todos os sinais (challenger, dinâmico, baselines momentum/reversão) são negativos em ambos
os alvos — fixar o par WIN-WDO não recupera edge algum; a regra simples não vence a
complexa porque as duas perdem.

**Decisão:** não dispatchei uma terceira revisão (ex.: `deep-reasoner`). Duas revisões
independentes e sequenciais já ocorreram, com uma correção real aplicada e verificada na
primeira (item E, ranking por janela) e confirmação por uma segunda fonte independente na
segunda. Redespachar uma terceira passada sobre a mesma conclusão, sem um motivo novo
(dado novo, mudança de metodologia, ou dúvida específica não coberta pelas duas revisões
anteriores), seria trabalho redundante. Se quiser uma passada adicional mesmo assim — por
exemplo, por `deep-reasoner` como uma terceira lente, já que `fable-reasoner`/`codex` via
agentrelay estavam indisponíveis nesta sessão para outras tarefas — é só pedir.

## 7. Próxima fase (não iniciada, apenas registrada para contexto)

Após consolidação deste artefato: captura de sessões fechadas até o gate de 60 sessões e
avaliação OOS (Brier/log-loss/AUC/calibração por horário), conforme o plano de implementação
da IRAI-17 (`Implementation Plan`, passos 3-5). **Nenhuma promoção de versão nem troca do
P_up de produção ocorre neste artefato ou nesta fase.**

**Atualização 2026-07-18:** a "próxima fase" acima já está implementada e em produção —
não é trabalho pendente, é a task IRAI-18 (`scripts/evaluate_p_dynamic_champions.py`,
timer diário `rastro-irado-p-dynamic-ledger.timer`, Mon-Fri 17:56 BRT, capturando desde
2026-07-16). Ver §8.

## 8. Estado do ledger champion-challenger (IRAI-18) em 2026-07-21

**Estado implantado em 2026-07-20: `methodology_version: 3`, ledger em 1/60.**

Após a captura regular de 2026-07-20, `data/p_dynamic_parity/` contém uma sessão
elegível da revisão runtime `f0b63d4`. `sessões=1/60`, `status: INCONCLUSIVE`,
`quality_winner: null`. Métricas de uma única sessão não têm AUC definida e não são
interpretadas como evidência de qualidade; a captura diária segue acumulando sob a regra
nova.

**Atualização pendente de implantação em 2026-07-21: `methodology_version: 4`.** A
identidade runtime da v3 só continha o commit Git e hashes de `engine.py`/`kalman.py`.
Ela não distinguia uma recalibração ativa do WIN em `asset_models`/`model_params`, mas
tratava um commit documental como motor diferente e zerava o ledger. A v4 registra um
hash do código que alimenta `P_up` e outro da configuração ativa **do WIN**; o commit
continua no manifesto para auditoria, mas não integra o agrupamento semântico. O único
bundle v3 será mantido e contado como `superseded` após a implantação, não misturado com
v4; por isso a nova contagem começará em `0/60` de forma deliberada.

As duas sessões que existiam (2026-07-16 e 2026-07-17) foram movidas para
`data/p_dynamic_parity_pre_2026-07-19_rule_change/` — **superseded**, não apagadas. Elas
foram apuradas sob `methodology_version: 1`, cujas regras a revisão por painel mostrou
enviesadas:

- **Elegibilidade**: `closed` olhava só o último ponto operacional, sem cobertura nem
  abertura. Uma fonte com **uma única barra tardia** era gravada como fechada e vencia o
  torneio com Brier ≈ 0.0004 contra ≈ 0.20 dos locais — um palpite único com a sessão já
  decidida é quase-oráculo, e o viés era auto-favorável: quanto mais o feed degradasse,
  melhor o score dele.
- **Métrica**: Brier/log-loss eram média sobre as barras que **cada modelo por acaso
  tinha**, sem alinhamento por timestamp. Barras da manhã valem Brier ≈ 0,25 (P≈0,5) e as
  do fim valem quase zero, então perder manhã dava score de graça.
- **Desfecho**: `actual_up` saía de base diferente da métrica. O collector coleta até
  18:10 BRT com margem, então **844 sessões do banco de produção** têm barra WIN depois
  das 18:00; essas barras têm a mesma data BRT, entram no bundle, não são pontuadas — e
  fixavam o rótulo de verdade. Medido em `data/irai.db`: **30 de 835 sessões (3,6%)**
  teriam o desfecho invertido, concentradas nos dias de menor `|close−open|`, que são
  justamente onde os modelos discordam.

Regras de elegibilidade preservadas na `methodology_version: 4`, gravadas em cada
manifesto e revalidadas na leitura:

| Regra | Valor |
|---|---|
| Janela de pregão (eixo BRT) | 09:00–18:00, exclusivo no fim |
| Abertura | primeira barra operacional ≤ 09:10 |
| Cobertura por fonte | ≥ 98 dos 108 **slots M5 canônicos** |
| **Cobertura da interseção** | ≥ 98 slots — **condição vinculante** |
| Fechamento | ≥ 17:50 BRT (v1/v2); ≥ 17:45 para a referência pública |
| Trio obrigatório | `miqueias`, `v1`, `v2` — challenger não altera elegibilidade nem placar |
| Cru auditável | os três payloads arquivados, validados por sha256 e tamanho |
| Revisão do motor | hashes de código runtime + configuração ativa que altera `P_up` do WIN; commit Git é auditável, mas não fragmenta sessões semanticamente idênticas |
| Métrica | Brier/log-loss na **interseção** de timestamps operacionais em sessão |
| Desfecho | última barra comum a **v1 e v2**, dentro da janela, preços consensuais |

Cobertura é medida em **slot**, não em contagem de linhas. Contagem bruta era burlável:
98 barras publicadas de minuto em minuto no fim do pregão satisfaziam qualquer piso e ainda
deixavam ~7h sem cobertura (gap medido de 438 min). Exigindo 98 dos 108 slots da grade M5,
o buraco máximo fica limitado a 10 slots (55 min) **por construção** —
`canonical_session_slots` é a fonte única dessa regra, usada no status por fonte, na
interseção e na revalidação do leitor.

Elegibilidade por fonte não basta: três fontes com 98 slots cada, mas com lacunas
**disjuntas**, são todas elegíveis e ainda assim deixam uma interseção de 78 — uma sessão
de baixa informação que pesaria igual a uma íntegra no gate de 60. Por isso a interseção
tem piso próprio e é a condição vinculante. O manifesto registra
`intersection.{rows, canonical_slots_covered, min_rows, sufficient, max_gap_minutes,
first/last_scored_brt, first/last_scored_timestamp}`.

O leitor **não confia no manifesto**: rederiva janela, cobertura, interseção e desfecho a
partir das séries, e valida a integridade do cru por sha256/tamanho. Um manifesto forjado
com `closed=true`, ou um payload cru adulterado, é rejeitado com motivo registrado em
`audit.invalid_reasons`.

O desfecho usa base **deliberadamente distinta** da métrica: sai da última barra comum às
fontes **locais**, porque o rótulo é propriedade do mercado, não dos modelos. Ancorá-lo na
última barra pontuada tornaria o alvo endógeno à disponibilidade do feed de terceiro e
vazaria o preço quase-determinante para dentro do próprio rótulo. O que as duas bases
partilham é a janela de pregão. A distância entre rótulo e última barra pontuada fica
auditável em `audit.outcome_timestamps` contra `intersection.last_scored_timestamp` (nos
bundles reais, 17:55 versus 17:50 — uma previsão legítima de 5 minutos à frente).

O `objective.primary` do manifesto declara o proxy operacional real: *"último print
operacional em sessão (≤ 17:55 BRT) fechar acima da abertura"*. A captura roda 17:56 e por
construção **não observa o leilão de fechamento** — dizer "fechamento da sessão" prometeria
mais do que se mede.

O piso é absoluto e não relativo à fonte mais completa: o relativo tem denominador
endógeno (degradação correlacionada rebaixa o piso junto) e entregaria a régua a um
terceiro. Não custa nada em pregão encurtado, porque esse dia já morre no limiar de
fechamento, que também é absoluto.

O avaliador recusa bundle cuja `methodology_version` **difira** da corrente — anterior
(`superseded_bundles`) ou posterior (`foreign_version_bundles`, caso de rollback só do
avaliador). O corte de época portanto **não depende de mover diretórios**: um restore de
backup ou um `--ledger-dir` um nível acima não reinjeta sessões apuradas por régua
diferente. Verificado: apontar para `data/` alcança os 5 manifestos arquivados e ingere
`sessões=0, superseded=5`. O `champion_report.json` também carrega a
`methodology_version`, para que um relatório solto seja auto-datável quanto à época.

Além da metodologia, cada captura registra `engine_revision` que a **API congela no
startup** e expõe internamente: o commit Git para auditoria, hashes individuais de
`engine.py`/`kalman.py`, um hash composto dos módulos que alimentam `P_up` e um hash da
configuração ativa que altera `P_up` do WIN (`asset_models` do alvo e os parâmetros
`w_*`, `sigma_*`, `alpha` e `intercept` do seu slug). O capturador lê a identidade
semântica antes e depois de receber v1/v2; se o cálculo ou a calibração mudar no
intervalo, a captura falha sem criar uma sessão elegível. Um restart após commit
documental com a mesma semântica não falha a captura nem fragmenta o ledger. O leitor
recusa versões sem todos esses campos e, se sessões fechadas contiverem mais de uma
identidade semântica, retorna **zero sessões selecionadas** e registra
`mixed_engine_revision_bundles`, em vez de escolher silenciosamente a revisão majoritária.

Custo real da troca de métrica, medido nos bundles preservados: Brier v1
0.22319373 → 0.22334139, **ranking inalterado**. O número maior (+0,066) que aparece na
justificativa do código refere-se a **degradação simulada**, não ao delta antigo→novo
destes dois dias — a distinção está registrada no docstring de `_aligned_forecasts`.

Vetores verificados contra o código desta versão, todos rejeitados com motivo explícito:
série de uma barra tardia; duas barras com âncora de madrugada; 98 timestamps fora da
grade M5 (gap de 438 min); cobertura só à tarde; lacunas disjuntas entre fontes
individualmente elegíveis; bundle sem o trio obrigatório; manifesto forjado `closed=true`;
cru ausente; cru adulterado (detectado por hash). Sessão íntegra segue ingerindo
normalmente. Suíte do repositório após a atualização da v4: **477 passed, 1 skipped**.

Corte de calibração confirmado em `data/irai.db`/`model_params` (`effective_from`): WIN
`2026-07-10T19:53:35Z`, WDO `2026-07-10T05:47:55Z` — datas diferentes, não uma calibração
simultânea; o corte relevante para qualquer avaliação OOS que cubra os dois ativos é o mais
tardio dos dois. `calibration_log` (tabela legada) está vazio — não é usado por este
pipeline. Mesmo reprocessando todo o histórico causal disponível em `market_bars` desde o
corte, só existem 5 sessões fechadas até 2026-07-17 (13–17/07) — abaixo do próprio gate de
60. Fechar o gate é um problema de tempo de calendário (~58 sessões úteis restantes ao
ritmo de 1/dia útil, ~11-12 semanas a partir de 2026-07-18), não de infraestrutura: não há
atalho honesto sem violar o corte OOS ou recalibrar em produção (proibido por este mesmo
artefato, ver cabeçalho).

**Correções aplicadas nesta data:** `scripts/compare_p_dynamic_parity.py` tinha três
lacunas na captura diária usada pelo timer:

1. `load_json_document` fazia uma única tentativa de `urlopen`/parse JSON, com um retry
   genérico anterior (`OSError`, `json.JSONDecodeError`) — padrão semelhante ao de
   `backend/gex_official.py` (commit `376dff1`; essa área do código passou por revisão com
   P1 ainda pendentes, então não é citada aqui como precedente já validado, só como o
   mesmo padrão de risco). Esse retry genérico tinha dois defeitos próprios: retentava
   indevidamente `HTTPError` 4xx (`HTTPError` é subclasse de `OSError`, mas um erro de
   contrato do cliente não se resolve com retry) e não cobria `IncompleteRead` (conexão
   truncada, `http.client.HTTPException`, não `OSError`) nem `UnicodeDecodeError`
   (payload corrompido, `ValueError`, não `OSError`) — exatamente os cenários transitórios
   visados. Reescrito para: `HTTPError` 4xx falha imediatamente; `HTTPError` 5xx e demais
   falhas de transporte/formato (`OSError`, `IncompleteRead`, `UnicodeDecodeError`,
   `json.JSONDecodeError`) retentam 3x/5s.
2. A sessão capturada era derivada de `reference[0].timestamp` — a barra mais ANTIGA da
   série pública ordenada, não necessariamente "hoje" (a série pública é histórico
   multi-dia, não paginada por sessão). `--session-date` passou a ser obrigatório
   (fornecido pelo timer via `date +%Y-%m-%d` em horário BRT) e toda referência
   pública/série local que, após filtrar por essa sessão, fique sem nenhuma barra é
   rejeitada — mesmo depois de esgotar o retry de transporte — em vez de `main()` adotar
   silenciosamente uma sessão errada.
3. `v1`/`v2` indisponíveis não abortavam a captura: com `v2` OK e `v1` ausente, `main()`
   podia retornar 0 e gravar `closed: true` mesmo faltando uma fonte inteira do bundle.
   Miqueias, v1 e v2 passaram a ser todos obrigatórios (quando a API local não é pulada);
   a falta de qualquer um retorna código não-zero sem gravar bundle parcial.

**Segunda rodada de correções (mesma data), após revisão por painel.** A rodada acima
foi revisada por duas lentes independentes e cegas (`deep-reasoner`, `fable-reasoner`;
`codex`/agentrelay indisponível — painel de dois, não de três) e **não sobreviveu
intacta**. Quatro defeitos adicionais, dois deles em consenso das duas lentes:

4. **O filtro de sessão estava no eixo errado.** `session` é um dia BRT, não um dia do
   rótulo do timestamp. Apesar do sufixo `+00:00`, os timestamps da API e do Firebase são
   hora de parede do servidor Tickmill (ver "Timezones" no CLAUDE.md), e a sessão B3 vai
   do rótulo 15:00 até 00:00 do rótulo seguinte. Filtrar por data do rótulo aceitava a
   cauda da sessão anterior — que, se fosse operacional, sozinha satisfaria o limiar das
   17:50 e produziria manifesto `closed=true` feito só de barras estrangeiras. Passou a
   filtrar **linhas cruas** por data de sessão BRT (`rótulo − brt_offset_h`).
5. **O filtro só existia em memória.** O bundle persistia o documento íntegro e o
   avaliador o relia sem filtrar, então a proteção não alcançava Brier/log-loss. Agora
   persiste-se o envelope com apenas as linhas da sessão, e o avaliador aplica o mesmo
   filtro como defesa em profundidade, isolando por modelo (um challenger esporádico sem
   barras da sessão não derruba mais a sessão inteira do gate).
6. **`exit 3` para sessão não fechada tinha raio de explosão maior que o alvo.** Sob
   `ExecStartPost`, qualquer saída não-zero pulava o avaliador — que reagrega o ledger
   acumulado e nada tem a ver com a captura do dia. Corrigido movendo o avaliador para
   `ExecStopPost` e separando "sessão parcial" (anomalia → `exit 3`) de "nenhuma barra
   operacional" (feriado B3 ou catch-up de `Persistent=true` → `exit 0`), para não
   transformar todo feriado em falha do systemd.
7. Menores: `--session-date` validado no argparse; `static_challengers` limpo quando o
   challenger falha o filtro.

**Registro histórico (2026-07-18):** as regressões permanentes então criadas em
`tests/test_compare_p_dynamic_parity.py` e
`tests/test_p_dynamic_champion_evaluator.py` levaram a suíte completa daquele
checkout a `410 passed, 1 skipped`. Não é a contagem atual: a validação mais
recente está registrada acima como `477 passed, 1 skipped`.

*Correção de registro:* uma versão anterior desta seção afirmava que
`tests/test_measure_tactical_gate3.py` exige dependências ausentes neste ambiente e
precisaria de `--ignore`. Isso é falso — o módulo roda e passa (19 testes); a exclusão era
desnecessária e a contagem "384 passed" era de suíte parcial.

**Riscos conhecidos, não corrigidos aqui (registrados para decisão):**
- O limiar de fechamento (17:50 BRT) tem **margem zero**: nos dois bundles reais a
  referência pública fecha exatamente em 17:50. Um único atraso de 5 min do publicador
  externo torna a sessão inelegível, sem reexecução possível para o mesmo dia. Mudar o
  limiar (ou aferir o fechamento só por v1/v2, que definem o outcome) é regra de negócio
  — não alterado unilateralmente.
- `evaluate_p_dynamic_champions.py` deriva `common_models` por **união** e depois exige
  todos: um único bundle com challenger extra colapsaria as sessões comparáveis,
  reiniciando o gate de 60 em silêncio. É **pré-existente** e hoje inativo (todos os
  bundles têm os mesmos 3 modelos).
- O ledger real tem **2 sessões fechadas** (2026-07-16 e 17), não 5. As "5 sessões" citadas
  acima são sessões reprocessáveis de `market_bars` — base distinta de bundles capturados.

**Pendente de confirmação do usuário (fora deste ambiente Linux de edição):** se
`rastro-irado-p-dynamic-ledger.timer` está de fato habilitado e ativo no Ryzen5WSL de
produção — sem isso, o gate nunca fecha mesmo com o tempo passando.

## 9. Walk-forward histórico local (2026-07-20)

O código do upstream do Miqueias e a configuração divulgada em
`backend/irai/config/miqueias_static_win_2026-06-23.json` permitem reconstruir uma
**curva estática diagnóstica**, mas não a versão dinâmica dele: o upstream carrega
`model_params`/`asset_models` de um banco que não versiona a história dos parâmetros,
nem os estados/covariâncias Kalman ou os Q/R usados em cada sessão. Portanto, a série
histórica foi separada em duas perguntas:

- `v1_pit` versus `v2_pit`: replay walk-forward local, com calibração limitada a cada
  cutoff e estado v2 encadeado cronologicamente;
- `miqueias_static_disclosed`: somente a configuração estática divulgada, após
  `effective_from: 2026-06-23`; diagnóstico sem poder de promoção e sem alegação de
  paridade com o deploy v2 do upstream.

O script novo é `scripts/backtest_p_dynamic_walkforward.py`. Ele exige
`--snapshot-db`, recusa WAL/journal pendente e calcula SHA-256 antes e depois do replay;
isso evita misturar leituras do collector vivo entre Windows e WSL. A execução abaixo
usou uma cópia fechada de `data/backups/irai_pre_gex_reclass_20260716_142148.db`, movida
para filesystem Linux local, com SHA-256
`2635c029791e5b8e637d769f98bd219c1b7f4eac1ed416470ed64308c066e230`.

Contrato temporal: a previsão de `10:00 BRT` usa a última M5 **já fechada** (início
`09:55`); previsão e desfecho aceitam apenas 09:00–18:00 BRT no eixo Tickmill. Isso
impede tanto a leitura da barra 10:00 ainda em formação como barras pós-pregão do
rótulo seguinte. O estado v2 persistido/herdado usa o posterior da última barra real
dessa mesma janela: não inclui pós-pregão que, no verão, cruza para o rótulo seguinte
e, no inverno, permanece no mesmo rótulo Tickmill. Regressões permanentes cobrem os
dois offsets sazonais.

Resultado descritivo, amostra OOS de 230 sessões (2025-07-01 a 2026-07-15). **Esta tabela
foi rebaselinada em 2026-07-21 — ver seção 11**: reexecutada sob o harness corrigido
(candidatos 1/2/3/5 da revisão `/tri-r`), idêntica ao valor abaixo. A seção 10 (AUC/log-
loss pareados, ECE) **não** foi recoberta por este rebaseline — ver ressalva na seção 11.

| Braço | Sessões | Brier | Log-loss | AUC | Acerto direcional |
|---|---:|---:|---:|---:|---:|
| v1 PIT | 230 | 0,24329206 | 0,67930164 | 0,58893775 | 57,391304% |
| v2 PIT | 230 | 0,24006482 | 0,67306793 | 0,61877322 | 57,391304% |
| Miqueias estático divulgado | 16 | 0,25350311 | 0,70564141 | 0,63492063 | 56,25% |

O delta Brier pareado `v2 - v1` foi `-0,00322724`, IC95% bootstrap por sessão
`[-0,00726876, 0,00084694]`. O ponto favorece v2, mas o intervalo ainda inclui zero:
**não há promoção, troca de P_up de produção ou conclusão sobre Miqueias**. O braço
estático tem apenas 16 sessões e o seu IC95% contra v2 também inclui zero. A comparação
prospectiva de três braços do ledger IRAI-18 continua necessária para confrontar a curva
publicada do Miqueias sob regras idênticas.

## 10. Diagnóstico AUC, Brier e acurácia (2026-07-20)

A acurácia idêntica não é coincidência nem indica curvas iguais. No mesmo artefato de
230 observações, v1 e v2 discordam em 28 decisões ao limiar de 50%: v1 acerta sozinha
14 e v2 acerta sozinha 14. Por isso ambas terminam com 132/230 acertos (57,391304%),
mesmo com probabilidades diferentes (correlação 0,935969; diferença absoluta média de
0,024438).

O AUC mede a ordenação entre sessões positivas e negativas, enquanto a acurácia usa
apenas o lado de um único limiar. A v2 melhora o AUC pontual em `+0,02983547`, mas o
bootstrap pareado de 20.000 reamostragens por sessão (seed `20260720`) deu IC95%
`[-0,00321763, +0,06394788]`; portanto, também não sustenta promoção. Os deltas
pareados v2-v1 para Brier e log-loss são, respectivamente, `-0,00322375` (IC95%
`[-0,00725652, +0,00081670]`) e `-0,00622642` (IC95%
`[-0,01457327, +0,00213587]`). Os três pontos favorecem v2, mas os três intervalos
ainda contêm zero.

A calibração por faixas fixas é somente descritiva nesta amostra: o ECE de seis faixas
foi 0,043506 para v1 e 0,060663 para v2, com várias faixas de 24 a 58 sessões. Isto não
prova que v2 esteja pior calibrada e não justifica recalibração. Qualquer ajuste de
limiar ou calibração escolhido nesta mesma amostra seria in-sample e introduziria
lookahead. A decisão permanece: manter v1/v2 em paralelo, sem promoção, e acumular
evidência prospectiva no ledger IRAI-18.

## 11. Rebaseline pós-correção PIT (2026-07-21)

**Escopo desta seção:** cobre exclusivamente o que `scripts/backtest_p_dynamic_walkforward.py`
calcula — a tabela de métricas por braço e o delta Brier pareado (seção 9). Ele **não**
calcula IC pareado de AUC, delta pareado de log-loss nem ECE — essas vêm de uma
computação separada (seção 10) que este rebaseline não reexecutou. Ver ressalva ao final.

A revisão `/tri-r` do branch `fix/irai-18-methodology-v2` confirmou e corrigiu quatro
bugs de metodologia (candidatos 1, 2, 3 e 5 de
`docs/artifacts/irai-18/review-candidates-2026-07-21-UNVERIFIED.md`), entre eles um
possível lookahead na elegibilidade de barra do próprio walk-forward (candidato 5:
`_is_b3_session_timestamp`/`build_observation` aceitavam qualquer timestamp `< horário de
decisão`, sem exigir que a barra pertencesse à grade M5 canônica e já tivesse fechado —
um print fora da grade que abre antes mas fecha depois do instante de decisão podia
"vazar" para dentro do score). Este rebaseline reexecuta o walk-forward de 230 sessões
(seção 9) sob o harness corrigido, contra a mesma cópia fechada do banco, para confirmar
se a tabela da seção 9 continua válida ou precisa ser substituída.

**Reprodução:**

```
python3 scripts/backtest_p_dynamic_walkforward.py \
  --snapshot-db data/backups/irai_pre_gex_reclass_20260716_142148.db \
  --output-json <saída>
```

- Snapshot: `data/backups/irai_pre_gex_reclass_20260716_142148.db`, SHA-256
  `2635c029791e5b8e637d769f98bd219c1b7f4eac1ed416470ed64308c066e230` (idêntico ao da
  seção 9 — mesma cópia fechada, não a base viva do collector).
- Código: working tree do branch `fix/irai-18-methodology-v2` sobre o commit
  `3e495cd` (HEAD), com os candidatos 1/2/3/5 já aplicados e ainda não commitados no
  momento deste rebaseline; `pytest -q tests` → 481 passed, 1 skipped.
- Resultado do rerun: idêntico à tabela da seção 9 — `v1_pit` Brier `0,24329206`,
  `v2_pit` Brier `0,24006482`, delta pareado `v2-v1` `-0,00322724`, IC95%
  `[-0,00726876; 0,00084694]`, 230 sessões, mesmo conjunto de 214 sessões descartadas.
  Artefato salvo em
  `docs/artifacts/irai-17/rebaseline-2026-07-21/walkforward_postfix.json`, SHA-256
  `21f5c572244a2a6f5bd59db1f9a319576fb3efe82f9a5bd63bc3dc14935cf653`.

**Verificação do candidato 5 (a correção do lookahead muda algum número real?).**
Resultado idêntico não prova por si só que a correção teve efeito nulo — poderia ser
coincidência, ou o relatório original já podia ter sido gerado com o código corrigido.
Para isolar a pergunta, o walk-forward foi reexecutado uma segunda vez com uma cópia
standalone de `scripts/backtest_p_dynamic_walkforward.py` no estado **anterior** à
correção (`git show HEAD:scripts/backtest_p_dynamic_walkforward.py`, ou seja, sem a
checagem de grade M5 `brt.minute % 5 == 0 and brt.second == 0 and brt.microsecond == 0`),
contra a mesmíssima cópia fechada do banco, mantendo os demais módulos importados
(`scripts/measure_pair_signal_value.py`, `scripts/pit_calibration.py`, `backend/irai/*`)
no estado atual — isso isola o efeito marginal só do candidato 5, sem contaminação de
import/bytecode. Resultado: o JSON de saída da versão pré-fix e o da versão corrigida são
**byte-idênticos entre si** (mesmo SHA-256,
`21f5c572244a2a6f5bd59db1f9a319576fb3efe82f9a5bd63bc3dc14935cf653` — arquivos salvos em
`docs/artifacts/irai-17/rebaseline-2026-07-21/walkforward_postfix.json` e
`walkforward_prefix_candidate5.json`). Conclusão: o candidato 5 é uma correção real e
necessária (o cenário de exploração usava uma fixture construída com timestamp fora de
grade, `09:57`, para demonstrar o defeito). Quanto a esta base real: **verificado
diretamente** (não apenas assumido) via consulta ao snapshot fechado —
`SELECT timestamp_utc FROM market_bars WHERE symbol=? AND timeframe='M5' AND source='br'`
sobre as 138.711 barras M5 de `WIN$N` e as 139.958 de `WDO$N` em todo o histórico do
snapshot — **zero timestamps fora da grade de 5 minutos** em qualquer uma das duas séries
(nenhum minuto não-múltiplo-de-5, nenhum segundo/microssegundo não-zero). Isso explica
mecanicamente por que os dois reruns deram byte-idênticos: não há, nesta base, nenhum
print que a checagem mais estrita pudesse excluir. A correção é necessária mesmo assim
como blindagem estrutural (garante a invariante sob qualquer fonte de dados futura, e sob
qualquer `--decision-time` que não caia num múltiplo de 5 minutos — o CLI não valida isso
hoje), mas **não altera nenhum número já publicado na seção 9**.

**Veredito do rebaseline:** a tabela da seção 9 permanece válida. A comparação pré/pós-fix
do candidato 5 é reprodutível (hash de entrada idêntico, comando documentado acima, hash
de saída idêntico) — isso não é o mesmo que uma cadeia de proveniência automatizada
completa: os JSONs de artefato registram o hash do snapshot e os resultados, mas não
embutem o hash do commit/script exato nem a saída bruta do `pytest` — esses ficam
registrados apenas em texto nesta seção (commit `3e495cd` + candidatos 1/2/3/5 aplicados
no working tree; `pytest -q tests` → 481 passed, 1 skipped, conforme relatado acima), não
como artefato verificável à parte. **A seção 10 não foi revalidada por este rebaseline** —
seus números (IC
pareado de AUC, delta pareado de log-loss, ECE) vêm de uma computação distinta que
`backtest_p_dynamic_walkforward.py` não reproduz. Nota à parte: o delta Brier pareado
implícito na tabela da seção 9 (`0,24006482 − 0,24329206 = -0,00322724`) e o delta Brier
pareado declarado na seção 10 (`-0,00322375`) **diferem na 6ª casa decimal** — inconsistência
pré-existente entre as duas seções (não introduzida por este rebaseline, não afeta
nenhuma conclusão, já que ambos os IC95% incluem zero), mas que fica registrada aqui para
não ser silenciada; investigar a fonte da seção 10 é trabalho futuro, fora do escopo
deste rebaseline. O relatório de candidatos
(`docs/artifacts/irai-18/review-candidates-2026-07-21-UNVERIFIED.md`) pode ser atualizado
para refletir esta confirmação empírica do candidato 5.

**Revisão adversarial (`/codex-r`, 2026-07-21):** apontou corretamente que uma versão
anterior desta seção alegava validar a seção 10 sem tê-la recomputado (corrigido acima) e
que os JSONs de saída não estavam em local durável (corrigido acima). Também alegou que
`warmup_sessions` teria mudado de 133 para 0 e os descartes de 81 para 214 entre as
execuções pré/pós-fix — **verificado e refutado**: os dois JSONs de saída são idênticos
campo a campo (`warmup_sessions=0` e 214 descartes em ambos, confirmado por comparação de
dicionário completa em Python, não só pelos campos de métricas).
