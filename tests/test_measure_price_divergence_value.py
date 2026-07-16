"""Spec do backtester NF-01 item 2 — Divergência macro-preço (marker Z)
isolada.

Ref: scripts/measure_price_divergence_value.py — a metodologia de extração/
medição (entrada na barra seguinte, cooldown, MFE/MAE clampado, burn-in,
bootstrap, Kalman encadeado) é REUSADA inteira de
scripts/measure_pair_signal_value.py e já tem cobertura própria em
tests/test_measure_pair_signal_value.py. Este arquivo testa só o que é
genuinamente novo aqui: a detecção de direção via z_compra_val/z_venda_val
(`_divergence_direction`) e que `run()` de fato usa esse callback e a lista
LIMITATIONS deste módulo, não os defaults do Pair Signal.

Roda sem pytest:  python3 tests/test_measure_price_divergence_value.py
Ou com pytest:    pytest tests/test_measure_price_divergence_value.py
"""
import os
import sys
import types
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pykalman  # noqa: F401
except ModuleNotFoundError:
    stub = types.ModuleType("pykalman")
    stub.KalmanFilter = object
    sys.modules["pykalman"] = stub
try:
    import statsmodels  # noqa: F401
except ModuleNotFoundError:
    for _sub in ("statsmodels", "statsmodels.tsa", "statsmodels.tsa.vector_ar",
                 "statsmodels.tsa.vector_ar.vecm"):
        sys.modules[_sub] = types.ModuleType(_sub)
    sys.modules["statsmodels.tsa.vector_ar.vecm"].coint_johansen = lambda *a, **k: None

from backend.irai.engine import IRAISnapshot

import scripts.measure_pair_signal_value as psv
from scripts.measure_price_divergence_value import (
    LIMITATIONS,
    _divergence_direction,
)


def _snap(i, close, *, z_compra_val=None, z_venda_val=None, pair_factor=None, ts_hour=10):
    s = IRAISnapshot(
        timestamp=f"2026-07-10T{ts_hour:02d}:{(i * 5) % 60:02d}:00+00:00",
        session_date="2026-07-10", bar_idx=i, t_frac=1.0, p_up=50.0,
        score=0.0, verdict="", verdict_color="",
    )
    s.win_current = close
    s.z_compra_val = z_compra_val
    s.z_venda_val = z_venda_val
    s.pair_factor = pair_factor
    return s


# ── 1. _divergence_direction ────────────────────────────────────────────

def test_direction_compra_quando_z_compra_val_presente():
    snap = _snap(0, 100.0, z_compra_val=100.0)
    assert _divergence_direction(snap) == "buy"


def test_direction_venda_quando_z_venda_val_presente():
    snap = _snap(0, 100.0, z_venda_val=100.0)
    assert _divergence_direction(snap) == "sell"


def test_direction_none_sem_marker():
    snap = _snap(0, 100.0)
    assert _divergence_direction(snap) is None


def test_direction_ignora_pair_compra_pair_venda():
    """Um snapshot com o marker do OUTRO signal (Pair) ativo mas sem marker
    Z não deve ser confundido — prova que _divergence_direction olha só
    z_compra_val/z_venda_val, não pair_compra/pair_venda."""
    snap = _snap(0, 100.0)
    snap.pair_compra = 100.0  # marker do OUTRO signal, não deve importar aqui
    assert _divergence_direction(snap) is None


# ── 2. extract_trade_outcomes com direction_of=_divergence_direction ───────

def test_extrai_evento_da_transicao_z_nao_da_transicao_pair():
    """Uma sessão com marker Pair ativo na barra 0 mas SEM marker Z não deve
    gerar evento quando extraída com direction_of=_divergence_direction; o
    inverso (só Z, sem Pair) deve gerar exatamente 1 evento."""
    only_pair = [_snap(0, 100.0)] + [_snap(i, 100.0 + i * 5.0) for i in range(1, 25)]
    only_pair[0].pair_compra = 100.0
    outcomes = psv.extract_trade_outcomes(
        "2026-07-10", "WIN$N", only_pair, is_b3=True, direction_of=_divergence_direction)
    assert outcomes == []

    only_z = [_snap(0, 100.0, z_compra_val=100.0)] + [
        _snap(i, 100.0 + i * 5.0) for i in range(1, 25)
    ]
    outcomes = psv.extract_trade_outcomes(
        "2026-07-10", "WIN$N", only_z, is_b3=True, direction_of=_divergence_direction)
    assert len(outcomes) == 1
    assert outcomes[0].direction == "buy"
    assert outcomes[0].entry_price == 105.0  # barra seguinte à do sinal, mesma metodologia


# ── 3. run() usa o direction_of e o LIMITATIONS deste módulo ───────────────

def test_run_do_pacote_z_usa_direction_of_e_limitations_proprios():
    dates = ["2026-07-10"]

    class _FakeCandidates:
        pass
    _FakeCandidates.dates = dates
    _FakeCandidates.discarded = []

    @contextmanager
    def fake_replay(db_path):
        def compute(date, target):
            return [_snap(0, 100.0, z_compra_val=100.0)] + [
                _snap(j, 100.0 + j * 5.0) for j in range(1, 25)
            ]
        yield compute

    with patch.object(psv, "candidate_sessions", lambda db, target, limit: _FakeCandidates), \
         patch.object(psv, "chronological_replay", fake_replay):
        report = psv.run(
            "unused.db", ["WIN$N"], limit=1, iterations=50, burn_in_sessions=0,
            direction_of=_divergence_direction, limitations=LIMITATIONS)

    t = report["targets"]["WIN$N"]
    assert t["by_direction"]["all"]["n_events"] == 1, (
        "run() precisa ter usado _divergence_direction — com o default "
        "(_pair_direction) este snapshot sintético (só z_compra_val, sem "
        "pair_compra/pair_venda) não geraria nenhum evento")
    assert report["limitations"] == LIMITATIONS, (
        "run() precisa reportar a lista LIMITATIONS deste módulo (C1-a "
        "versão forte), não o default de measure_pair_signal_value.py")


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
