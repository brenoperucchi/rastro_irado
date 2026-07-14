"""Regressões da persistência explícita de métricas out-of-sample."""

import sqlite3
import importlib.util
import os
import re
import threading

import pytest

from backend import db


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "calibrate_universal_persistence",
    os.path.join(ROOT, "scripts", "calibrate_universal.py"),
)
calibrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calibrator)


def _run_db_main(db_path):
    source = os.path.join(ROOT, "backend", "db.py")
    code = open(source, encoding="utf-8").read()
    code = re.sub(
        r"^DB_PATH = .*$",
        f"DB_PATH = {str(db_path)!r}",
        code,
        count=1,
        flags=re.MULTILINE,
    )
    exec(compile(code, source, "exec"), {"__name__": "__main__", "__file__": source})


def test_fluxo_oficial_cria_oos_em_banco_novo_e_legado(tmp_path):
    clean_path = tmp_path / "clean.db"
    legacy_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(legacy_path)
    legacy.execute(
        "CREATE TABLE asset_models (target TEXT PRIMARY KEY, accuracy REAL, r_squared REAL)"
    )
    legacy.close()

    for db_path in (clean_path, legacy_path):
        _run_db_main(db_path)
        conn = sqlite3.connect(db_path)
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(asset_models)").fetchall()
        }
        conn.close()
        assert {"oos_accuracy", "oos_r2"} <= columns


def test_migracao_oos_e_idempotente(tmp_path):
    db_path = tmp_path / "irai.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE asset_models (target TEXT PRIMARY KEY, accuracy REAL, r_squared REAL)")
    conn.close()

    db.migrate_oos_metrics(str(db_path))
    db.migrate_oos_metrics(str(db_path))

    conn = sqlite3.connect(db_path)
    columns = {
        row[1]: row[2]
        for row in conn.execute("PRAGMA table_info(asset_models)").fetchall()
    }
    conn.close()
    assert columns["oos_accuracy"] == "REAL"
    assert columns["oos_r2"] == "REAL"


def test_migracao_kalman_adiciona_assinatura_em_tabela_legada(tmp_path):
    db_path = tmp_path / "irai.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE kalman_state (
            slug TEXT PRIMARY KEY,
            state_mean TEXT NOT NULL,
            state_covariance TEXT NOT NULL,
            johansen_p_value REAL,
            is_cointegrated INTEGER DEFAULT 1,
            timestamp_utc TEXT NOT NULL
        )"""
    )
    conn.close()

    db.migrate_kalman_state(str(db_path))

    conn = sqlite3.connect(db_path)
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(kalman_state)").fetchall()
    }
    conn.close()
    assert "factor_signature" in columns


def test_migradores_concorrentes_toleram_coluna_adicionada_pelo_rival(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "irai.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE kalman_state (
            slug TEXT PRIMARY KEY,
            state_mean TEXT NOT NULL,
            state_covariance TEXT NOT NULL,
            johansen_p_value REAL,
            is_cointegrated INTEGER DEFAULT 1,
            timestamp_utc TEXT NOT NULL
        )"""
    )
    conn.close()

    barrier = threading.Barrier(2)

    class RacingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args):
            cursor = self.connection.execute(sql, *args)
            if sql.strip().startswith("PRAGMA table_info(kalman_state)"):
                rows = cursor.fetchall()
                barrier.wait(timeout=5)
                return rows
            return cursor

        def __getattr__(self, name):
            return getattr(self.connection, name)

    connections = []
    for _ in range(2):
        connection = sqlite3.connect(db_path, check_same_thread=False)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.row_factory = sqlite3.Row
        connections.append(RacingConnection(connection))

    monkeypatch.setattr(db, "get_connection", lambda _path=None: connections.pop())
    errors = []

    def migrate():
        try:
            db.migrate_kalman_state(str(db_path))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=migrate) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []


def test_add_column_nao_esconde_outros_erros_operacionais():
    class LockedConnection:
        def execute(self, _sql):
            raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        db._add_column(LockedConnection(), "ALTER TABLE example ADD COLUMN value TEXT")


def test_init_db_migra_ate_head_e_permite_write_kalman_em_banco_legado(tmp_path):
    """O bootstrap novo precisa tornar um banco pré-bcab7a1 gravável."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE market_bars (
            symbol TEXT, source TEXT, timeframe TEXT, timestamp_utc TEXT
        );
        CREATE TABLE asset_models (
            target TEXT PRIMARY KEY, accuracy REAL, r_squared REAL
        );
        CREATE TABLE kalman_state (
            slug TEXT PRIMARY KEY,
            state_mean TEXT NOT NULL,
            state_covariance TEXT NOT NULL,
            johansen_p_value REAL,
            is_cointegrated INTEGER DEFAULT 1,
            timestamp_utc TEXT NOT NULL
        );
        """
    )
    conn.close()

    db.init_db(str(db_path))

    conn = db.get_connection(str(db_path))
    db.save_kalman_state(
        conn,
        "win",
        [0.1, 0.2],
        [[1.0, 0.0], [0.0, 1.0]],
        0.04,
        True,
        "2026-07-13T18:00:00+00:00",
        db.factor_signature(["WDO$N", "DI1$N"]),
    )
    saved = conn.execute(
        "SELECT factor_signature FROM kalman_state WHERE slug = 'win'"
    ).fetchone()[0]
    conn.close()

    assert saved == '["WDO$N","DI1$N"]'


def test_save_to_db_persiste_metricas_in_e_oos_separadas(tmp_path):
    db_path = tmp_path / "irai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(db.SCHEMA)
    conn.execute(
        """INSERT INTO asset_models
           (target, slug, display_name, factors, factor_labels)
           VALUES ('WIN$N', 'win', 'WIN', '[]', '{}')"""
    )
    result = {
        "factors": ["WDO$N"],
        "factor_labels": {"WDO$N": "wdo"},
        "weights": {"wdo": 0.5},
        "sigmas": {"wdo": 0.01},
        "alpha": 1.0,
        "intercept": 0.0,
        "accuracy": 75.0,
        "r2": 0.6,
        "oos_accuracy": 62.0,
        "oos_r2": 0.3,
        "n_sessions": 200,
    }

    calibrator.save_to_db(conn, "WIN$N", "win", result)

    row = conn.execute(
        "SELECT accuracy, r_squared, oos_accuracy, oos_r2 FROM asset_models"
    ).fetchone()
    conn.close()
    assert row == (75.0, 0.6, 62.0, 0.3)


def test_save_to_db_remove_estado_quando_cesta_muda(tmp_path):
    db_path = tmp_path / "irai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(db.SCHEMA)
    conn.execute(
        """INSERT INTO asset_models
           (target, slug, display_name, factors, factor_labels)
           VALUES ('WDO$N', 'wdo', 'WDO', '["FACTOR_A"]', '{}')"""
    )
    conn.execute(
        """INSERT INTO kalman_state
           (slug, state_mean, state_covariance, timestamp_utc)
           VALUES ('wdo', '[1, 2]', '[[1, 0], [0, 1]]', '2026-07-10T18:00:00Z')"""
    )
    result = {
        "factors": ["FACTOR_B"],
        "factor_labels": {"FACTOR_B": "factor_b"},
        "weights": {"factor_b": 0.5},
        "sigmas": {"factor_b": 0.01},
        "alpha": 1.0,
        "intercept": 0.0,
        "accuracy": 75.0,
        "r2": 0.6,
        "oos_accuracy": 62.0,
        "oos_r2": 0.3,
        "n_sessions": 200,
    }

    calibrator.save_to_db(conn, "WDO$N", "wdo", result)

    assert conn.execute(
        "SELECT 1 FROM kalman_state WHERE slug = 'wdo'"
    ).fetchone() is None
    conn.close()
