# IRAI — Intraday Risk Appetite Index (V2 Multi-Asset)

Dashboard cross-asset em tempo real que estima a **probabilidade direcional intraday (alta/baixa)** de 13 ativos globais (Índices, Moedas, Commodities e Crypto), inferida a partir de uma regressão múltipla (Ridge) sobre fatores macroeconômicos independentes.

> *"Neste momento do pregão, o resto do mundo está dizendo que este ativo deveria estar subindo ou caindo?"*

Atualiza a cada 30 segundos. Reseta no open da Sessão. Opera com dois terminais MetaTrader 5 em paralelo — um nacional (XP) para WIN/DOL/DI, um internacional (Tickmill) para todo o resto do portfólio.

## Documentação relacionada

- [`PRD.md`](./PRD.md) — visão, objetivos, escopo, métricas de sucesso
- [`SPEC.md`](./SPEC.md) — arquitetura, schema, algoritmo, API contract

---

## Performance do Modelo (V2 - Ridge Regularization)

O novo motor Multi-Asset V2 calibra 13 ativos dinamicamente garantindo que não haja *overfitting* via Filtros de Correlação Cruzada e Penalidade L2 (Ridge).

**Cobertura de Acurácia Direcional (pós-calibração 2026-04-27, sem DXY nos majors):**
- **Moedas/Forex Major:** 80% a 91% (EURUSD 91.2%, USDCHF 90.0%, GBPUSD 88.8%, AUDUSD 86.4%, USDCAD 80.8%, USDJPY 80.4%)
- **Índices Americanos:** 77% a 84% (USTEC 83.9%, US500 82.3%, US30 77.5%)
- **Mercado BR:** 75% a 76% (WDO$N 76.0%, WIN$N 74.8%)
- **Crypto e Metais:** 73% (BTCUSD 73.1%, XAUUSD 73.5%)

Para a tabela completa de fatores de cada ativo, seus pesos normalizados ($w_i$), sigmas ($\sigma$) e acurácias individuais, consulte o documento dinâmico [`FACTOR_MAP.md`](./FACTOR_MAP.md).

---

## Arquitetura em 30 segundos (Cloud Híbrida)

```
MT5 Brasil (XP)     ─┐                                  ┌─► Firebase Realtime DB
                      ├─► collector.py ─► API :8888 ────┤       ▲
MT5 Tickmill        ─┘                          │       └─► firebase_sync.py
                                              SQLite            │
                                                                ▼
                                                        React Frontend (Vercel/Firebase)
```

- **Collector unificado:** coleta sequencial dos 2 terminais MT5 a cada ciclo (30s).
- **SQLite (WAL):** armazena o histórico cru e metadados.
- **FastAPI:** expõe endpoints com cálculos sob demanda (IRAI + Fatores).
- **Sincronizador (firebase_sync.py):** roda em background (NSSM) empurrando o estado atual pra nuvem a cada 30s.
- **Frontend (Firebase Hosting):** site passivo acessível globalmente via celular/desktop, lendo o JSON hospedado.

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

A infraestrutura foi configurada para rodar **100% invisível em background** usando o **NSSM** no Windows.

### Instalação dos Serviços:
Abra o PowerShell como Administrador e rode:
```powershell
.\scripts\install_nssm_services.ps1
```

Isso instalará 3 serviços automáticos:
1. `IRAI_API` (Uvicorn FastAPI na porta 8888)
2. `IRAI_Collector` (Sincroniza o MT5 pro SQLite a cada 60s)
3. `IRAI_FirebaseSync` (Lê a API local e empurra o payload pro Firebase a cada 30s)

### Frontend (Nuvem):
O React já está empacotado e hospedado publicamente no Firebase Hosting. Para acessar, abra `rastromacro.web.app` em qualquer dispositivo.

Para desenvolvimento local do frontend:
```bash
cd frontend && npm run dev
```

---

## Calibração (Motor V2)

Para recalibrar automaticamente toda a malha de ativos globais, execute:

```bash
python -X utf8 scripts/calibrate_universal.py --all --force
```

Para recalibrar apenas os pares de moedas major (EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, USD/CHF) **sem DXY** (exclusão obrigatória — multicolinearidade):
```bash
python calibrate_majors_nodxy.py
```

Isso dispara o processo completo:
1. Extrai retornos de sessões construídos a partir de blocos exatos M5 para cada ativo.
2. Aplica regras de exclusão: BR isolado de Internacional; índices US não usam DXY; **majors forex não usam DXY** (DXY é derivado dos próprios pares).
3. Executa brute-force em todos os alvos, buscando combinações (entre 4 e 8 fatores) com Score Misto: 70% Acurácia + 30% R².
4. Calibra logistic regression (α, intercept) sobre o score linear para mapear em probabilidade [0–100%].
5. Atualiza os metadados do SQLite (`asset_models` e `model_params`).

Para regenerar a documentação após a calibração:
```bash
python -X utf8 scripts/generate_factor_map.py
```

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

### V2 (atual) ✅

- [x] Expansão Multi-Asset (13 Alvos simultâneos)
- [x] Brute-force Calibrador V2 (`calibrate_v2.py`)
- [x] Regressão Ridge (Alpha Regularization) para prevenir Overfitting
- [x] Filtros Dinâmicos de Correlação Cross-Asset
- [x] Dashboard dinâmico exibindo Divergência de Preço (Z-Score)
- [x] Integração completa de visualização de Compra/Venda via CSS
- [x] **Arquitetura Cloud Híbrida**: Firebase Realtime DB + Hosting para acesso mobile.
- [x] **Deploy Invisível**: Instalação automatizada via NSSM Services (`install_nssm_services.ps1`).

### V3

- [ ] Integração com Regime Supervisor (ajuste de exposição por regime IRAI em MT5 portfólios)
- [ ] Ensemble com features de microestrutura (book, trades)
- [ ] WebSocket push em vez de polling
- [ ] Alertas desktop / som ao cruzar thresholds

---

## Licença & notas

Projeto pessoal, uso interno. **IRAI é ferramenta de suporte à decisão, não recomendação de investimento.**

**Autor:** Miqueias
**Início:** 2026-04-23
**Última atualização:** 2026-04-27


## Arquitetura Multi-Ativo (V2)
O sistema evoluiu para cobrir 13 ativos globais. Para entender a relação de fatores e pesos de cada modelo (WIN, WDO, S&P500, Forex, Cripto), consulte o [FACTOR_MAP.md](FACTOR_MAP.md).
