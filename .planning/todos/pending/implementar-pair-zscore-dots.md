---
title: "Implementar Pair Z-Score Dots no IRAI"
date: 2026-05-12
priority: medium
context: "Exploração /gsd-explore sobre sinal pairwise dinâmico"
ref: ".planning/notes/pair-zscore-signal.md"
---

# Implementar Pair Z-Score Dots

## Objetivo
Adicionar sinal de compra/venda pairwise no gráfico do IRAI para todos os targets,
baseado no z-score do spread entre o target e o fator de maior peso Kalman.

## Tarefas

### Backend
- [ ] Em `engine.py` → `compute_from_db()` (v2), após `kf.update()`:
  - Extrair o fator de maior |β| do estado Kalman
  - Calcular resíduo pairwise: `ret_target - β_max * ret_factor`
  - Calcular σ rolling do resíduo (20 sessões)
  - Calcular z_pair normalizado por √t
  - Injetar `pair_z`, `pair_factor`, `pair_beta`, `pair_signal` no IRAISnapshot
- [ ] Adicionar campos no dataclass `IRAISnapshot`
- [ ] Adicionar `pair_threshold` ao `divergence_config` no DB

### Frontend
- [ ] Renderizar dots 🟢/🔴 no gráfico superior de preço
- [ ] Mostrar par ativo + beta no gauge (ex: "Par: WDO | β=-0.72")
- [ ] Adicionar coluna "Pr" (Pair) no heatmap D-P-Z-E

### Validação
- [ ] Backtest: comparar frequência e acurácia do sinal pairwise vs Z atual
- [ ] Verificar que os dots aparecem corretamente para WIN, S&P, US30
