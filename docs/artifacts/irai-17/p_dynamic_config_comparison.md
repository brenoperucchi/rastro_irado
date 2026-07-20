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

## 8. Estado do ledger champion-challenger (IRAI-18) em 2026-07-19

**`methodology_version: 2` — ledger reiniciado em 0/60.**

Estado real de `data/p_dynamic_parity/`: **vazio**. `sessões=0/60`,
`status: INCONCLUSIVE`, `quality_winner: null`. A contagem recomeça do zero sob a
regra nova; a captura diária volta a acumular a partir da próxima sessão útil.

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

Regras da `methodology_version: 2`, gravadas em cada manifesto e revalidadas na leitura:

| Regra | Valor |
|---|---|
| Janela de pregão (eixo BRT) | 09:00–18:00, exclusivo no fim |
| Abertura | primeira barra operacional ≤ 09:10 |
| Cobertura por fonte | ≥ 98 dos 108 **slots M5 canônicos** |
| **Cobertura da interseção** | ≥ 98 slots — **condição vinculante** |
| Fechamento | ≥ 17:50 BRT (v1/v2); ≥ 17:45 para a referência pública |
| Trio obrigatório | `miqueias`, `v1`, `v2` — challenger não altera elegibilidade nem placar |
| Cru auditável | os três payloads arquivados, validados por sha256 e tamanho |
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

Custo real da troca de métrica, medido nos bundles preservados: Brier v1
0.22319373 → 0.22334139, **ranking inalterado**. O número maior (+0,066) que aparece na
justificativa do código refere-se a **degradação simulada**, não ao delta antigo→novo
destes dois dias — a distinção está registrada no docstring de `_aligned_forecasts`.

Vetores verificados contra o código desta versão, todos rejeitados com motivo explícito:
série de uma barra tardia; duas barras com âncora de madrugada; 98 timestamps fora da
grade M5 (gap de 438 min); cobertura só à tarde; lacunas disjuntas entre fontes
individualmente elegíveis; bundle sem o trio obrigatório; manifesto forjado `closed=true`;
cru ausente; cru adulterado (detectado por hash). Sessão íntegra segue ingerindo
normalmente. Suíte do repositório: **435 passed, 1 skipped**.

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

Regressão permanente em `tests/test_compare_p_dynamic_parity.py` e
`tests/test_p_dynamic_champion_evaluator.py`. Suíte **completa** do repositório:
`pytest -q tests` → **410 passed, 1 skipped**.

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
