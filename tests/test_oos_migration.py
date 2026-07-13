"""Regressões da persistência explícita de métricas out-of-sample."""

import sqlite3
import importlib.util
import os
import re

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
