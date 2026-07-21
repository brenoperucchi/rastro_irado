# Revisão xhigh do branch `fix/irai-18` — validação parcial de candidatos

**Status: o workflow automático original foi abortado, mas os itens 1, 2, 3 e 5 foram
confirmados, corrigidos e aprovados por revisão independente em 2026-07-21.**

> ⚠️ **O resultado "0 findings" foi enganoso.** A revisão **não** voltou limpa: a fase de
> verificação foi inteira abortada pelo limite de sessão. Os 6 finders rodaram e
> produziram 31 candidatos, mas os **19 verificadores + o sweep morreram todos** antes de
> confirmar ou refutar qualquer um. O que segue começou como **candidatos recuperados do
> journal**. A validação manual e a revisão independente posteriores só cobriram os itens
> explicitamente marcados `CONFIRMADO`; os demais não ganharam esse selo.

Os itens 1, 2, 3 e 5 agora têm selo `CONFIRMADO` por reprodução controlada abaixo e foram
corrigidos com regressões. Os demais continuam `SOLO`/`CONSENSO-DE-FINDERS` — **não**
`CONFIRMADO`. Concordância entre finders independentes não vira ação sem verificação.

## Retomada (barata)

- `resumeFromRunId: wf_3336f44b-a56`
- Limite reseta **20:20 America/São_Paulo**.
- Os 6 finders voltam do cache instantaneamente; só os verificadores rodam de novo.

---

## Candidatos de correção (ordenados por confiança)

### 1. `scripts/measure_pair_signal_value.py:247` — chaining do Kalman por aritmética de índice
**CONFIRMADO.** Originalmente **6/6 finders (unânime — raro).**
`history[len(in_session) - 1]` assume que as barras em-sessão são um **prefixo estrito** de
todas as barras reais. Uma barra real do B3 antes das 09:00 BRT (pré-mercado) quebra a
suposição e o replay encadeia o posterior do bar errado. Mais forte da lista.

**Reprodução manual:** fixture do engine com uma barra `WIN$N` real às 08:55 BRT e 40
barras de 09:00–12:15 BRT. O replay teve `real=41`, `in_session=40` e injetou na sessão
seguinte a média do update 40; o posterior correto da última barra econômica era o update
41. A causa é exatamente o índice por contagem, não um problema de fixture.

### 2. `backend/irai/runtime_revision.py:11` — fingerprint do motor é INCOMPLETO
**CONFIRMADO.** Originalmente **4-5 finders.**
A revisão hasheia só `engine.py` + `kalman.py` (+ git HEAD), mas o forecast v2 também
depende de `johansen.py`, `zscore.py`, `miqueias_static.py` — e, crucialmente, dos
`model_params` calibrados no DB (pesos/sigmas/coefs do Kalman). Uma recalibração muda o
`P_up` **sem mudar a revisão** → bundles de modelos diferentes agregados como se fossem o
mesmo. Ataca diretamente a Prioridade 3 ("invalida o que promete?" — aparentemente não
invalida o suficiente).
*Nota do orquestrador: na sessão anterior eu elogiei o hash `engine_sha256`/`kalman_sha256`
como "o design certo". Este candidato refina isso — o hash cobre 2 arquivos, não a cadeia
de dependência completa do forecast. Coerente, merece verificação.*

**Reprodução manual:** numa cópia fechada do snapshot, alterar somente o último
`model_params.win_alpha` de `0,7365663101514398` para `1,1048494652271597` alterou o
`P_up` v1 de 37,98 para 32,39. `build_engine_revision()` retornou exatamente o mesmo
fingerprint antes e depois. Portanto, o vínculo atual não separa calibrações diferentes;
a extensão a outros módulos importados ainda requer verificação de escopo para o desenho
da correção.

### 3. `scripts/evaluate_p_dynamic_champions.py:448/453` — guard de revisão FRÁGIL no sentido oposto
**CONFIRMADO.** Originalmente **3 finders.**
O fingerprint inclui `git_commit`, e `load_ledger_sessions` descarta **TODAS** as sessões
quando há mais de um grupo de fingerprint. Então qualquer commit que nem toca em
engine/kalman, seguido de um restart da API, **zera permanentemente o ledger de 60 sessões
inteiro**. Combinado com o #2, o vínculo de revisão erra nas duas pontas: fraco demais
(ignora DB/módulos) e rígido demais (git_commit derruba tudo).

**Reprodução manual:** dois bundles íntegros, com a mesma configuração e os mesmos hashes
de cálculo, mas `git_commit` diferentes, foram tratados como dois grupos e o avaliador
retornou `selected_sessions=0`. Um commit documental seguido de restart da API teria o
mesmo efeito; portanto a identidade de agrupamento precisa excluir o commit auditável.

### 4. `backend/irai/engine.py:668` — fix do Kalman B3 usa horas hardcoded
**4 finders.**
`is_b3_target = session_start == 9 and session_end == 18` casa por hora exata, enquanto o
alinhamento de eixo +5h/+6h que cria a cauda pós-sessão casa por `source == 'br'`.
Divergência de gating: um alvo B3 com horas diferentes cai fora do fix silenciosamente.
`tests/test_premarket.py:474` só cobre o caso de inverno (mesmo rótulo); o caso de verão
com cruzamento de rótulo fica sem teste.

### 5. `scripts/backtest_p_dynamic_walkforward.py:217` — possível lookahead na barra de decisão
**CONFIRMADO.** Originalmente **1 finder, mas é a Prioridade 1.**
Elegibilidade da barra testa só `bar_start < decision_time`, sem checar a grade M5
canônica; uma barra fora-de-grade que começa antes mas **fecha depois** do instante de
decisão se qualifica como "já conhecida". Só um finder pegou e não foi verificado — mas por
ser exatamente a classe de falha (violação PIT) que mais importa nesse script, merece
verificação manual prioritária.

**Reprodução manual:** com decisão às 10:00 BRT, snapshots comuns às 09:00, 09:57 e
17:55 BRT fizeram `build_observation()` selecionar a barra de 09:57, cujo fechamento é
10:02. A previsão de 99% entrou no relatório apesar de ainda não ser conhecida às 10:00.

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

Os itens confirmados 1, 2, 3 e 5 foram corrigidos com regressões e receberam revisão
independente `GO`; validação final: `pytest -q tests` → **477 passed, 1 skipped**. #4,
#6 e a limpeza continuam sem verificação adversarial; concordância-de-finders não é
confirmação.
