---
title: "Pair Z-Score Signal — Design Técnico"
date: 2026-05-12
context: "Exploração sobre substituir o sinal Z multivariate por um sinal pairwise dinâmico"
---

# Pair Z-Score Signal — Sinal de Compra/Venda por Par Dinâmico

## Motivação

O sinal Z atual do IRAI compara o P(↑) do modelo completo (N fatores) contra o retorno
do preço do target. Funciona, mas é **indireto** — o preço diverge do "consenso" de 6+ fatores.

A ideia é criar um sinal mais **direto e operável**: um pair trading pairwise entre o target
e o seu fator de maior peso no Kalman Filter naquele momento.

## Design

### Seleção Dinâmica do Par

A cada sessão (ou a cada barra no v2), o Kalman Filter atualiza os betas de todos os fatores.
O **par ativo** é o fator com maior `|βᵢ|` no estado atual do Kalman:

```
par_ativo = argmax_i |β_i(t)|
```

Exemplos esperados:
- WIN → WDO (beta mais forte, correlação inversa histórica)
- S&P 500 → DXY (relação macro clássica)
- US30 → US500 (alta correlação entre índices americanos)

O par pode mudar dinamicamente: se amanhã o VIX ganhar mais peso que o DXY pro S&P,
o sinal muda pro VIX automaticamente.

### Cálculo do Z-Score Pairwise

```
1. β = peso Kalman do par ativo (já disponível no engine v2)
2. retorno_esperado = β × retorno_par_ativo(t)
3. resíduo = retorno_target(t) − retorno_esperado
4. σ_resíduo = std rolling do resíduo (janela de 20 BARRAS intra-sessão)
   ⚠️ z = (r − μ_janela)/σ_janela — CENTRADO na média e SEM √t (mudou em 65321f7;
      a fórmula `z = r/(σ·√t)` citada abaixo nesta nota está obsoleta)
5. z_pair = resíduo / (σ_resíduo × √t)
```

A normalização por `√t` mantém a consistência com o z-score existente —
um resíduo de 0.3% às 10:15 é muito mais significativo que às 16:00.

### Geração do Sinal

| Condição | Sinal | Significado |
|----------|-------|-------------|
| `z_pair < -threshold` (qualquer β ≠ 0) | 🟢 Compra | Target BARATO vs. seu hedge — o resíduo deve reverter pra cima |
| `z_pair > +threshold` (qualquer β ≠ 0) | 🔴 Venda | Target CARO vs. seu hedge — o resíduo deve reverter pra baixo |
| `|z_pair| < threshold` | Neutro | Spread dentro do normal |

> ⚠️ **CORRIGIDO em 2026-07-12.** A versão original desta tabela tinha 4 linhas e
> fazia a direção depender do **sinal de β** (β>0 invertia compra/venda). Estava
> **errada**, e o código a implementou fielmente por 2 meses.
>
> **Por quê:** o resíduo é `ret_target − β·ret_fator`. O β multiplica **apenas a
> perna do fator**, logo `∂resíduo/∂ret_target = +1` para **todo** β, e o z é afim
> crescente no resíduo. Resíduo abaixo da média ⇒ o target precisa **subir** para
> reverter ⇒ **compra**, qualquer que seja o β. Usar `sign(β)` na direção **conta o
> β duas vezes** — ele já foi liquidado dentro do resíduo.
>
> **Como o erro nasceu:** a tabela antiga era a ação correta da **perna errada**.
> "Long do spread" = comprar 1 target e **vender β do fator**; a perna do *fator* é
> sell se β>0 / buy se β<0. Isso coincide com a ação do *target* só quando β<0 — e
> o par-carro-chefe do projeto (WIN↔WDO, β<0) caía justamente no ramo que estava
> certo por acidente, mascarando o bug. No **WDO$N**, cujo par ativo é o DI1 com
> **β>0 em 100% das barras**, todos os sinais saíam invertidos.
>
> O backtest do próprio projeto (`scripts/optimize_zscore.py`) sempre foi
> β-agnóstico — o ramo β só existiu no caminho de display, que nunca foi medido.
>
> Invariante travada em `tests/test_pair_zscore.py`
> (`test_sinal_independe_do_sinal_de_beta`, `test_mesmo_residuo_mesmo_sinal_qualquer_beta`).

O threshold padrão sugerido: **1.5σ** (pode ser otimizado por backtest).

### Diferença do Sinal Z Atual

| Aspecto | Z Atual (IRAI) | Z Pairwise (novo) |
|---------|---------------|-------------------|
| Compara | P(↑) multivariate vs preço | 1 fator vs preço |
| Critério | Divergência probabilidade vs retorno | Divergência de spread mean-reverting |
| Base teórica | Consenso macro | Cointegração (Kalman) |
| Sensibilidade | Menor (N fatores diluem) | Maior (relação direta) |
| Frequência | Menos sinais | Mais sinais |

## Onde Implementar

### Backend (`engine.py`)
- No loop principal de `compute_from_db` (v2), após `kf.update()`:
  - Extrair `β_max = max(|betas[1:]|)` e o índice do fator correspondente
  - Calcular resíduo pairwise
  - Calcular z-score do resíduo
  - Injetar `pair_z`, `pair_factor`, `pair_signal` no snapshot

### Frontend (`App.jsx`)
- Renderizar dots 🟢/🔴 no gráfico de preço (chart superior)
- Adicionar coluna no heatmap D-P-Z-E → D-P-Z-**Pr**-E (Pr = Pair)
- Mostrar qual par está ativo no gauge ("Par: WDO | β=-0.72")

## Parâmetros

| Parâmetro | Default | Fonte |
|-----------|---------|-------|
| `pair_threshold` | 1.5 | `divergence_config` no DB |
| `pair_sigma_window` | 20 sessões | `divergence_config` no DB |
| `pair_min_beta` | 0.1 | Mínimo de |β| pra considerar o par válido |
