# IRAI — Intraday Risk Appetite Index

Dashboard cross-asset em tempo real que estima a **probabilidade de o IBOV fechar o dia em alta**, olhando não para o próprio índice, mas para o comportamento de 6 fatores macro que historicamente lideram o movimento do índice brasileiro.

> *"Neste momento do pregão, o resto do mundo está dizendo que o IBOV deveria estar subindo ou caindo?"*

Atualiza a cada 30 segundos. Reseta no open da B3 (10:00 BRT). Opera com dois terminais MetaTrader 5 sequenciais — um nacional (XP) para WIN/DOL/DI, um internacional (Tickmill) para DXY, BRENT, CHINA50 e USDMXN.

## Documentação relacionada

- [`PRD.md`](./PRD.md) — visão, objetivos, escopo, métricas de sucesso
- [`SPEC.md`](./SPEC.md) — arquitetura, schema, algoritmo, API contract

---

## Performance do Modelo

| Métrica | Valor |
|---------|-------|
| **Acurácia direcional** | **71.0%** |
| **R²** | 0.4630 |
| **α (logístico)** | 1.3065 |
| **Sessões de treino** | 252 (últimos ~12 meses) |

### Fatores e Pesos (por relevância)

| # | Fator | Label | Peso | Terminal | Lógica |
|---|-------|-------|------|----------|--------|
| 1 | DOL$N | dol | **-0.414** | XP | Dólar sobe → IBOV cai |
| 2 | DI1$N | di | **-0.293** | XP | Juros sobem → IBOV cai |
| 3 | CHINA50 | china | **+0.141** | Tickmill | China sobe → EM risk-on |
| 4 | USDMXN | mxn | **-0.041** | Tickmill | Peso fraco → EM risk-off |
| 5 | DXY | dxy | +0.029 | Tickmill | Dólar global |
| 6 | BRENT | brent | +0.019 | Tickmill | Petróleo |

> **Nota:** Combinação validada via brute-force de 64 combinações possíveis. VIX e IV ATM foram testados mas **removidos por reduzirem a acurácia direcional**.

---

## Arquitetura em 30 segundos

```
MT5 Brasil (XP)     ─┐
                      ├─► collector.py ─► SQLite (irai.db) ─► FastAPI :8888 ─► React :5175
MT5 Tickmill        ─┘
```

- **Um collector unificado** conecta sequencialmente aos dois terminais MT5 a cada ciclo (30s).
- **SQLite com WAL mode** como camada de comunicação — collector escreve, API lê.
- **FastAPI** calcula o IRAI sob demanda via `engine.py`, sem estado em memória.
- **React + Vite** faz polling de 30s no endpoint `/api/irai/current`.

---

## Pré-requisitos

### Software

- **Windows** (MT5 não roda nativo em Linux/Mac).
- **Python 3.11+** com `MetaTrader5`, `numpy`, `pandas`, `scikit-learn`, `scipy`.
- **Node.js 18+**.

### Terminais MT5

1. **XP** — `C:\Program Files\MetaTrader 5 Terminal\terminal64.exe`
   - Símbolos: `WIN$N`, `DOL$N`, `DI1$N`
2. **Tickmill** — `C:\Program Files\Tickmill MT5 Terminal\terminal64.exe`
   - Símbolos: `DXY`, `BRENT`, `CHINA50`, `USDMXN`

---

## Como rodar

```bash
# Backend — API
python -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8888

# Backend — Collector (30s interval)
python backend/workers/collector.py --interval 30

# Frontend
cd frontend && npm run dev
```

Dashboard em `http://localhost:5175`. API em `http://localhost:8888/docs`.

---

## Calibração

Rodar periodicamente (recomendado: semanal):

```bash
python scripts/calibrate_m5.py --db data/irai.db
```

Isso:
1. Carrega ~100k barras M5 de cada símbolo do banco.
2. Constrói sessões B3 (10:00-17:55 BRT) das últimas 252 sessões.
3. Estima pesos via OLS (retornos diários z-score → retorno WIN).
4. Estima α via regressão logística em barras M5 (subsampled).
5. Grava parâmetros em `model_params` com timestamp.
6. Gera relatório em `data/reports/calibration_m5_YYYYMMDD.md`.

**Sempre revisar o relatório** — se algum peso inverteu sinal, é sintoma de regime shift.

---

## Estrutura do projeto

```
rastro_irado/
├── backend/
│   ├── workers/
│   │   └── collector.py     ← coleta unificada (BR + Tickmill)
│   ├── api/
│   │   └── main.py          ← FastAPI (porta 8888)
│   ├── irai/
│   │   └── engine.py        ← motor de cálculo IRAI
│   └── db.py                ← conexão SQLite
├── frontend/
│   └── src/App.jsx          ← React dashboard
├── data/
│   ├── irai.db              ← SQLite (gitignored)
│   └── reports/             ← relatórios de calibração
└── scripts/
    └── calibrate_m5.py      ← calibração offline
```

---

## Roadmap

### V1 (atual) ✅

- [x] Collector MT5 sequencial (2 terminais)
- [x] FastAPI + SQLite com WAL
- [x] Engine de cálculo IRAI (z-score + OLS + logística)
- [x] Calibração automatizada com relatório
- [x] Dashboard React com gráficos Recharts
- [x] Velocímetro P(↑) com sinal COMPRA/VENDA/NEUTRO
- [x] Fluxo Delta (book pressure)
- [x] 6 fatores otimizados (brute-force 64 combos → 71% acc)
- [x] Validação ao vivo (operacional desde 2026-04-23)

### V2

- [ ] WebSocket push em vez de polling
- [ ] Alertas desktop / som ao cruzar thresholds
- [ ] Integração com Regime Supervisor (ajuste de exposição por regime IRAI)
- [ ] Backtester de estratégias baseadas em P_up thresholds
- [ ] Walk-forward validation automática na calibração

### V3

- [ ] Dashboard mobile (PWA)
- [ ] Multi-target (WDO, small caps, BRL)
- [ ] Ensemble com features de microestrutura (book, trades)

---

## Licença & notas

Projeto pessoal, uso interno. **IRAI é ferramenta de suporte à decisão, não recomendação de investimento.**

**Autor:** Miqueias
**Início:** 2026-04-23
**Última atualização:** 2026-04-24
