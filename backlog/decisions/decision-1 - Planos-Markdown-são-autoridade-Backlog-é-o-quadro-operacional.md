---
id: decision-1
title: Planos Markdown são autoridade; Backlog é o quadro operacional
date: '2026-07-15 22:50'
status: accepted
---
## Context

O projeto já possui um plano consolidado como fonte oficial de status e uma especificação
normativa do Tactical. A introdução do Backlog.md não deve criar uma segunda fonte de
verdade para regras de negócio.


## Decision

Os arquivos em `docs/plans/` continuam sendo autoridade de escopo, sequência, regras e
critérios. O Backlog.md gerencia execução diária, responsáveis, dependências, aceite e
estado das tarefas. Mudanças de direção são registradas primeiro nos planos e depois
refletidas nas tasks.


## Consequences

- Tasks podem apontar para planos, mas não substituí-los.
- O fechamento de uma task exige atualizar o status no plano quando alterar o roadmap.
- Claude e Codex usam o mesmo servidor MCP `backlog` e os mesmos arquivos versionados.
