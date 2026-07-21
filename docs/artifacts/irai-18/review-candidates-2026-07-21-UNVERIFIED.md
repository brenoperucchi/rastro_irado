# Revisão xhigh do branch `fix/irai-18` — candidatos NÃO-VERIFICADOS

**Status: NÃO APROVADO. Verificação abortada.** Registrado em 2026-07-21.

> ⚠️ **O resultado "0 findings" foi enganoso.** A revisão **não** voltou limpa: a fase de
> verificação foi inteira abortada pelo limite de sessão. Os 6 finders rodaram e
> produziram 31 candidatos, mas os **19 verificadores + o sweep morreram todos** antes de
> confirmar ou refutar qualquer um. O que segue são **candidatos recuperados do journal**,
> nenhum passou pela verificação adversarial — que é justamente a fase onde a revisão
> anterior refutou/confirmou coisas.

**Selo de todos os itens abaixo:** `SOLO`/`CONSENSO-DE-FINDERS` — **não** `CONFIRMADO`.
Na ausência de verificação formal, o único sinal disponível é **concordância entre finders
independentes**. Ranqueado por isso; não vira ação sem verificação.

## Retomada (barata)

- `resumeFromRunId: wf_3336f44b-a56`
- Limite reseta **20:20 America/São_Paulo**.
- Os 6 finders voltam do cache instantaneamente; só os verificadores rodam de novo.

---

## Candidatos de correção (ordenados por confiança)

### 1. `scripts/measure_pair_signal_value.py:247` — chaining do Kalman por aritmética de índice
**6/6 finders (unânime — raro).**
`history[len(in_session) - 1]` assume que as barras em-sessão são um **prefixo estrito** de
todas as barras reais. Uma barra real do B3 antes das 09:00 BRT (pré-mercado) quebra a
suposição e o replay encadeia o posterior do bar errado. Mais forte da lista.

### 2. `backend/irai/runtime_revision.py:11` — fingerprint do motor é INCOMPLETO
**4-5 finders.**
A revisão hasheia só `engine.py` + `kalman.py` (+ git HEAD), mas o forecast v2 também
depende de `johansen.py`, `zscore.py`, `miqueias_static.py` — e, crucialmente, dos
`model_params` calibrados no DB (pesos/sigmas/coefs do Kalman). Uma recalibração muda o
`P_up` **sem mudar a revisão** → bundles de modelos diferentes agregados como se fossem o
mesmo. Ataca diretamente a Prioridade 3 ("invalida o que promete?" — aparentemente não
invalida o suficiente).
*Nota do orquestrador: na sessão anterior eu elogiei o hash `engine_sha256`/`kalman_sha256`
como "o design certo". Este candidato refina isso — o hash cobre 2 arquivos, não a cadeia
de dependência completa do forecast. Coerente, merece verificação.*

### 3. `scripts/evaluate_p_dynamic_champions.py:448/453` — guard de revisão FRÁGIL no sentido oposto
**3 finders.**
O fingerprint inclui `git_commit`, e `load_ledger_sessions` descarta **TODAS** as sessões
quando há mais de um grupo de fingerprint. Então qualquer commit que nem toca em
engine/kalman, seguido de um restart da API, **zera permanentemente o ledger de 60 sessões
inteiro**. Combinado com o #2, o vínculo de revisão erra nas duas pontas: fraco demais
(ignora DB/módulos) e rígido demais (git_commit derruba tudo).

### 4. `backend/irai/engine.py:668` — fix do Kalman B3 usa horas hardcoded
**4 finders.**
`is_b3_target = session_start == 9 and session_end == 18` casa por hora exata, enquanto o
alinhamento de eixo +5h/+6h que cria a cauda pós-sessão casa por `source == 'br'`.
Divergência de gating: um alvo B3 com horas diferentes cai fora do fix silenciosamente.
`tests/test_premarket.py:474` só cobre o caso de inverno (mesmo rótulo); o caso de verão
com cruzamento de rótulo fica sem teste.

### 5. `scripts/backtest_p_dynamic_walkforward.py:217` — possível lookahead na barra de decisão
**1 finder, mas é a Prioridade 1.**
Elegibilidade da barra testa só `bar_start < decision_time`, sem checar a grade M5
canônica; uma barra fora-de-grade que começa antes mas **fecha depois** do instante de
decisão se qualifica como "já conhecida". Só um finder pegou e não foi verificado — mas por
ser exatamente a classe de falha (violação PIT) que mais importa nesse script, merece
verificação manual prioritária.

### 6. `docs/artifacts/.../p_dynamic_config_comparison.md` — contagem de testes ainda errada
**3 finders.**
O diff não-commitado troca "410 passed" por "450 passed, 1 skipped", mas os finders
rodando a suíte atual obtiveram 453/455 (orquestrador obteve 446 mais cedo). O número
continua não batendo — só ficou menos errado.

---

## Limpeza no walkforward (severidade menor)

- Regra de janela BRT 09:00-18:00 **duplicada em 4 lugares**.
- `run_walkforward` varre o histórico M5 inteiro **duas vezes**.
- `_percentile` / `_axis_datetime` / `_write_json` **reimplementam helpers já importados**.
- `dict discarded` sempre vazio.
- `MIQUEIAS_FACTORS` hardcoded em vez de derivar da config versionada.

---

## O que precisa acontecer antes de qualquer ação

Nenhum destes vira emenda sem verificação adversarial. #1-#4 têm forte concordância entre
finders e apontam problemas reais no **vínculo de revisão** e no **chaining do Kalman**,
mas concordância-de-finders ≠ verificado. Prioridade de verificação manual, se não retomar
o workflow: **#1, #2, #5** (o unânime, o que fura o fingerprint, e a classe PIT).
