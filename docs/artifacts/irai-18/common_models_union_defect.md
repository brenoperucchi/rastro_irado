# IRAI-18 — `common_models` por união colapsa o gate de 60 sessões

**Status:** aberto, **pré-existente**, hoje inativo.
**Escopo:** deliberadamente **fora** do commit da `methodology_version: 2`.
**Confirmado por:** três revisões independentes em rodadas separadas (duas lentes distintas),
mais reprodução direta.

## Defeito

`scripts/evaluate_p_dynamic_champions.py` deriva `common_models` como a **união** dos
modelos de todas as sessões do ledger, e depois exige que **todos** estejam presentes em
cada sessão para que ela seja comparável:

```python
common_models = sorted(set().union(*(set(session.forecasts) for session in sessions)))
comparable_sessions = [
    session for session in sessions
    if all(session.forecasts.get(model) for model in common_models)
]
```

Uma única rodada manual com `--candidate` ou `--miqueias-static-config` gravando em
`data/p_dynamic_parity` faz esse challenger entrar na união — e derruba do comparável
**todas** as sessões que não o contêm.

## Reprodução

Copiando `v2.json` como challenger extra em 1 de 2 bundles e registrando-o no manifesto:

```
antes:  Champion-challenger: INCONCLUSIVE — sessões=2/60
depois: Champion-challenger: INCONCLUSIVE — sessões=1/60
```

Com 60 sessões acumuladas, uma rodada manual reduziria o comparável a 1. O relatório
mostraria apenas *"amostra abaixo do gate"* — sem apontar a causa. O gate **nunca
fecharia** e ninguém saberia por quê: é uma falha silenciosa que custa ~3 meses de
calendário.

## Agravante introduzido pela `methodology_version: 2`

Com a pontuação na interseção de timestamps, o challenger esporádico passa a entrar
também na interseção, **encolhendo as barras pontuadas de todos os modelos** naquela
sessão — não só invalidando sessões, mas degradando a que sobra.

## Emenda proposta

Fixar o roster do torneio (`miqueias`, `v1`, `v2`, `baseline_climatology`) e avaliar
challengers esporádicos em pareamento próprio sobre o subconjunto de sessões em que
existem. Registrar em `audit` cada sessão excluída por ausência de modelo, para que a
causa apareça no relatório em vez de virar um "abaixo do gate" mudo.

## Por que não foi corrigido junto

O commit da `methodology_version: 2` já altera a regra de elegibilidade, a métrica oficial
do torneio e reinicia o ledger. Misturar uma mudança no roster do torneio tornaria
impossível atribuir qualquer variação futura de resultado a uma causa única.

## Estado atual

Inativo: todos os bundles capturados têm exatamente os mesmos três modelos
(`miqueias`, `v1`, `v2`). O risco se materializa na primeira rodada manual com challenger
gravando no diretório de produção — que o docstring de
`scripts/compare_p_dynamic_parity.py` explicitamente convida a fazer.
