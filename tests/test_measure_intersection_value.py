"""Spec do backtester NF-01 item 3 — Interseção Pair Signal + divergência
macro-preço (marker Z).

Ref: scripts/measure_intersection_value.py — a metodologia de extração/
medição (entrada na barra seguinte, cooldown, MFE/MAE clampado, burn-in,
bootstrap, Kalman encadeado, gate de amostra mínima) é REUSADA inteira de
scripts/measure_pair_signal_value.py e já tem cobertura própria em
tests/test_measure_pair_signal_value.py. Este arquivo testa só o que é
genuinamente novo aqui: `_mark_intersection` (edge-triggering sobre o
ALINHAMENTO de dois estados contínuos, não sobre um marker discreto já
pronto do engine) e `_intersection_direction`.

Roda sem pytest:  python3 tests/test_measure_intersection_value.py
Ou com pytest:    pytest tests/test_measure_intersection_value.py
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
from scripts.measure_intersection_value import (
    LIMITATIONS,
    _intersection_direction,
    _mark_intersection,
)


def _snap(i, close, *, pair_signal=None, price_diverge_dir=None, pair_factor=None, ts_hour=10):
    s = IRAISnapshot(
        timestamp=f"2026-07-10T{ts_hour:02d}:{(i * 5) % 60:02d}:00+00:00",
        session_date="2026-07-10", bar_idx=i, t_frac=1.0, p_up=50.0,
        score=0.0, verdict="", verdict_color="",
    )
    s.win_current = close
    s.win_bar_open = close
    s.win_high = close
    s.win_low = close
    s.pair_signal = pair_signal
    s.price_diverge_dir = price_diverge_dir
    s.pair_factor = pair_factor
    return s


# ── 1. _mark_intersection ───────────────────────────────────────────────

def test_marca_apenas_a_primeira_barra_de_alinhamento():
    """pair_signal='buy' desde a barra 0; price_diverge_dir só vira 'buy' na
    barra 3 -> alinhamento começa na barra 3 -> intersect_compra só ali,
    mesmo com o alinhamento persistindo nas barras seguintes."""
    snaps = [
        _snap(0, 100.0, pair_signal="buy", price_diverge_dir=None),
        _snap(1, 101.0, pair_signal="buy", price_diverge_dir=None),
        _snap(2, 102.0, pair_signal="buy", price_diverge_dir=None),
        _snap(3, 103.0, pair_signal="buy", price_diverge_dir="buy"),
        _snap(4, 104.0, pair_signal="buy", price_diverge_dir="buy"),
        _snap(5, 105.0, pair_signal="buy", price_diverge_dir="buy"),
    ]
    _mark_intersection(snaps)
    marked = [i for i, s in enumerate(snaps) if s.intersect_compra is not None]
    assert marked == [3]
    assert snaps[3].intersect_compra == 103.0


def test_ignora_barras_onde_so_um_dos_dois_esta_ativo():
    """pair_signal ativo mas price_diverge_dir sempre None -> nunca alinha,
    nenhum evento."""
    snaps = [_snap(i, 100.0 + i, pair_signal="buy", price_diverge_dir=None) for i in range(10)]
    _mark_intersection(snaps)
    assert all(s.intersect_compra is None and s.intersect_venda is None for s in snaps)


def test_ignora_direcoes_opostas_simultaneas():
    """pair_signal='buy' e price_diverge_dir='sell' ao mesmo tempo -> não é
    alinhamento (direções diferentes), nenhum evento."""
    snaps = [_snap(i, 100.0, pair_signal="buy", price_diverge_dir="sell") for i in range(5)]
    _mark_intersection(snaps)
    assert all(s.intersect_compra is None and s.intersect_venda is None for s in snaps)


def test_realinhamento_apos_quebra_gera_2_eventos():
    """Alinha 'buy' nas barras 0-2, quebra (price_diverge_dir vira None) nas
    barras 3-4, realinha 'buy' de novo na barra 5 -> 2 eventos distintos
    (barra 0 e barra 5), não 1."""
    snaps = [
        _snap(0, 100.0, pair_signal="buy", price_diverge_dir="buy"),
        _snap(1, 101.0, pair_signal="buy", price_diverge_dir="buy"),
        _snap(2, 102.0, pair_signal="buy", price_diverge_dir="buy"),
        _snap(3, 103.0, pair_signal="buy", price_diverge_dir=None),
        _snap(4, 104.0, pair_signal="buy", price_diverge_dir=None),
        _snap(5, 105.0, pair_signal="buy", price_diverge_dir="buy"),
    ]
    _mark_intersection(snaps)
    marked = [i for i, s in enumerate(snaps) if s.intersect_compra is not None]
    assert marked == [0, 5]


def test_troca_de_direcao_dentro_do_alinhamento_gera_novo_evento():
    """Alinhado 'buy' nas barras 0-1, alinha 'sell' na barra 2 (sem passar
    por None) -> intersect_venda dispara na barra 2 (mudou de direção)."""
    snaps = [
        _snap(0, 100.0, pair_signal="buy", price_diverge_dir="buy"),
        _snap(1, 101.0, pair_signal="buy", price_diverge_dir="buy"),
        _snap(2, 102.0, pair_signal="sell", price_diverge_dir="sell"),
    ]
    _mark_intersection(snaps)
    assert snaps[0].intersect_compra == 100.0
    assert snaps[1].intersect_compra is None and snaps[1].intersect_venda is None
    assert snaps[2].intersect_venda == 102.0


# ── 2. _intersection_direction ──────────────────────────────────────────

def test_direction_le_campos_estampados():
    buy_snap = _snap(0, 100.0)
    buy_snap.intersect_compra = 100.0
    buy_snap.intersect_venda = None
    assert _intersection_direction(buy_snap) == "buy"

    sell_snap = _snap(0, 100.0)
    sell_snap.intersect_compra = None
    sell_snap.intersect_venda = 100.0
    assert _intersection_direction(sell_snap) == "sell"

    none_snap = _snap(0, 100.0)
    assert _intersection_direction(none_snap) is None  # sem _mark_intersection rodado antes


# ── 3. extract_trade_outcomes com preprocess+direction_of da interseção ───

def test_extrai_evento_so_quando_ambos_alinhados():
    """Uma sessão com pair_signal ativo mas price_diverge_dir sempre None
    (só o Pair, sem o Z) não deve gerar evento de interseção; alinhar os
    dois na barra 0 deve gerar exatamente 1, com entrada na barra seguinte
    (mesma metodologia dos itens 1/2)."""
    only_pair = [_snap(0, 100.0, pair_signal="buy", price_diverge_dir=None)] + [
        _snap(i, 100.0 + i * 5.0, pair_signal="buy", price_diverge_dir=None) for i in range(1, 25)
    ]
    _mark_intersection(only_pair)
    outcomes = psv.extract_trade_outcomes(
        "2026-07-10", "WIN$N", only_pair, is_b3=True, direction_of=_intersection_direction)
    assert outcomes == []

    aligned = [_snap(0, 100.0, pair_signal="buy", price_diverge_dir="buy")] + [
        _snap(i, 100.0 + i * 5.0, pair_signal="buy", price_diverge_dir="buy") for i in range(1, 25)
    ]
    _mark_intersection(aligned)
    outcomes = psv.extract_trade_outcomes(
        "2026-07-10", "WIN$N", aligned, is_b3=True, direction_of=_intersection_direction)
    assert len(outcomes) == 1
    assert outcomes[0].direction == "buy"
    assert outcomes[0].entry_price == 105.0  # barra seguinte à do 1º alinhamento


# ── 4. run() com preprocess=_mark_intersection ──────────────────────────

def test_run_usa_preprocess_e_direction_of_da_intersecao():
    dates = ["2026-07-10"]

    class _FakeCandidates:
        pass
    _FakeCandidates.dates = dates
    _FakeCandidates.discarded = []

    @contextmanager
    def fake_replay(db_path):
        def compute(date, target):
            return [_snap(0, 100.0, pair_signal="buy", price_diverge_dir="buy")] + [
                _snap(j, 100.0 + j * 5.0, pair_signal="buy", price_diverge_dir="buy")
                for j in range(1, 25)
            ]
        yield compute, None

    with patch.object(psv, "candidate_sessions", lambda db, target, limit: _FakeCandidates), \
         patch.object(psv, "chronological_replay", fake_replay):
        report = psv.run(
            "unused.db", ["WIN$N"], limit=1, iterations=50, burn_in_sessions=0,
            direction_of=_intersection_direction, limitations=LIMITATIONS,
            preprocess=_mark_intersection)

    t = report["targets"]["WIN$N"]
    assert t["by_direction"]["all"]["n_events"] == 1, (
        "sem preprocess=_mark_intersection rodando, intersect_compra nunca "
        "seria estampado e nenhum evento apareceria")
    assert report["limitations"] == LIMITATIONS
    assert t["gate_verdict"] == "INCONCLUSIVO (amostra abaixo do mínimo)", (
        "1 evento é bem abaixo do MIN_EVENTS_FOR_GATE default (100)")


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
