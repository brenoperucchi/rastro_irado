"""Contrato da identidade do motor que alimenta o ledger P Dinâmico."""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.irai.runtime_revision import (
    build_engine_revision,
    prediction_revision_fingerprint,
)
from backend.irai import runtime_revision


def _seed_model_config(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE asset_models (
                target TEXT PRIMARY KEY,
                slug TEXT NOT NULL,
                factors TEXT NOT NULL,
                factor_labels TEXT NOT NULL,
                session_start_h INTEGER,
                session_end_h INTEGER,
                data_proxy TEXT,
                divergence_config TEXT,
                active INTEGER NOT NULL
            );
            CREATE TABLE model_params (
                param_name TEXT NOT NULL,
                value REAL NOT NULL,
                effective_from TEXT NOT NULL,
                PRIMARY KEY (param_name, effective_from)
            );
            """
        )
        conn.execute(
            "INSERT INTO asset_models VALUES (?,?,?,?,?,?,?,?,?)",
            ("WIN$N", "win", '["WDO$N"]', '{}', 9, 18, None, '{}', 1),
        )
        conn.execute(
            "INSERT INTO model_params VALUES (?,?,?)",
            ("win_alpha", 1.0, "2026-07-01T00:00:00Z"),
        )


def test_revisao_muda_quando_calibracao_ativa_muda(tmp_path):
    database = tmp_path / "irai.db"
    _seed_model_config(database)

    before = build_engine_revision(db_path=database)
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE model_params SET value = 1.5 WHERE param_name = 'win_alpha'"
        )
    after = build_engine_revision(db_path=database)

    assert before["model_config_sha256"] != after["model_config_sha256"]
    assert prediction_revision_fingerprint(before) != prediction_revision_fingerprint(after)


def test_fingerprint_semantico_ignora_commit_mas_preserva_auditoria(tmp_path):
    database = tmp_path / "irai.db"
    _seed_model_config(database)

    first = build_engine_revision(db_path=database)
    restarted_after_commit = {**first, "git_commit": "f" * 40}

    assert first["git_commit"] != restarted_after_commit["git_commit"]
    assert prediction_revision_fingerprint(first) == prediction_revision_fingerprint(
        restarted_after_commit
    )


def test_revisao_do_win_ignora_recalibracao_de_outro_ativo(tmp_path):
    database = tmp_path / "irai.db"
    _seed_model_config(database)
    with sqlite3.connect(database) as conn:
        conn.execute(
            "INSERT INTO asset_models VALUES (?,?,?,?,?,?,?,?,?)",
            ("US500", "us500", '[]', '{}', 0, 24, None, '{}', 1),
        )
        conn.execute(
            "INSERT INTO model_params VALUES (?,?,?)",
            ("us500_alpha", 1.0, "2026-07-01T00:00:00Z"),
        )
    before = build_engine_revision(db_path=database)
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE model_params SET value = 1.5 WHERE param_name = 'us500_alpha'"
        )
    after = build_engine_revision(db_path=database)

    assert before["model_config_sha256"] == after["model_config_sha256"]


def test_revisao_do_win_ignora_configuracao_que_nao_altera_p_up(tmp_path):
    database = tmp_path / "irai.db"
    _seed_model_config(database)
    before = build_engine_revision(db_path=database)
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE asset_models SET divergence_config = ? WHERE target = 'WIN$N'",
            ('{"use_johansen": false}',),
        )
        conn.execute(
            "INSERT INTO model_params VALUES (?,?,?)",
            ("win_johansen_lookback", 100.0, "2026-07-01T00:00:00Z"),
        )
    after = build_engine_revision(db_path=database)

    assert before["model_config_sha256"] == after["model_config_sha256"]


def test_revisao_falha_fechado_sem_configuracao_ativa_do_win(tmp_path):
    database = tmp_path / "irai.db"
    _seed_model_config(database)
    with sqlite3.connect(database) as conn:
        conn.execute("DELETE FROM asset_models WHERE target = 'WIN$N'")

    with pytest.raises(RuntimeError, match="configuração ativa ausente"):
        build_engine_revision(db_path=database)


def test_revisao_retenta_leitura_transitoria_da_configuracao(tmp_path, monkeypatch):
    database = tmp_path / "irai.db"
    _seed_model_config(database)
    original_connect = runtime_revision.sqlite3.connect
    calls = 0

    def transient_connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("disk I/O error")
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(runtime_revision.sqlite3, "connect", transient_connect)
    monkeypatch.setattr(runtime_revision.time, "sleep", lambda _seconds: None)

    revision = build_engine_revision(db_path=database)

    assert calls == 2
    assert len(revision["model_config_sha256"]) == 64
