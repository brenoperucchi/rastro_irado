# IRAI Multi-Asset — Mapa de Fatores por Ativo

> [!NOTE]
> 13 modelos calibrados. Regras aplicadas:
> 1. Ativos internacionais **não** utilizam ativos BR (WIN, DOL, DI1).
> 2. Índices americanos (US500, US30, USTEC) **não** utilizam outros índices americanos.
> 3. Horários das Sessões respeitados (BR: 09h às 18h | Internacional: 03h às 22h).
> 4. **Otimização (Score Misto):** Modelos classificados por 70% Acurácia + 30% R².
> 5. **Pares de moedas major (EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF) não utilizam DXY** — os próprios pares compõem o índice, gerando multicolinearidade artificial. Fatores alternativos economicamente independentes são selecionados via brute-force.
> Última calibração: 2026-04-27

---

## Ranking por Acurácia

| # | Ativo | ACC | R² | Fatores | Fator Principal |
|---|---|---|---|---|---|
| 1 | 🇪🇺 **EUR/USD** | **91.2%** | **0.8601** | 7 | USDCHF (-0.426650) |
| 2 | 🇨🇭 **USD/CHF** | **90.0%** | **0.7741** | 7 | EURUSD (-0.879301) |
| 3 | 🇬🇧 **GBP/USD** | **88.8%** | **0.6944** | 7 | EURUSD (+0.514644) |
| 4 | 🇦🇺 **AUD/USD** | **86.4%** | **0.7382** | 7 | USDCAD (-0.666021) |
| 5 | 💻 **Nasdaq 100** | **83.9%** | **0.7177** | 8 | USDCAD (-0.235021) |
| 6 | 🇺🇸 **S&P 500** | **82.3%** | **0.7806** | 8 | VIX (-0.165268) |
| 7 | 🇨🇦 **USD/CAD** | **80.8%** | **0.5604** | 6 | AUDUSD (-0.337907) |
| 8 | 🇯🇵 **USD/JPY** | **80.4%** | **0.5401** | 7 | EURUSD (-0.576287) |
| 9 | 🏛️ **Dow Jones** | **77.5%** | **0.6209** | 7 | VIX (-0.161183) |
| 10 | 💵 **Mini Dólar** | **76.0%** | **0.4763** | 8 | DI1 (+0.382437) |
| 11 | 🇧🇷 **Mini Índice** | **74.8%** | **0.4671** | 7 | WDO (-0.504052) |
| 12 | 🥇 **Ouro** | **73.5%** | **0.2539** | 8 | AUDUSD (+1.057791) |
| 13 | ₿ **Bitcoin** | **73.1%** | **0.3879** | 8 | USTEC (+1.970084) |

> [!IMPORTANT]
> Os majors perderam ACC vs. calibrações anteriores com DXY (era ~88–99%). A queda é **esperada e correta**: o ganho anterior era artificial — DXY é derivado dos próprios pares. Acurácias atuais (80–91%) refletem poder preditivo real com fatores independentes.

---

## Detalhamento Completo por Ativo

### 1. 🇪🇺 EUR/USD (EURUSD) — ACC 91.2% | LogACC 90.4% (Sessão: 03h - 22h)
```
α=6.3231  intercept=-0.2188

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  USDCHF      -0.426650   0.00462   ↓ VENDA
  GBPUSD      +0.239618   0.00403   ↑ COMPRA
  USDMXN      -0.178371   0.00460   ↓ VENDA
  USDJPY      -0.111855   0.00514   ↓ VENDA
  US30        -0.034772   0.00737   ↓ VENDA
  BRENT       -0.014889   0.02260   ↓ VENDA
  VIX         +0.004446   0.03253   ↑ COMPRA
```

### 2. 🇨🇭 USD/CHF (USDCHF) — ACC 90.0% | LogACC 87.6% (Sessão: 03h - 22h)
```
α=3.6686  intercept=0.0301

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  EURUSD      -0.879301   0.00400   ↓ VENDA
  GBPUSD      -0.124394   0.00403   ↓ VENDA
  US500       +0.097273   0.00720   ↑ COMPRA
  US30        -0.090259   0.00737   ↓ VENDA
  XAUUSD      -0.025174   0.01446   ↓ VENDA
  VIX         -0.016667   0.03253   ↓ VENDA
  USTEC       -0.004621   0.00951   ↓ VENDA
```

### 3. 🇬🇧 GBP/USD (GBPUSD) — ACC 88.8% | LogACC 87.6% (Sessão: 03h - 22h)
```
α=4.9047  intercept=-0.1094

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  EURUSD      +0.514644   0.00400   ↑ COMPRA
  USDJPY      -0.098070   0.00513   ↓ VENDA
  USDCAD      -0.093623   0.00273   ↓ VENDA
  USDCHF      -0.070371   0.00462   ↓ VENDA
  US30        +0.057772   0.00737   ↑ COMPRA
  USDMXN      -0.137034   0.00460   ↓ VENDA
  BRENT       +0.003516   0.02260   ↑ COMPRA
```

### 4. 🇦🇺 AUD/USD (AUDUSD) — ACC 86.4% | LogACC 83.2% (Sessão: 03h - 22h)
```
α=2.7137  intercept=0.1905

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  USDCAD      -0.666021   0.00273   ↓ VENDA
  USDMXN      -0.260634   0.00460   ↓ VENDA
  GBPUSD      +0.199815   0.00403   ↑ COMPRA
  EURUSD      +0.180872   0.00400   ↑ COMPRA
  USTEC       +0.086827   0.00951   ↑ COMPRA
  US30        -0.051223   0.00737   ↓ VENDA
  VIX         -0.023360   0.03253   ↓ VENDA
```

### 5. 💻 Nasdaq 100 (USTEC) — ACC 83.9% (Sessão: 03h - 22h)
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

### 6. 🇺🇸 S&P 500 (US500) — ACC 82.3% (Sessão: 03h - 22h)
```
α=6.1502

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  VIX         -0.165268   0.03259   ↓ VENDA
  EURUSD      -0.123561   0.00400   ↓ VENDA
  AUDUSD      +0.121529   0.00491   ↑ COMPRA
  GBPUSD      +0.091197   0.00404   ↑ COMPRA
  USDCAD      -0.065354   0.00272   ↓ VENDA
  BTCUSD      +0.043232   0.02340   ↑ COMPRA
  USDCHF      +0.041257   0.00463   ↑ COMPRA
```

> [!NOTE]
> US500 mantém EURUSD e AUDUSD como fatores de risco-sentimento global. DXY foi removido na calibração anterior (regra: índices US não usam DXY, apenas majors individuais selecionados pelo brute-force).

### 7. 🇨🇦 USD/CAD (USDCAD) — ACC 80.8% | LogACC 78.0% (Sessão: 03h - 22h)
```
α=4.1323  intercept=0.2882

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  AUDUSD      -0.337907   0.00492   ↓ VENDA
  USDCHF      +0.206200   0.00462   ↑ COMPRA
  EURUSD      +0.038870   0.00400   ↑ COMPRA
  XAUUSD      +0.006811   0.01446   ↑ COMPRA
  VIX         -0.014124   0.03253   ↓ VENDA
  BRENT       -0.009516   0.02260   ↓ VENDA
```

### 8. 🇯🇵 USD/JPY (USDJPY) — ACC 80.4% | LogACC 79.2% (Sessão: 03h - 22h)
```
α=2.4314  intercept=0.4823

  Fator       Peso        σ         Direção
  ──────────  ──────────  ────────  ─────────
  EURUSD      -0.576287   0.00400   ↓ VENDA
  USDCHF      +0.265177   0.00462   ↑ COMPRA
  BTCUSD      +0.025371   0.02341   ↑ COMPRA
  BRENT       +0.023314   0.02260   ↑ COMPRA
  US30        +0.014627   0.00737   ↑ COMPRA
  XAUUSD      +0.014013   0.01446   ↑ COMPRA
  AUDUSD      -0.093953   0.00492   ↓ VENDA
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

---

## Decisão de Arquitetura: Por Que Excluir DXY dos Majors

O **DXY (Dollar Index)** é calculado como média ponderada de 6 moedas:

| Moeda | Peso no DXY |
|---|---|
| EUR/USD | 57.6% |
| USD/JPY | 13.6% |
| GBP/USD | 11.9% |
| USD/CAD | 9.1% |
| USD/SEK | 4.2% |
| USD/CHF | 3.6% |

Usar DXY como fator preditivo de qualquer um desses pares é **tautologia** — você está usando o índice para prever um dos seus próprios componentes. O resultado era acurácia artificialmente alta (~90–99% R²) por multicolinearidade, sem poder preditivo real.

**Pós-exclusão:** os modelos selecionam fatores economicamente justificáveis — outros pares de moedas, VIX, commodities (BRENT, XAUUSD), e sentimento de risco (US30, USTEC). A acurácia direcional real ficou em 80–91%.


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

