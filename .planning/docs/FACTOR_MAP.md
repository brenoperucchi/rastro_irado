# IRAI Multi-Asset — Mapa de Fatores por Ativo

> [!NOTE]
> 20 modelos ativos extraídos diretamente do banco de dados (irai.db).
> Regras aplicadas:
> 1. Ativos internacionais **não** utilizam ativos BR (WIN, DOL, DI1).
> 2. Índices americanos (US500, US30, USTEC) **não** utilizam outros índices americanos.
> 3. Horários das Sessões respeitados.
> 4. **Otimização (Score Misto):** Modelos classificados por 70% Acurácia + 30% R² para garantir robustez estrutural.

---

## Ranking por Acurácia (Pós-Isolamento e Score Misto)

| # | Ativo | ACC | R² | Fatores | Fator Principal |
|---|---|---|---|---|---|
| 1 | 💵 **Mini Dólar** | **73.9%** | **0.4985** | 8 | DI1$N (0.3616) |
| 2 | 🇧🇷 **Mini Índice** | **69.0%** | **0.4640** | 8 | iSharesTreasury1-3+ (-0.8004) |

---

## Detalhamento Completo por Ativo

### 1. 💵 Mini Dólar (WDO$N) — ACC 73.9% (Sessão: 09h - 18h)
```
α=2.5654

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  DI1$N       0.361647    0.00497   ↑ COMPRA
  WIN$N       -0.237025   0.00583   ↓ VENDA
  USDCHF      0.141296    0.00349   ↑ COMPRA
  DE40        0.065169    0.00730   ↑ COMPRA
  US500       -0.039270   0.00635   ↓ VENDA
  iSharesCurrencyBond+  -0.025679   0.00896   ↓ VENDA
  USTEC       -0.019262   0.00914   ↓ VENDA
  VIX         0.009290    0.03430   ↑ COMPRA
```

### 2. 🇧🇷 Mini Índice (WIN$N) — ACC 69.0% (Sessão: 09h - 18h)
```
α=0.7366

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  iSharesTreasury1-3+  -0.800422   0.00049   ↓ VENDA
  DI1$N       -0.431176   0.00484   ↓ VENDA
  WDO$N       -0.428164   0.00455   ↓ VENDA
  US30        0.111251    0.00649   ↑ COMPRA
  CADCHF      0.110682    0.00274   ↑ COMPRA
  USDMXN      0.037873    0.00377   ↑ COMPRA
  BTCUSD      0.028140    0.01843   ↑ COMPRA
  BRENT       -0.009650   0.01992   ↓ VENDA
```

