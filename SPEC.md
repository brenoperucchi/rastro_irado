# SPEC — IRAI (Intraday Risk Appetite Index)

**Versão:** 1.0 (Produção)
**Status:** Operacional
**Complementa:** PRD.md
**Última atualização:** 2026-04-24

---

## 1. Arquitetura

### 1.1 Visão geral

```
┌────────────────────────┐         ┌────────────────────────┐
│  MT5 Terminal BR (XP)  │         │  MT5 Terminal Tickmill  │
│  símbolos: WIN$N,      │         │  símbolos: DXY, BRENT,  │
│           DOL$N, DI1$N│         │  CHINA50, USDMXN        │
└───────────┬────────────┘         └───────────┬────────────┘
            │                                  │
            │   mt5.initialize(path=...)       │ (sequencial)
            │                                  │
            └──────────────┬───────────────────┘
                           ▼
              ┌────────────────────┐
              │  collector.py          │
              │  loop 30s, sequencial  │
              │  (BR → Tickmill)       │
              └──────────┬─────────┘
                         │
                         │  INSERT OR IGNORE INTO market_bars
                         ▼
                ┌─────────────────┐
                │  SQLite         │
                │  irai.db        │
                │  WAL mode       │
                └────────┬────────┘
                         │
                         │ SELECT (sob demanda)
                         ▼
                ┌─────────────────┐
                │  FastAPI        │
                │  main.py        │
                │  port 8888      │
                └────────┬────────┘
                         │
                         │ REST (polling 30s)
                         ▼
                ┌─────────────────┐
                │  React Dashboard│
                │  Vite port 5175 │
                └─────────────────┘

                ┌─────────────────┐
                │ calibrate_m5.py │──── roda offline, semanal
                │ atualiza        │
                │ model_params    │
                └─────────────────┘
```

### 1.2 Princípios de design

1. **Isolamento de falha por broker.** Cada MT5 em um processo worker independente. Se o broker internacional cai, o worker BR continua gravando barras de IBOV/WIN; o cálculo do IRAI segue com marcação de *stale*.
2. **Idempotência na ingestão.** `INSERT OR IGNORE` com chave composta `(symbol, timestamp_utc)`. Rodar o worker duas vezes no mesmo minuto não duplica dados.
3. **Separação cálculo × coleta.** Workers só coletam. O cálculo do IRAI é feito no endpoint da API, sob demanda, lendo das tabelas. Evita recalcular tudo em cada barra.
4. **Estado zero entre processos.** Workers não se conhecem. Comunicação exclusivamente via banco. Facilita debug, restart e extensão.
5. **UTC everywhere internally.** Timezones só aparecem na apresentação ou em timestamps de log humanamente legíveis.

---

## 2. Componentes

### 2.1 `collector.py`

**Responsabilidade:** conectar sequencialmente aos 2 terminais MT5 (XP e Tickmill), coletar barras M5 dos 7 símbolos, gravar em SQLite.

**Símbolos:**
- **XP:** `WIN$N`, `DOL$N`, `DI1$N` (target + 2 fatores locais)
- **Tickmill:** `DXY`, `BRENT`, `CHINA50`, `USDMXN` (4 fatores internacionais)

**Ciclo de vida:**
1. No startup: `mt5.initialize(path=MT5_BR_PATH, login=..., password=..., server=...)`.
2. Registra job APScheduler: trigger cron `*/5 10-18 * * 1-5` (BRT), com offset `+3s` para garantir barra fechada.
3. A cada disparo:
   - Pega `mt5.copy_rates_from_pos(symbol, TIMEFRAME_M5, 0, 2)` — últimas 2 barras.
   - Converte timestamps pra UTC (MT5 retorna em server time, que tipicamente é EET; ajustar via offset conhecido).
   - `INSERT OR IGNORE` no SQLite com `source='br'`.
4. Se `mt5.initialize()` falha ou `copy_rates_from_pos` retorna `None`, loga erro e agenda reconexão com backoff 5s → 15s → 30s → 60s.

### 2.2 `worker_intl.py`

**Responsabilidade:** análoga ao `worker_br.py`, mas para símbolos internacionais.

**Símbolos (configuráveis, com fallbacks):**
- `SYMBOL_VIX_INTL` — ex: `VIX`, `VIX.cash`, `USVIX`.
- `SYMBOL_DXY_INTL` — ex: `DXY`, `USDX`, `DX.f`.
- `SYMBOL_EWZ_INTL` — ex: `EWZ`, `EWZ.us`.
- `SYMBOL_US10Y_INTL` — ex: `US10Y`, `USTNOTE10Y`. Fallback: usar `TLT` e inverter sinal do peso.

**Diferenças do worker BR:**
- Símbolos podem não ter cotação durante todo o pregão B3 (VIX só a partir de 10:30 BRT). Worker grava barras apenas quando disponíveis; a API lida com missing data.
- Broker internacional geralmente usa server time UTC puro — menos conversão.

### 2.3 `irai_api.py`

**Responsabilidade:** expor endpoints REST que leem do SQLite, calculam IRAI sob demanda, retornam JSON.

**Ciclo de vida:** servidor FastAPI Uvicorn stateless. Sem estado em memória exceto cache LRU de 60s no endpoint `/current` para evitar recomputar entre requests do frontend.

**Endpoints em §8.**

### 2.4 `calibrate.py`

**Responsabilidade:** script offline, executado manualmente ou via cron semanal, que lê dados diários históricos e atualiza pesos/α/σ.

**Entrada:** 60 dias de closes diários dos 5 símbolos (IBOV + 4 fatores). Origem: histórico do próprio banco SQLite (barras 5 min agregadas) OU export manual de CSVs do MT5.

**Saída:** atualização atômica da tabela `model_params` + geração de `calibration_report_YYYYMMDD.md` em `/reports`.

### 2.5 Dashboard React

**Já prototipado** em `irai-dashboard.jsx`. No MVP real, substituir o `generateDay()` simulado por chamadas `fetch('http://localhost:8000/api/v1/irai/session')` com polling de 30s.

---

## 3. Modelo de Dados

### 3.1 Schema SQLite

```sql
-- Barras brutas coletadas pelos workers
CREATE TABLE IF NOT EXISTS market_bars (
    symbol          TEXT NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('br', 'intl')),
    timestamp_utc   TEXT NOT NULL,    -- ISO 8601 em UTC
    open            REAL NOT NULL,
    high            REAL NOT NULL,
    low             REAL NOT NULL,
    close           REAL NOT NULL,
    volume          REAL,
    received_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (symbol, timestamp_utc)
);

CREATE INDEX IF NOT EXISTS idx_market_bars_time ON market_bars(timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_market_bars_symbol_time ON market_bars(symbol, timestamp_utc);

-- Cache de snapshots IRAI (opcional; pode ser recalculado sempre)
CREATE TABLE IF NOT EXISTS irai_snapshots (
    session_date    TEXT NOT NULL,          -- YYYY-MM-DD em BRT
    timestamp_utc   TEXT NOT NULL,
    bar_idx         INTEGER NOT NULL,       -- 0..95
    p_up            REAL NOT NULL,
    score           REAL NOT NULL,
    z_ewz           REAL, z_vix REAL, z_dxy REAL, z_us10y REAL,
    c_ewz           REAL, c_vix REAL, c_dxy REAL, c_us10y REAL,
    ibov_return     REAL,                   -- retorno % desde open
    stale_flags     TEXT,                   -- JSON array com fatores stale, ex '["vix"]'
    computed_at     TEXT NOT NULL,
    PRIMARY KEY (session_date, timestamp_utc)
);

-- Parâmetros do modelo (versionados)
CREATE TABLE IF NOT EXISTS model_params (
    param_name      TEXT NOT NULL,
    value           REAL NOT NULL,
    effective_from  TEXT NOT NULL,          -- usado pra pegar versão vigente em timestamp X
    PRIMARY KEY (param_name, effective_from)
);

-- Preços de referência (open B3) por sessão
CREATE TABLE IF NOT EXISTS session_opens (
    session_date    TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    open_price      REAL NOT NULL,
    open_timestamp  TEXT NOT NULL,          -- pode não ser 13:00 UTC exato se VIX
    PRIMARY KEY (session_date, symbol)
);
```

### 3.2 Parâmetros vigentes

Exemplo de estado inicial em `model_params`:

| param_name      | value   | effective_from       |
|-----------------|---------|----------------------|
| w_dol           | −0.414  | 2026-04-24T00:00:00Z |
| w_di            | −0.293  | 2026-04-24T00:00:00Z |
| w_china         | +0.141  | 2026-04-24T00:00:00Z |
| w_mxn           | −0.041  | 2026-04-24T00:00:00Z |
| w_dxy           | +0.029  | 2026-04-24T00:00:00Z |
| w_brent         | +0.019  | 2026-04-24T00:00:00Z |
| alpha           | 1.3065  | 2026-04-24T00:00:00Z |
| intercept       | 0.1580  | 2026-04-24T00:00:00Z |
| sigma_dol       | 0.00559 | 2026-04-24T00:00:00Z |
| sigma_di        | 0.00631 | 2026-04-24T00:00:00Z |
| sigma_dxy       | 0.00281 | 2026-04-24T00:00:00Z |
| sigma_brent     | 0.01767 | 2026-04-24T00:00:00Z |
| sigma_china     | 0.00311 | 2026-04-24T00:00:00Z |
| sigma_mxn       | 0.00373 | 2026-04-24T00:00:00Z |

Consulta pra pegar parâmetros vigentes em uma data:

```sql
SELECT param_name, value
FROM model_params
WHERE effective_from = (
    SELECT MAX(effective_from) FROM model_params mp2
    WHERE mp2.param_name = model_params.param_name
      AND mp2.effective_from <= :query_date
);
```

---

## 4. Fluxo de Dados

### 4.1 Fluxo intraday (evento típico: barra de 10:05 BRT)

```
10:05:00 BRT │ Barra M5 fecha na B3 e no MT5 INTL
10:05:03     │ APScheduler dispara worker_br.on_bar_close()
10:05:03     │ APScheduler dispara worker_intl.on_bar_close()
10:05:04     │ worker_br insere IBOV, WIN em market_bars
10:05:05     │ worker_intl insere EWZ, VIX, DXY, US10Y em market_bars
10:05:30     │ Frontend faz poll em GET /api/v1/irai/current
10:05:30     │ API calcula P_up(t=10:05), retorna JSON
10:05:30     │ Dashboard renderiza nova barra no gráfico
```

### 4.2 Reset diário

Não há "reset" destrutivo — o modelo é aditivo por sessão. O que muda é a referência:

```
09:59 BRT │ Sistema identifica próxima abertura B3 = hoje 10:00 BRT
10:00 BRT │ Primeira barra pode ainda não estar disponível
10:05 BRT │ Barra fechada 10:00-10:05 captura o open
10:05 BRT │ Job dedicado `capture_session_open` insere em session_opens:
           │   - IBOV: open da barra 10:00
           │   - EWZ: último close disponível (mercado americano abre 10:30)
           │   - VIX: último close disponível
           │   - DXY, US10Y: último close disponível
10:05 BRT │ A partir daqui, todo z_i(t) usa P_i(open) de session_opens
```

### 4.3 Cálculo do IRAI (pseudo-código)

```python
def compute_irai(session_date: date, timestamp_utc: datetime) -> IraiSnapshot:
    params = load_params(effective_at=timestamp_utc)
    opens = load_session_opens(session_date)
    current = load_current_prices(timestamp_utc)

    # bar fraction: 0.01 para primeira barra, 1.0 para última
    bar_idx = bars_since_open(timestamp_utc)
    t_fraction = (bar_idx + 1) / BARS_PER_SESSION  # 96

    z = {}
    stale = []
    for factor in ['ewz', 'vix', 'dxy', 'us10y']:
        if factor not in current or age_seconds(current[factor]) > 600:
            stale.append(factor)
            z[factor] = last_known_z.get(factor, 0.0)
            continue
        ret = (current[factor] - opens[factor]) / opens[factor]
        sigma_intraday = params[f'sigma_{factor}_daily'] * sqrt(t_fraction)
        z[factor] = ret / sigma_intraday

    contributions = {f: params[f'w_{f}'] * z[f] for f in z}
    score = sum(contributions.values())
    p_up = sigmoid(params['alpha'] * score) * 100

    ibov_return = (current['ibov'] - opens['ibov']) / opens['ibov']

    return IraiSnapshot(
        session_date=session_date,
        timestamp_utc=timestamp_utc,
        bar_idx=bar_idx,
        p_up=p_up,
        score=score,
        z=z,
        contributions=contributions,
        ibov_return=ibov_return,
        stale_flags=stale,
    )
```

---

## 5. Algoritmo IRAI

### 5.1 Formalização matemática

**Retorno intraday normalizado** (z-score) do fator i no instante t desde a abertura:

$$
z_i(t) = \frac{P_i(t) - P_i(\text{open})}{\sigma_i \cdot \sqrt{t/T}}
$$

Onde:
- $P_i(t)$ é o preço do fator i no instante t.
- $P_i(\text{open})$ é o preço de referência congelado em 10:00 BRT.
- $\sigma_i$ é a volatilidade diária histórica do fator (escala decimal; ex: 0.018 = 1.8% ao dia).
- $T$ é a duração total da sessão em barras (96 para 5 min × 8 horas).
- $t$ é o número de barras decorridas desde o open.

A raiz de $t/T$ aplica escalonamento por Brownian motion — a vol esperada em meio período é $\sigma \sqrt{1/2}$, não $\sigma/2$.

**Score composto:**

$$
S(t) = \sum_{i \in \{\text{EWZ, VIX, DXY, US10Y}\}} w_i \cdot z_i(t)
$$

Onde $w_i$ são os pesos estimados via regressão linear multivariada (§6).

**Probabilidade de alta:**

$$
P_{\text{up}}(t) = \sigma(\alpha \cdot S(t)) \cdot 100
$$

Onde $\sigma(x) = 1/(1+e^{-x})$ é a sigmoide logística, e $\alpha$ é um parâmetro de calibração que controla quão agressivamente o score é mapeado pra probabilidade.

### 5.2 Interpretação

- $P_{\text{up}}(t) = 50\%$ ⇔ $S(t) = 0$ ⇔ sem informação líquida dos fatores.
- $P_{\text{up}}(t) = 70\%$ ⇔ $S(t) \approx 0.70$ (com α=1.2) ⇔ combinação de fatores sugere probabilidade ~2x maior de alta vs. baixa.
- Banda 40–60% corresponde a $|S| < 0.34$ ≈ "ruído".

### 5.3 Tratamento de dados faltantes

- Se fator i tem preço atrasado > 10 min: z_i é congelado no último valor conhecido; flag `stale` ativada.
- Se fator i nunca teve cotação na sessão (ex: VIX no pré-open B3 antes das 10:30 BRT): z_i = 0 com flag `pre_market`.
- Se IBOV não tem cotação: cálculo continua (IRAI não depende do IBOV; ele é apenas alvo de validação visual).

---

## 6. Calibração

### 6.1 Estimação dos pesos $w_i$

Amostra: últimos 60 dias úteis. Variável Y: retorno diário IBOV (close-to-close). Variáveis X: retornos diários EWZ, VIX, DXY, US10Y.

```python
import statsmodels.api as sm

X = df[['ret_ewz', 'ret_vix', 'ret_dxy', 'ret_us10y']]
y = df['ret_ibov']
X_scaled = X / X.std()  # normalizar pra pesos comparáveis
y_scaled = y / y.std()

model = sm.OLS(y_scaled, sm.add_constant(X_scaled)).fit()
weights = model.params.drop('const').to_dict()
```

Os pesos resultantes são gravados em `model_params`. **Sanity check:** `w_ewz > 0`, `w_vix < 0`, `w_dxy < 0`. Se algum sinal vier invertido, NÃO aplicar — gerar alerta no relatório, manter pesos anteriores, investigar manualmente (provável regime shift ou data quality issue).

### 6.2 Estimação de $\sigma_i$

$$
\sigma_i = \text{std}(\text{ret}_i^{\text{diário}}) \text{ sobre janela de 60 dias}
$$

Armazenado em `model_params` como `sigma_<fator>_daily` em escala decimal.

### 6.3 Calibração de $\alpha$

Em dados intraday históricos (se disponíveis) ou diários (proxy):

1. Para cada dia t, calcular S(t) usando as barras do dia e os pesos.
2. Y = 1 se IBOV fechou acima do open, 0 caso contrário.
3. Regressão logística: `logit(Y) = α · S(t) + β`.
4. α estimado vai pra model_params. β geralmente ≈ 0 e é ignorado.

### 6.4 Validação

O `calibrate.py` deve gerar `calibration_report_YYYYMMDD.md` com:

- Pesos estimados vs. vigentes (diff).
- R² da regressão.
- p-valores individuais dos fatores.
- Reliability plot (bucket P_up × frequência observada) em dados out-of-sample.
- Alerta se qualquer peso trocou de sinal.

---

## 7. Conexão Dual MT5

### 7.1 Setup físico

Duas instâncias completas do MetaTrader 5, cada uma em diretório separado:

```
C:\Program Files\MetaTrader 5 BR\    ← instalação normal, broker nacional
C:\Program Files\MetaTrader 5 INTL\  ← segunda instalação (renomear pasta), broker offshore
```

Para instalar a segunda instância, baixar o instalador do broker INTL e mudar o diretório de destino durante o instalador.

### 7.2 Conexão Python

A biblioteca `MetaTrader5` aceita o argumento `path` em `initialize()` para apontar pro `terminal64.exe` específico. **Cada processo Python** pode ter uma conexão ativa. Portanto:

```python
# worker_br.py
import MetaTrader5 as mt5
mt5.initialize(
    path=r"C:\Program Files\MetaTrader 5 BR\terminal64.exe",
    login=int(os.environ['MT5_BR_LOGIN']),
    password=os.environ['MT5_BR_PASSWORD'],
    server=os.environ['MT5_BR_SERVER'],
)
```

E em um **processo separado**:

```python
# worker_intl.py
import MetaTrader5 as mt5
mt5.initialize(
    path=r"C:\Program Files\MetaTrader 5 INTL\terminal64.exe",
    login=int(os.environ['MT5_INTL_LOGIN']),
    password=os.environ['MT5_INTL_PASSWORD'],
    server=os.environ['MT5_INTL_SERVER'],
)
```

**Importante:** rodar ambos no mesmo processo Python vai falhar silenciosamente — a segunda `initialize` derruba a primeira. É por isso que precisam ser processos independentes.

### 7.3 Server time

Cada broker tem seu server time:

| Broker BR típico | Server time = UTC−3 (BRT) |
| Broker INTL típico | Server time = UTC+2/+3 (EET) |

Os `datetime` retornados por `copy_rates_from_pos` estão em server time. Conversão obrigatória:

```python
from datetime import datetime, timezone, timedelta

BR_SERVER_OFFSET_HOURS = -3   # ajustar por broker
INTL_SERVER_OFFSET_HOURS = 3  # ajustar por broker

def to_utc(naive_dt: datetime, source: str) -> datetime:
    offset = BR_SERVER_OFFSET_HOURS if source == 'br' else INTL_SERVER_OFFSET_HOURS
    return (naive_dt - timedelta(hours=offset)).replace(tzinfo=timezone.utc)
```

O offset de cada broker é configurável via `.env` e deve ser validado manualmente comparando `mt5.symbol_info_tick(symbol).time` com `time.time()` no primeiro setup.

---

## 8. API Contract

Base URL: `http://localhost:8000`

### 8.1 `GET /api/v1/health`

Status das conexões e últimas barras recebidas.

```json
{
  "status": "ok",
  "mt5_br": {"connected": true, "last_bar_utc": "2026-04-23T17:00:00Z"},
  "mt5_intl": {"connected": true, "last_bar_utc": "2026-04-23T17:00:00Z"},
  "last_irai_compute": "2026-04-23T17:00:05Z"
}
```

### 8.2 `GET /api/v1/irai/current`

Snapshot mais recente.

```json
{
  "session_date": "2026-04-23",
  "timestamp_utc": "2026-04-23T17:00:00Z",
  "bar_idx": 84,
  "p_up": 67.3,
  "score": 0.587,
  "regime": "risk_on",
  "z_scores": {"ewz": 1.42, "vix": -0.88, "dxy": -0.31, "us10y": 0.15},
  "contributions": {"ewz": 0.639, "vix": 0.264, "dxy": 0.078, "us10y": 0.015},
  "ibov_return": 0.0112,
  "stale_flags": []
}
```

### 8.3 `GET /api/v1/irai/session?date=YYYY-MM-DD`

Série completa da sessão. Sem parâmetro, assume hoje.

```json
{
  "session_date": "2026-04-23",
  "bars": [
    {"bar_idx": 0, "timestamp_utc": "...", "p_up": 51.2, "score": 0.04, "z_scores": {...}, "contributions": {...}, "ibov_return": 0.0003},
    ...
  ]
}
```

### 8.4 `GET /api/v1/params`

Parâmetros vigentes e histórico.

```json
{
  "current": {"w_ewz": 0.45, "w_vix": -0.30, ..., "effective_from": "2026-04-20"},
  "previous_calibrations": [
    {"effective_from": "2026-04-13", "w_ewz": 0.42, ...}
  ]
}
```

### 8.5 `GET /api/v1/bars?symbol=EWZ&from=...&to=...`

Debug endpoint — barras brutas.

### 8.6 Padrões de erro

```json
{"error": "no_session_data", "detail": "Pregão ainda não abriu ou sessão vazia", "status": 404}
```

---

## 9. Scheduler & Reset Diário

### 9.1 Agenda dos workers

```python
# APScheduler cron
trigger = CronTrigger(
    day_of_week='mon-fri',
    hour='10-17',
    minute='0,5,10,15,20,25,30,35,40,45,50,55',
    second=3,
    timezone='America/Sao_Paulo',
)
```

A barra que fecha às 10:00 BRT é capturada no disparo de 10:05:03 (lê barra já fechada). A última barra da sessão fecha 17:55 BRT e é capturada 18:00:03.

### 9.2 Job de captura do session open

Roda uma vez por dia às 10:05:30 BRT:

```python
def capture_session_open():
    today = date.today()
    for symbol in ALL_SYMBOLS:
        first_bar = get_first_bar_of_session(symbol, today)
        if first_bar is None:
            # Fator estrangeiro sem cotação ainda — usar último close
            first_bar = get_last_close_before(symbol, today_open_utc)
        upsert_session_open(today, symbol, first_bar.open, first_bar.timestamp)
```

### 9.3 Horário de verão

B3 não faz mais horário de verão desde 2019. Fuso BRT = UTC−3 ano inteiro. O broker internacional pode fazer DST — isso afeta a conversão server time → UTC. O offset de cada server deve ser revalidado quando ocorrer mudança de DST nos EUA/Europa (março e novembro).

---

## 10. Error Handling & Resiliência

| Cenário                             | Resposta                                                                 |
|-------------------------------------|--------------------------------------------------------------------------|
| MT5 não conecta no startup          | Worker loga erro fatal, sai com exit code 1; process supervisor reinicia |
| MT5 desconecta no meio do dia       | `mt5.last_error()` logado; reconexão com backoff 5s→15s→30s→60s          |
| `copy_rates_from_pos` retorna None  | Log warning, skip, próxima barra tenta de novo                           |
| Preço anômalo (spike > 10σ)         | Log warning, inserir mesmo assim, flag `anomaly` no snapshot             |
| SQLite locked                       | WAL mode resolve em MVP; se persistir, retry com jitter                  |
| `model_params` vazio                | API retorna 503 "model_not_calibrated"; rodar `calibrate.py` seed        |
| Session opens faltantes             | API retorna P_up = 50% com flag `no_open_reference`                      |

---

## 11. Estrutura de Arquivos

```
irai/
├── README.md
├── PRD.md
├── SPEC.md
├── .env.example
├── pyproject.toml
├── requirements.txt
│
├── backend/
│   ├── __init__.py
│   ├── config.py              # leitura .env, constantes (BARS_PER_SESSION etc.)
│   ├── db.py                  # conexão SQLite + migrations
│   ├── schemas.py             # Pydantic models
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── worker_br.py
│   │   ├── worker_intl.py
│   │   └── common.py          # helpers compartilhados (to_utc, retry, etc.)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py            # FastAPI app
│   │   ├── routes_irai.py
│   │   ├── routes_health.py
│   │   └── routes_params.py
│   ├── irai/
│   │   ├── __init__.py
│   │   ├── compute.py         # função compute_irai()
│   │   ├── params.py          # load_params()
│   │   └── session.py         # helpers de open B3, reset, etc.
│   └── calibrate.py
│
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── IRAIDashboard.tsx  # adaptado do protótipo
│       └── api.ts             # fetch wrappers
│
├── data/
│   ├── irai.db                # SQLite (gitignored)
│   └── reports/
│       └── calibration_*.md   # gerados por calibrate.py
│
└── scripts/
    ├── list_symbols.py        # helper pra descobrir símbolos disponíveis
    ├── seed_params.py         # popula model_params com defaults
    └── validate_offsets.py    # valida server time offsets
```

---

## 12. Stack Técnico

| Camada      | Tecnologia            | Versão alvo | Justificativa                              |
|-------------|-----------------------|-------------|--------------------------------------------|
| MT5 client  | MetaTrader5 (pip)     | 5.0.45+     | Oficial, estável, suporta dual via path    |
| Scheduler   | APScheduler           | 3.10+       | Cron robusto, tolerante a restart          |
| DB          | SQLite                | 3.40+       | Zero setup, WAL suficiente pra MVP         |
| API         | FastAPI + Uvicorn     | 0.110+      | Stack padrão do Miqueias                   |
| Frontend    | React + Vite          | 18 / 5      | Consistente com dashboards anteriores      |
| Chart       | Recharts              | 2.12+       | Já usado no protótipo                      |
| Python      | CPython               | 3.11+       | MetaTrader5 lib suporta até 3.12           |

---

## 13. Testes

### 13.1 Unitários

- `test_compute_irai.py` — casos sintéticos: todos z_i=0 ⇒ P_up=50%; z_ewz=1, resto=0 ⇒ P_up ≈ 64% (com α=1.2, w_ewz=0.45).
- `test_time_conversion.py` — converte timestamps de ambos os brokers pra UTC.
- `test_session_open.py` — validação de reset diário e tratamento de fatores sem cotação.

### 13.2 Integração

- `test_full_session_replay.py` — alimenta banco com uma sessão histórica (CSV), roda API, compara série retornada vs. esperada.
- `test_dual_worker_isolation.py` — simula falha de um MT5, verifica que o outro continua e que o cálculo marca stale.

### 13.3 Manual

- Checklist de setup de cada broker (ver README §Troubleshooting).
- Validação visual do dashboard contra TradingView em paralelo por 5 pregões.

---

## 14. Deploy

### 14.1 MVP (desktop local)

- Máquina Windows pessoal com os dois terminais MT5.
- `backend/` e `frontend/` rodam em terminais separados via `make start-backend` / `make start-frontend`.
- Ou via supervisor como [nssm](https://nssm.cc/) pra rodar os workers como serviços Windows.

### 14.2 V2 (VPS Windows)

- VPS Windows (Contabo ou Hetzner, ~$15/mês) com os dois terminais instalados.
- Acesso ao dashboard via túnel Tailscale / ngrok.
- Backup automático do SQLite pra S3 diariamente.

---

## 15. Anexos

### 15.1 Valores típicos σ_i (referência inicial)

| Fator  | σ diária aprox. | Observação                              |
|--------|-----------------|-----------------------------------------|
| EWZ    | 1.8%            | ETF Brasil, vol alta                    |
| VIX    | 5.0%            | vol de vol, movimentos grandes normais  |
| DXY    | 0.4%            | índice dólar, movimentos pequenos       |
| US10Y  | 2.0%            | yield, vol em pontos-base significativa |
| IBOV   | 1.2%            | só para referência                      |

Estes são valores *seed* — o primeiro `calibrate.py` substituirá por estimativas empíricas.

### 15.2 Glossário

- **IRAI:** Intraday Risk Appetite Index (este projeto).
- **z-score normalizado:** retorno dividido pela vol esperada até o instante t; unidades em desvios-padrão.
- **Score S(t):** combinação linear ponderada dos z-scores.
- **Sigmoide:** função que mapeia ℝ → (0,1); usada para converter score em probabilidade.
- **Stale:** fator cujo último preço tem mais de 10 min de idade; indica dado potencialmente desatualizado.
- **Session open:** preço congelado às 10:00 BRT usado como denominador de todos os z-scores do dia.
