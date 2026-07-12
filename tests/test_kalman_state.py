"""Spec da persistência do estado do Kalman (backend/db.py).

Ref: docs/plans/2026-07-10-frontend-migration-status-and-forward-plan.md — Fase 4.

REGRESSÃO. O estado do Kalman é o prior causal da sessão seguinte: o engine só o
adota quando `state_ts < início da sessão` (engine.py:614). Isso protege contra
compounding em polls repetidos do MESMO dia, mas não protegia contra o caminho
inverso: computar uma sessão ANTIGA em v2 (date-picker do frontend, ou o fallback
do /api/irai/current) gravava o estado daquela sessão POR CIMA do estado vivo
(`INSERT OR REPLACE`, sem guard). No dia seguinte esse estado velho passava no
teste `state_ts < session_start` e virava prior — betas de outro regime.

A invariante que estes testes travam: **a persistência é monotônica no tempo.**
Um write com timestamp mais antigo que o já gravado é IGNORADO; um mais novo (ou
o primeiro de todos) é aceito.

Roda sem pytest:  python3 tests/test_kalman_state.py
Ou com pytest:    pytest tests/test_kalman_state.py
"""
import os
import sys
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db import SCHEMA, save_kalman_state, load_kalman_state

LIVE_TS = "2026-07-10T18:00:00+00:00"      # último bar da sessão viva
OLD_TS = "2026-07-01T18:00:00+00:00"       # replay de uma sessão antiga
NEXT_TS = "2026-07-13T18:00:00+00:00"      # sessão seguinte

LIVE_MEAN = [0.5, 1.5, -2.5]
OLD_MEAN = [9.9, 9.9, 9.9]
NEXT_MEAN = [0.1, 0.2, 0.3]
COV = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def _conn():
    """Banco temporário só com o schema — não toca o irai.db real."""
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def test_primeiro_write_grava():
    c = _conn()
    save_kalman_state(c, "win", LIVE_MEAN, COV, 0.04, True, LIVE_TS)
    st = load_kalman_state(c, "win")
    assert st is not None, "primeiro estado deve ser gravado"
    assert st["timestamp_utc"] == LIVE_TS
    assert list(st["state_mean"]) == LIVE_MEAN


def test_replay_historico_nao_sobrescreve_estado_vivo():
    """O BUG: computar uma sessão antiga em v2 apagava o estado vivo."""
    c = _conn()
    save_kalman_state(c, "win", LIVE_MEAN, COV, 0.04, True, LIVE_TS)
    # replay de 01/07 tenta gravar por cima do estado de 10/07
    save_kalman_state(c, "win", OLD_MEAN, COV, 0.90, False, OLD_TS)

    st = load_kalman_state(c, "win")
    assert st["timestamp_utc"] == LIVE_TS, (
        f"estado vivo foi sobrescrito por replay histórico "
        f"({st['timestamp_utc']} deveria ser {LIVE_TS})"
    )
    assert list(st["state_mean"]) == LIVE_MEAN, "betas do regime antigo vazaram p/ o estado vivo"
    assert st["is_cointegrated"] == 1, "flag de cointegração do replay vazou"


def test_sessao_seguinte_avanca_o_estado():
    """O guard não pode travar o avanço legítimo (senão o v2 congela)."""
    c = _conn()
    save_kalman_state(c, "win", LIVE_MEAN, COV, 0.04, True, LIVE_TS)
    save_kalman_state(c, "win", NEXT_MEAN, COV, 0.02, True, NEXT_TS)

    st = load_kalman_state(c, "win")
    assert st["timestamp_utc"] == NEXT_TS, "estado mais novo deve substituir o anterior"
    assert list(st["state_mean"]) == NEXT_MEAN


def test_mesmo_timestamp_atualiza_o_estado():
    """Barra EM FORMAÇÃO: o collector reescreve o mesmo bar (mesmo timestamp) com
    um close novo a cada ciclo, então o estado recomputado precisa acompanhar.
    É por isso que o guard é `>=` e não `>` — mas sem duplicar linha."""
    c = _conn()
    save_kalman_state(c, "win", LIVE_MEAN, COV, 0.04, True, LIVE_TS)
    save_kalman_state(c, "win", NEXT_MEAN, COV, 0.01, True, LIVE_TS)  # mesmo ts, close novo

    st = load_kalman_state(c, "win")
    assert st["timestamp_utc"] == LIVE_TS
    assert list(st["state_mean"]) == NEXT_MEAN, "estado da barra em formação deve ser atualizado"
    n = c.execute("SELECT COUNT(*) FROM kalman_state WHERE slug='win'").fetchone()[0]
    assert n == 1, "slug é PRIMARY KEY — não pode duplicar linha"


def test_slugs_sao_independentes():
    """O guard é por slug: o replay do WDO não pode travar o write do WIN."""
    c = _conn()
    save_kalman_state(c, "win", LIVE_MEAN, COV, 0.04, True, LIVE_TS)
    save_kalman_state(c, "wdo", OLD_MEAN, COV, 0.10, True, OLD_TS)

    assert load_kalman_state(c, "win")["timestamp_utc"] == LIVE_TS
    assert load_kalman_state(c, "wdo")["timestamp_utc"] == OLD_TS


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL {name}: {e}")
    print("todos passaram" if not fails else f"{fails} falha(s)")
    sys.exit(1 if fails else 0)
