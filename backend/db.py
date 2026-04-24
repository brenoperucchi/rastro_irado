"""
IRAI Database — Schema e inicialização do SQLite.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "irai.db")

SCHEMA = """
-- Barras brutas coletadas pelos workers (M5 e D1)
CREATE TABLE IF NOT EXISTS market_bars (
    symbol          TEXT NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('br', 'tickmill')),
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


if __name__ == "__main__":
    init_db()
