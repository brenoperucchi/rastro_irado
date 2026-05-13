---
title: "Pair Z-Score Multivariate — Resíduo do Modelo Completo"
trigger_condition: "Quando o sinal pairwise (1 fator) estiver validado e operacional por pelo menos 2 semanas"
planted_date: 2026-05-12
context: "Evolução natural do pair z-score pairwise para multivariate"
---

# Pair Z-Score Multivariate

## Ideia
Após validar o sinal pairwise (target vs 1 fator de maior β), evoluir para usar o
**resíduo completo do Kalman** (target vs TODOS os fatores).

## Motivação
O resíduo multivariate captura divergências que nenhum fator individual explica.
Se o WIN está caindo mas TODOS os fatores (WDO, DI, DXY, Brent, China, MXN) dizem
que deveria subir, o sinal é mais forte que qualquer par isolado.

## Diferença
```
Pairwise:     resíduo = ret_target − β_max × ret_fator_max
Multivariate: resíduo = ret_target − Σ βᵢ × ret_fator_i    (modelo Kalman completo)
```

## Riscos
- Overfitting: com N fatores, o modelo pode se ajustar demais e o resíduo ficar artificialmente pequeno
- O sinal pode ser mais raro (spread multivariate é mais "explicado")
- Precisa de mais dados pra calibrar σ do resíduo

## Trigger
Implementar quando:
- O pairwise estiver rodando em produção por 2+ semanas
- Backtest do pairwise mostrar profit factor > 1.5
- Houver demanda por sinal de maior convicção (menos frequente, mais certeiro)
