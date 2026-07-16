# IRAI — handoff de contexto para Codex no Ryzen

**Data:** 2026-07-16

**Objetivo:** permitir que uma nova sessão do Codex no Ryzen retome o projeto sem depender do histórico desta conversa.

**Autoridade de escopo e sequência:** `docs/plans/2026-07-13-irai-plano-consolidado.md`.
**Regras operacionais:** `AGENTS.md`, `CLAUDE.md` e Backlog.md.

## 1. Ambiente e fluxo de trabalho

- Produção no Ryzen/WSL: `/home/brenoperucchi/Devs/rastro_irado`.
- API: `rastro-irado-api.service`, porta 8888.
- Collector: `rastro-irado-collector.service`.
- Frontend dev: porta 5175; confirmar o `WorkingDirectory` do processo antes de atribuir um problema ao código em produção.
- GEX: `rastro-irado-gex.timer`, agendado para 09:10 BRT após a primeira M5 do WIN.
- MT5 é executado pelo Python do Windows, ainda que os serviços sejam orquestrados pelo systemd do WSL.
- Não desenvolver diretamente no checkout de produção. Usar clone ou worktree separado e implantar por commit, push e pull fast-forward.
- Claude implementa a correção metodológica atual do GEX. Codex faz revisão independente do commit/diff final.

## 2. Estado do projeto

O IRAI é um painel pessoal de apoio à decisão. Ele não executa ordens. O plano busca separar:

- regime macro (`P_up`);
- estrutura de mercado (GEX/walls);
- confirmação tática (Pair/Z/NWE);
- executabilidade e resultado econômico;
- somente depois, distribuição, shadow live e eventual Execution Layer MT5.

Backlog relevante em 2026-07-16:

- IRAI-4 — NF-01B/VAL-04: em andamento; faltam separar análises confirmatórias e avaliar a regra local com/sem gate IRAI.
- IRAI-5 — rollover WIN/WDO: em andamento; WIN medido, WDO pendente.
- IRAI-17 — comparação `P Dinâmico` Miqueias × v1 × v2: em Review; precisa acumular sessões operacionais e avaliar qualidade OOS.
- IRAI-22 — histórico causal GEX: concluído quanto à fonte, causalidade e paridade live/backfill; a metodologia do Gamma Flip voltou a ser investigada.
- IRAI-7 — decisão de promoção econômica: bloqueado por IRAI-4 e IRAI-5.
- NF-02/03/04 e VAL-05 permanecem condicionados ao gate econômico.

## 3. O que foi corrigido no pipeline GEX

Commits publicados:

- `3155c98` — unifica o GEX WIN live com o bundle oficial causal.
- `d70f273` — fecha documentação e backlog do rollout.

O WIN passou a usar exclusivamente:

- SPRE/B3: posições em aberto das opções IBOV;
- PE/B3: strike, call/put, vencimento e prêmio;
- IR/B3: fechamento do IBOV;
- SPRD/B3: contrato e ajuste do WIN;
- BCB SGS 1178: Selic causal.

Garantias entregues:

- live e backfill chamam o mesmo cálculo;
- arquivos precisam declarar a mesma sessão internamente;
- proveniência registra nomes e SHA-256;
- bundle ausente/inconsistente falha fechado;
- sessão fonte e efetiva vêm do ledger WIN M5, inclusive pós-feriado;
- WDO preserva o fluxo BDI/MT5;
- timer roda às 09:10 BRT após a primeira M5.

Validação do rollout:

- 72 testes GEX relacionados passaram;
- 314 testes amplos passaram e 18 foram ignorados por dependências ausentes no Linux;
- 44/44 testes do worker passaram no Python 3.12 do Windows;
- API, collector e timer ficaram ativos no Ryzen.

## 4. Diagnóstico atual: o GEX ainda não está resolvido metodologicamente

A API foi confirmada correta em relação ao cálculo implementado:

- `active=true`;
- `as_of=2026-07-15`;
- Gamma Max WIN: `191863.354452`;
- Gamma Flip WIN: `186364.052641`;
- Gamma Min WIN: `171805.827309`;
- spot IBOV: `176010.9`;
- fator IBOV→WIN: `1.0104146959`;
- 97 strikes agregados.

Há dois problemas independentes:

### 4.1 Frontend mostra níveis antigos

O screenshot observado exibia várias linhas entre aproximadamente 175.500 e 178.500. Esse conjunto não corresponde às walls retornadas pela API atual, cuja grade derivada do Flip se estende majoritariamente de 177.833 até 194.000.

O frontend `:5175` consome a API `:8888` em modo dev, mas foi identificado que o processo pode estar rodando de outro clone (`/mnt/c/.../rastro_irado`). Deve-se inspecionar `WorkingDirectory`, Network/console do navegador, resposta efetivamente recebida e qualquer cache/render state. Não assumir que `API active=true` prova que o navegador desenhou o mesmo JSON.

### 4.2 Gamma Flip está distante do preço

O Flip calculado em aproximadamente 186.364 WIN está cerca de 5% acima do WIN próximo de 177.842. Isso concentra quase toda a grade acima do preço e diverge do comportamento visual do MagicGEX/Miqueias, que apresenta suporte e resistência ao redor do mercado.

Paridade live↔backfill prova reprodutibilidade, não correção metodológica. A investigação do Claude deve verificar antes de alterar produção:

- netGEX strike a strike;
- sinal e convenção de calls e puts;
- definição de Gamma Flip;
- uso de GEX pontual versus cumulativo;
- seleção quando há múltiplos zero-crossings;
- agregação por vencimento;
- unidades e escala de OI/gamma;
- conversão IBOV→WIN;
- comparação com valores brutos de MagicGEX/Miqueias em sessões comuns.

Não escolher uma fórmula apenas por produzir um gráfico parecido. A decisão precisa ser reproduzível e economicamente defensável.

## 5. Contrato da revisão Codex após o Claude

Quando o Claude entregar commit ou diff:

1. Ler a hipótese, fórmula e evidências sem assumir a conclusão do implementador.
2. Reproduzir o problema anterior com regressão permanente.
3. Conferir causalidade, fontes e ausência de lookahead.
4. Auditar netGEX por strike e múltiplos cruzamentos.
5. Verificar conversão IBOV→WIN e walls nos dois lados do preço quando justificado pelos dados.
6. Comparar o mesmo snapshot no cálculo, banco, API e frontend.
7. Rodar testes estreitos, suíte mantida e validação read-only no Ryzen/Windows.
8. Não implantar antes de GO independente e autorização humana.

## 6. Sequência depois da solução do GEX

1. Recalcular o histórico causal do GEX se a fórmula mudar, preservando versão e hashes.
2. Formalizar com Miqueias região válida, wall relevante, suporte/resistência, alvo, stop, cooldown, invalidação e papel do NWE.
3. Executar a regra em challengers comparáveis: preço+GEX; +Pair/Z; +`P_up` local; +`P_up` Miqueias; +NWE; baselines sem IRAI.
4. Medir expectativa líquida, drawdown, frequência, MFE/MAE, cobertura, maus trades evitados e bons trades bloqueados.
5. Ajustar stop/alvo somente por grade limitada e walk-forward; usar ticks para ambiguidades intrabarra.
6. Concluir rollover WDO no IRAI-5.
7. Executar IRAI-7: parar hipótese, promover regra transparente ou autorizar NF-02 com hipótese incremental pré-registrada.
8. Somente após edge aprovado: NF-03 → NF-04 com flag desligada → VAL-05 shadow live → governança/drift → eventual Execution Layer MT5.

Em paralelo, continuar coletando ticks WIN e capturas diárias do `P Dinâmico` Miqueias × v1 × v2.

## 7. Prompt de retomada recomendado

```text
Leia integralmente AGENTS.md, CLAUDE.md,
docs/handoffs/2026-07-16-irai-contexto-atual.md,
docs/plans/2026-07-13-irai-plano-consolidado.md e a tarefa relevante no Backlog.md.

Este é um checkout de desenvolvimento no Ryzen; não altere diretamente
/home/brenoperucchi/Devs/rastro_irado, usado pela produção.

Claude está implementando a correção metodológica do GEX. Sua função inicial é
preparar e executar revisão independente do commit/diff final. Não aceite
paridade live/backfill como prova de correção da fórmula. Verifique netGEX por
strike, sinais call/put, definição e múltiplos cruzamentos do Gamma Flip,
conversão IBOV→WIN, distribuição das walls, causalidade, banco, API e frontend.
```

## 8. Comandos operacionais úteis

```bash
# Estado de produção
ssh ryzen5wsl
cd /home/brenoperucchi/Devs/rastro_irado
git status --short
git rev-parse HEAD
systemctl --user is-active rastro-irado-api rastro-irado-collector rastro-irado-gex.timer
curl -fsS 'http://127.0.0.1:8888/api/irai/gex?target=WIN%24N'

# Testes GEX
pytest -q tests/test_gex_worker.py tests/test_backfill_gex_history.py
python3 tests/test_gex_worker.py
```

Preservar bancos, logs, caches, artefatos e alterações do usuário. Nunca limpar o checkout de produção com comandos destrutivos.
