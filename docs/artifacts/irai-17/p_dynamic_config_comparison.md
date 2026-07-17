# P Dinâmico WIN — Configuração versionada Miqueias × Local (IRAI-17)

**Status:** documento de comparação/diagnóstico. **Não altera** `asset_models`/`model_params`
nem qualquer caminho de cálculo do engine — o P_up de produção do WIN$N continua vindo
exclusivamente da calibração local descrita abaixo. Este artefato existe para consolidar,
em um único lugar versionado, o que já foi apurado nas tasks IRAI-17 e IRAI-21 antes da
próxima fase (captura de sessões fechadas até o gate de 60 + avaliação OOS).

Fontes: comentário `@codex` de 2026-07-16 12:29 em
`backlog/tasks/irai-17 - Medir-paridade-do-P-Dinâmico-do-Miqueias-para-WIN.md` (informação
repassada pelo Miqueias) + consulta direta a `data/irai.db` (`asset_models`/`model_params`,
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

## 3. Configuração MIQUEIAS — PARCIAL (apenas o que foi disclosed em 2026-07-16)

| Campo | Valor | Status |
|---|---|---|
| effective_from (calibração citada) | `2026-06-23` | confirmado |
| alpha | `1.918606` | confirmado |
| intercept | `-0.25` | confirmado |
| USDMXN (w) | `-0.303354` | confirmado |
| Treasury (w) | `+0.257738` | confirmado |
| WDO (w) | `-0.604859` | confirmado |
| DI (w) | `-0.315301` | confirmado |
| BRENT (w) | — | **GAP — não disclosed** |
| BTCUSD (w) | — | **GAP — não disclosed** |
| US30 (w) | — | **GAP — não disclosed** |
| CADCHF (w) | — | **GAP — não disclosed** |
| sigmas (todos os 8) | — | **GAP — nenhum disclosed** |
| campo/versão pública renderizada (`p_up` vs `p_up_v1`, v1 vs v2) | — | **GAP — não confirmado** |

**Importante:** com 4 de 8 pesos e nenhum sigma disclosed, a configuração Miqueias **não é
reprodutível integralmente** ainda — apenas parcialmente comparável nos 4 fatores conhecidos
mais alpha/intercept. Qualquer "challenger estático" construído a partir desta informação
reproduz a estrutura (mesma cesta) e o comportamento nos 4 pesos conhecidos, mas não é uma
réplica fiel da fórmula completa do Miqueias.

## 4. Diffs observados nos 4 fatores conhecidos + parâmetros globais

| Item | Local | Miqueias | Observação |
|---|---|---|---|
| alpha | 0.736566 | 1.918606 | Miqueias ~2.6× maior |
| intercept | ~0.0003 (~0) | −0.25 | com score zero, curva Miqueias parte de ~43.8% vs ~50.0% local |
| WDO (w) | −0.428164 | −0.604859 | mesmo sinal, magnitude maior no Miqueias |
| DI (w) | −0.431176 | −0.315301 | mesmo sinal, magnitude menor no Miqueias |
| Treasury (w) | −0.800422 | +0.257738 | **sinal invertido** |
| USDMXN (w) | +0.037873 | −0.303354 | **sinal invertido** |

Duas inversões de sinal (Treasury, USDMXN) em 4 fatores comparáveis é uma diferença
qualitativa, não só de escala — reforça que a divergência visual das curvas é dominada por
calibração (e possivelmente por estado/dados do Kalman no v2), não pela cesta de fatores.

## 5. O que ainda impede paridade v2 exata (item 2)

Mesmo que os 4 pesos e todos os sigmas do Miqueias fossem disclosed, os itens abaixo
continuam bloqueando paridade v2 exata — não são "mais dados de calibração", são estado e
infraestrutura do motor causal:

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

Nenhum destes 4 gaps é resolvido por mais disclosure de pesos/sigmas — são perguntas
distintas (estado do filtro, infraestrutura de dados, contrato do campo publicado).

## 6. Item 3 — revisão independente do challenger IRAI-21: JÁ CONCLUÍDA

**Nota de desambiguação:** o "challenger" desta seção **não é o mesmo objeto** do
"challenger estático" hipotético mencionado no §3/§5. IRAI-21 (Pair fixo WIN-WDO) é um
sinal de pairs-trading (par fixo vs. par dinâmico do Kalman) — não tem relação com a
fórmula de `P_up` do Miqueias nem com os pesos/sigmas comparados neste documento. O
"challenger estático" de §3 (réplica da fórmula de `P_up` do Miqueias com os pesos
disclosed) **não foi construído** — é apenas descrito como hipótese, bloqueada pelos
gaps de §3/§5. Os dois compartilham a palavra "challenger" e o mesmo par WIN$N/WDO$N como
contexto, mas são avaliações distintas, com métodos e conclusões independentes.

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
