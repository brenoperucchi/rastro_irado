# IRAI Multi-Asset — Mapa de Fatores por Ativo

> [!NOTE]
> 13 modelos recalibrados. Regras aplicadas:
> 1. Ativos internacionais **não** utilizam ativos BR (WIN, DOL, DI1).
> 2. Índices americanos (US500, US30, USTEC) **não** utilizam outros índices americanos.
> 3. Horários das Sessões respeitados (BR: 09h às 18h | Internacional: 03h às 22h).
> 4. **Otimização (Score Misto):** Modelos classificados por 70% Acurácia + 30% R² para garantir robustez estrutural (ex: DI no Dólar).
> Última calibração: 2026-04-25

---

## Ranking por Acurácia (Pós-Isolamento e Score Misto)

| # | Ativo | ACC | R² | Fatores | Fator Principal |
|---|---|---|---|---|---|
| 1 | 🇪🇺 **EUR/USD** | **99.2%** | **0.9936** | 8 | DXY (-1.521238) |
| 2 | 🇬🇧 **GBP/USD** | **93.6%** | **0.9203** | 8 | DXY (-5.544002) |
| 3 | 🇯🇵 **USD/JPY** | **91.2%** | **0.9353** | 6 | DXY (+6.770268) |
| 4 | 🇨🇭 **USD/CHF** | **89.2%** | **0.8528** | 8 | DXY (+5.693010) |
| 5 | 🇦🇺 **AUD/USD** | **88.0%** | **0.7324** | 8 | USDCAD (-0.620127) |
| 6 | 💻 **Nasdaq 100** | **83.9%** | **0.7177** | 8 | USDCAD (-0.235021) |
| 7 | 🇨🇦 **USD/CAD** | **83.9%** | **0.7660** | 8 | DXY (+5.279406) |
| 8 | 🇺🇸 **S&P 500** | **82.3%** | **0.7806** | 8 | VIX (-0.165268) |
| 9 | 🏛️ **Dow Jones** | **77.5%** | **0.6209** | 7 | VIX (-0.161183) |
| 10 | 💵 **Mini Dólar** | **76.0%** | **0.4763** | 8 | DI1 (+0.382437) |
| 11 | 🇧🇷 **Mini Índice** | **74.8%** | **0.4671** | 7 | WDO (-0.504052) |
| 12 | 🥇 **Ouro** | **73.5%** | **0.2539** | 8 | AUDUSD (+1.057791) |
| 13 | ₿ **Bitcoin** | **73.1%** | **0.3879** | 8 | USTEC (+1.970084) |

---

## Detalhamento Completo por Ativo

### 1. 🇪🇺 EUR/USD (EURUSD) — ACC 99.2% (Sessão: 03h - 22h)
```
α=19.4887

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  DXY         -1.521238   0.00375   ↓ VENDA
  USDJPY      +0.195887   0.00514   ↑ COMPRA
  GBPUSD      -0.191476   0.00404   ↓ VENDA
  USDCAD      +0.158776   0.00272   ↑ COMPRA
  US30        -0.008054   0.00738   ↓ VENDA
  XAUUSD      -0.003564   0.01449   ↓ VENDA
  USTEC       +0.002236   0.00953   ↑ COMPRA
  BTCUSD      -0.000729   0.02340   ↓ VENDA
```

### 2. 🇬🇧 GBP/USD (GBPUSD) — ACC 93.6% (Sessão: 03h - 22h)
```
α=4.5540

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  DXY         -5.544002   0.00375   ↓ VENDA
  EURUSD      -3.171417   0.00400   ↓ VENDA
  USDJPY      +0.690437   0.00514   ↑ COMPRA
  USDCAD      +0.518754   0.00272   ↑ COMPRA
  USDCHF      +0.269883   0.00463   ↑ COMPRA
  AUDUSD      +0.020085   0.00491   ↑ COMPRA
  USDMXN      +0.012898   0.00461   ↑ COMPRA
  XAUUSD      -0.009561   0.01449   ↓ VENDA
```

### 3. 🇯🇵 USD/JPY (USDJPY) — ACC 91.2% (Sessão: 03h - 22h)
```
α=3.9029

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  DXY         +6.770268   0.00375   ↑ COMPRA
  EURUSD      +3.992904   0.00400   ↑ COMPRA
  GBPUSD      +0.908062   0.00404   ↑ COMPRA
  USDCAD      -0.646577   0.00272   ↓ VENDA
  USDCHF      -0.300321   0.00463   ↓ VENDA
  XAUUSD      +0.015583   0.01449   ↑ COMPRA
```

### 4. 🇨🇭 USD/CHF (USDCHF) — ACC 89.2% (Sessão: 03h - 22h)
```
α=2.7233

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  DXY         +5.693010   0.00375   ↑ COMPRA
  EURUSD      +2.855907   0.00400   ↑ COMPRA
  USDJPY      -0.681072   0.00514   ↓ VENDA
  GBPUSD      +0.676751   0.00404   ↑ COMPRA
  USDCAD      -0.336351   0.00272   ↓ VENDA
  AUDUSD      +0.090694   0.00491   ↑ COMPRA
  VIX         -0.015242   0.03259   ↓ VENDA
  XAUUSD      -0.008145   0.01449   ↓ VENDA
```

### 5. 🇦🇺 AUD/USD (AUDUSD) — ACC 88.0% (Sessão: 03h - 22h)
```
α=2.7568

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  USDCAD      -0.620127   0.00272   ↓ VENDA
  DXY         -0.479995   0.00375   ↓ VENDA
  GBPUSD      +0.180889   0.00404   ↑ COMPRA
  US500       +0.141873   0.00722   ↑ COMPRA
  USDCHF      +0.140720   0.00463   ↑ COMPRA
  US30        -0.097063   0.00738   ↓ VENDA
  XAUUSD      +0.053932   0.01449   ↑ COMPRA
  VIX         -0.038219   0.03259   ↓ VENDA
```

### 6. 💻 Nasdaq 100 (USTEC) — ACC 83.9% (Sessão: 03h - 22h)
```
α=4.6038

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  USDCAD      -0.235021   0.00272   ↓ VENDA
  VIX         -0.203637   0.03259   ↓ VENDA
  EURUSD      -0.179837   0.00400   ↓ VENDA
  GBPUSD      +0.171541   0.00404   ↑ COMPRA
  BTCUSD      +0.078266   0.02340   ↑ COMPRA
  XAUUSD      +0.064305   0.01449   ↑ COMPRA
  USDJPY      +0.031433   0.00514   ↑ COMPRA
  BRENT       -0.002326   0.02265   ↓ VENDA
```

### 7. 🇨🇦 USD/CAD (USDCAD) — ACC 83.9% (Sessão: 03h - 22h)
```
α=2.2918

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  DXY         +5.279406   0.00375   ↑ COMPRA
  EURUSD      +3.188058   0.00400   ↑ COMPRA
  GBPUSD      +0.698719   0.00404   ↑ COMPRA
  USDJPY      -0.671048   0.00514   ↓ VENDA
  USDCHF      -0.178654   0.00463   ↓ VENDA
  AUDUSD      -0.139877   0.00491   ↓ VENDA
  US500       +0.053546   0.00722   ↑ COMPRA
  CHINA50     +0.011684   0.00858   ↑ COMPRA
```

### 8. 🇺🇸 S&P 500 (US500) — ACC 82.3% (Sessão: 03h - 22h)
```
α=6.1502

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  VIX         -0.165268   0.03259   ↓ VENDA
  EURUSD      -0.123561   0.00400   ↓ VENDA
  AUDUSD      +0.121529   0.00491   ↑ COMPRA
  DXY         -0.114036   0.00375   ↓ VENDA
  GBPUSD      +0.091197   0.00404   ↑ COMPRA
  USDCAD      -0.065354   0.00272   ↓ VENDA
  BTCUSD      +0.043232   0.02340   ↑ COMPRA
  USDCHF      +0.041257   0.00463   ↑ COMPRA
```

### 9. 🏛️ Dow Jones (US30) — ACC 77.5% (Sessão: 03h - 22h)
```
α=6.9163

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  VIX         -0.161183   0.03259   ↓ VENDA
  GBPUSD      +0.115528   0.00404   ↑ COMPRA
  EURUSD      -0.084145   0.00400   ↓ VENDA
  USDMXN      -0.065152   0.00461   ↓ VENDA
  USDJPY      -0.041426   0.00514   ↓ VENDA
  BRENT       -0.034791   0.02265   ↓ VENDA
  AUDUSD      -0.003756   0.00491   ↓ VENDA
```

### 10. 💵 Mini Dólar (WDO$N) — ACC 76.0% (Sessão: 09h - 18h)
```
α=2.1437

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  DI1         +0.382437   0.00712   ↑ COMPRA
  USDCAD      +0.295066   0.00223   ↑ COMPRA
  WIN         -0.226644   0.00991   ↓ VENDA
  EURUSD      -0.221139   0.00323   ↓ VENDA
  USDCHF      -0.144108   0.00384   ↓ VENDA
  US30        +0.112370   0.00591   ↑ COMPRA
  BTCUSD      -0.046567   0.01604   ↓ VENDA
  BRENT       -0.030000   0.01748   ↓ VENDA
```

### 11. 🇧🇷 Mini Índice (WIN$N) — ACC 74.8% (Sessão: 09h - 18h)
```
α=1.3845

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  WDO         -0.504052   0.00668   ↓ VENDA
  DI1         -0.481067   0.00712   ↓ VENDA
  USDMXN      -0.245899   0.00398   ↓ VENDA
  USDCAD      -0.195920   0.00223   ↓ VENDA
  US30        +0.191812   0.00591   ↑ COMPRA
  USDCHF      +0.149724   0.00384   ↑ COMPRA
  GBPUSD      -0.027812   0.00328   ↓ VENDA
```

### 12. 🥇 Ouro (XAUUSD) — ACC 73.5% (Sessão: 03h - 22h)
```
α=0.5459

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  AUDUSD      +1.057791   0.00491   ↑ COMPRA
  US500       -0.815213   0.00722   ↓ VENDA
  USTEC       +0.710698   0.00953   ↑ COMPRA
  USDMXN      -0.522826   0.00461   ↓ VENDA
  US30        +0.404179   0.00738   ↑ COMPRA
  VIX         +0.177545   0.03259   ↑ COMPRA
  CHINA50     +0.079506   0.00858   ↑ COMPRA
  BTCUSD      +0.023887   0.02340   ↑ COMPRA
```

### 13. ₿ Bitcoin (BTCUSD) — ACC 73.1% (Sessão: 03h - 22h)
```
α=0.6084

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  USTEC       +1.970084   0.00953   ↑ COMPRA
  US500       -1.722516   0.00722   ↓ VENDA
  GBPUSD      +0.987179   0.00404   ↑ COMPRA
  USDMXN      -0.927982   0.00461   ↓ VENDA
  US30        +0.724595   0.00738   ↑ COMPRA
  USDJPY      +0.592452   0.00514   ↑ COMPRA
  EURUSD      -0.540041   0.00400   ↓ VENDA
  USDCHF      -0.321126   0.00463   ↓ VENDA
```

