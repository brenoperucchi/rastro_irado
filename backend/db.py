"""
IRAI Database — Schema e inicialização do SQLite.
"""
import sqlite3
import os
import json

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "irai.db")

SCHEMA = """
-- Barras brutas coletadas pelos workers (M5 e D1)
CREATE TABLE IF NOT EXISTS market_bars (
    symbol          TEXT NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('br', 'tickmill', 'axi')),
    timeframe       TEXT NOT NULL CHECK (timeframe IN ('M5', 'D1')),
    timestamp_utc   TEXT NOT NULL,    -- ISO 8601 em UTC
    open            REAL NOT NULL,
    high            REAL NOT NULL,
    low             REAL NOT NULL,
    close           REAL NOT NULL,
    volume          REAL,
    real_volume     REAL DEFAULT 0,
    delta           REAL DEFAULT 0,
    received_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (symbol, timeframe, timestamp_utc)
);

CREATE INDEX IF NOT EXISTS idx_bars_sym_tf_time ON market_bars(symbol, timeframe, timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_bars_time ON market_bars(timestamp_utc);

-- Cache de snapshots IRAI computados
CREATE TABLE IF NOT EXISTS irai_snapshots (
    session_date    TEXT NOT NULL,          -- YYYY-MM-DD em BRT
    timestamp_utc   TEXT NOT NULL,
    bar_idx         INTEGER NOT NULL,       -- 0..95
    p_up            REAL NOT NULL,
    score           REAL NOT NULL,
    z_dol           REAL, z_di REAL, z_vix REAL, z_dxy REAL,
    z_brent         REAL, z_us500 REAL, z_btcusd REAL,
    c_dol           REAL, c_di REAL, c_vix REAL, c_dxy REAL,
    c_brent         REAL, c_us500 REAL, c_btcusd REAL,
    win_return      REAL,                   -- retorno % WIN desde open
    stale_flags     TEXT,                   -- JSON array com fatores stale
    computed_at     TEXT NOT NULL,
    PRIMARY KEY (session_date, timestamp_utc)
);

-- Parâmetros do modelo (versionados)
CREATE TABLE IF NOT EXISTS model_params (
    param_name      TEXT NOT NULL,
    value           REAL NOT NULL,
    effective_from  TEXT NOT NULL,
    PRIMARY KEY (param_name, effective_from)
);

-- Preços de referência (open B3) por sessão
CREATE TABLE IF NOT EXISTS session_opens (
    session_date    TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    open_price      REAL NOT NULL,
    open_timestamp  TEXT NOT NULL,
    PRIMARY KEY (session_date, symbol)
);

-- Log de calibrações
CREATE TABLE IF NOT EXISTS calibration_log (
    calibration_date TEXT NOT NULL PRIMARY KEY,
    window_days      INTEGER NOT NULL,
    r_squared        REAL,
    params_json      TEXT NOT NULL,         -- JSON com todos os pesos
    notes            TEXT
);

-- Configuração de modelos por ativo
CREATE TABLE IF NOT EXISTS asset_models (
    target          TEXT PRIMARY KEY,          -- 'US500', 'XAUUSD', etc.
    slug            TEXT NOT NULL UNIQUE,      -- 'us500' (prefixo no model_params)
    display_name    TEXT NOT NULL,             -- 'S&P 500'
    icon            TEXT DEFAULT '📊',
    factors         TEXT NOT NULL,             -- JSON: ["DXY","VIX","BTCUSD"]
    factor_labels   TEXT NOT NULL,             -- JSON: {"DXY":"dxy","VIX":"vix"}
    session_start_h INTEGER DEFAULT 0,        -- hora UTC início sessão
    session_end_h   INTEGER DEFAULT 24,       -- hora UTC fim sessão (0-24 = 24h)
    data_proxy      TEXT,                     -- símbolo no banco se diferente (DOL$N para WDO$N)
    accuracy        REAL,                     -- última acurácia direcional calibrada
    r_squared       REAL,                     -- último R²
    oos_accuracy    REAL,                     -- acurácia em holdout temporal intocado
    oos_r2          REAL,                     -- R² em holdout temporal intocado
    n_sessions      INTEGER,                  -- sessões usadas na calibração
    calibrated_at   TEXT,                     -- timestamp última calibração
    active          INTEGER DEFAULT 1         -- 1=ativo, 0=inativo
);
-- Estado Dinâmico (Kalman Filter & Johansen)
CREATE TABLE IF NOT EXISTS kalman_state (
    slug             TEXT PRIMARY KEY,
    state_mean       TEXT NOT NULL,         -- JSON array
    state_covariance TEXT NOT NULL,         -- JSON 2D array
    johansen_p_value REAL,
    is_cointegrated  INTEGER DEFAULT 1,     -- 1=sim, 0=não
    factor_signature TEXT,                  -- cesta ordenada associada aos betas
    timestamp_utc    TEXT NOT NULL
);
"""

def get_connection(db_path=None):
    """Retorna conexão SQLite com WAL mode."""
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path=None):
    """Cria schema se não existir."""
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    print(f"Database initialized: {db_path or DB_PATH}")
    conn.close()


def migrate_delta(db_path=None):
    """Adiciona colunas real_volume e delta se não existirem (migração)."""
    conn = get_connection(db_path)
    cursor = conn.execute("PRAGMA table_info(market_bars)")
    cols = {row["name"] for row in cursor}

    if "real_volume" not in cols:
        conn.execute("ALTER TABLE market_bars ADD COLUMN real_volume REAL DEFAULT 0")
        print("  + coluna real_volume adicionada")
    if "delta" not in cols:
        conn.execute("ALTER TABLE market_bars ADD COLUMN delta REAL DEFAULT 0")
        print("  + coluna delta adicionada")

    conn.commit()
    conn.close()


def migrate_divergence_config(db_path=None):
    """Adiciona a coluna divergence_config em asset_models se não existir.

    engine.py, api/main.py e calc_sigmas.py leem/escrevem essa coluna, mas o
    SCHEMA original nunca a criou — sem esta migração, calc_sigmas.py falha
    com "no such column" em qualquer banco criado do zero.
    """
    conn = get_connection(db_path)
    cursor = conn.execute("PRAGMA table_info(asset_models)")
    cols = {row["name"] for row in cursor}

    if "divergence_config" not in cols:
        conn.execute("ALTER TABLE asset_models ADD COLUMN divergence_config TEXT")
        print("  + coluna divergence_config adicionada")

    conn.commit()
    conn.close()


def migrate_oos_metrics(db_path=None):
    """Adiciona métricas de holdout a ``asset_models`` de forma idempotente."""
    conn = get_connection(db_path)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(asset_models)")}

    if "oos_accuracy" not in cols:
        conn.execute("ALTER TABLE asset_models ADD COLUMN oos_accuracy REAL")
        print("  + coluna oos_accuracy adicionada")
    if "oos_r2" not in cols:
        conn.execute("ALTER TABLE asset_models ADD COLUMN oos_r2 REAL")
        print("  + coluna oos_r2 adicionada")

    conn.commit()
    conn.close()


def migrate_kalman_state(db_path=None):
    """Adiciona a tabela kalman_state se não existir (migração Fase 6)."""
    conn = get_connection(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kalman_state (
            slug             TEXT PRIMARY KEY,
            state_mean       TEXT NOT NULL,
            state_covariance TEXT NOT NULL,
            johansen_p_value REAL,
            is_cointegrated  INTEGER DEFAULT 1,
            factor_signature TEXT,
            timestamp_utc    TEXT NOT NULL
        )
    """)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(kalman_state)")}
    if "factor_signature" not in cols:
        conn.execute("ALTER TABLE kalman_state ADD COLUMN factor_signature TEXT")
        print("  + coluna factor_signature adicionada")
    conn.commit()
    print("  + tabela kalman_state garantida")
    conn.close()


def factor_signature(factors) -> str:
    """Serializa a cesta preservando a ordem posicional dos betas."""
    return json.dumps(list(factors), ensure_ascii=False, separators=(",", ":"))


def save_kalman_state(conn, slug: str, state_mean, state_covariance, p_value: float, is_cointegrated: bool, timestamp_utc: str, factor_signature_value: str = None):
    """Salva o estado atual do filtro de Kalman e teste de Johansen."""
    import numpy as np
    
    # Converter numpy arrays para listas para serialização JSON
    if isinstance(state_mean, np.ndarray):
        state_mean = state_mean.tolist()
    if isinstance(state_covariance, np.ndarray):
        state_covariance = state_covariance.tolist()
        
    # Upsert MONOTÔNICO: um write com timestamp mais antigo que o já gravado é
    # ignorado. O estado é o prior causal da sessão seguinte (engine.py:614) —
    # sem este guard, computar uma sessão antiga em v2 (date-picker do frontend,
    # fallback do /api/irai/current) gravava o estado daquela sessão por cima do
    # estado vivo, e no dia seguinte ele virava prior com betas de outro regime.
    # timestamp_utc é ISO-8601 com offset fixo (+00:00) → ordem lexicográfica ==
    # ordem cronológica. Ver tests/test_kalman_state.py.
    conn.execute("""
        INSERT INTO kalman_state
        (slug, state_mean, state_covariance, johansen_p_value, is_cointegrated, timestamp_utc, factor_signature)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            state_mean       = excluded.state_mean,
            state_covariance = excluded.state_covariance,
            johansen_p_value = excluded.johansen_p_value,
            is_cointegrated  = excluded.is_cointegrated,
            timestamp_utc    = excluded.timestamp_utc,
            factor_signature = excluded.factor_signature
        WHERE excluded.timestamp_utc >= kalman_state.timestamp_utc
    """, (slug, json.dumps(state_mean), json.dumps(state_covariance), p_value, int(is_cointegrated), timestamp_utc, factor_signature_value))
    conn.commit()


def load_kalman_state(conn, slug: str):
    """Carrega o último estado do filtro de Kalman para o ativo."""
    import numpy as np
    
    row = conn.execute(
        "SELECT * FROM kalman_state WHERE slug = ?", (slug,)
    ).fetchone()
    
    if not row:
        return None
        
    return {
        "state_mean": np.array(json.loads(row["state_mean"])),
        "state_covariance": np.array(json.loads(row["state_covariance"])),
        "johansen_p_value": row["johansen_p_value"],
        "is_cointegrated": bool(row["is_cointegrated"]),
        "timestamp_utc": row["timestamp_utc"],
        "factor_signature": (
            row["factor_signature"] if "factor_signature" in row.keys() else None
        ),
    }


if __name__ == "__main__":
    init_db()
    migrate_delta()
    migrate_kalman_state()
    migrate_divergence_config()
    migrate_oos_metrics()
