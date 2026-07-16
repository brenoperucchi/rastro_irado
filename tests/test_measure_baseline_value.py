"""Spec dos baselines NF-01 (momentum e reversão — AC #3 do IRAI-2).

Ref: scripts/measure_baseline_value.py — a metodologia de extração/medição
(entrada na barra seguinte, cooldown, MFE/MAE, timestamps causais) é REUSADA
inteira de scripts/measure_pair_signal_value.py e já tem cobertura própria.
Este arquivo testa só o que é novo: o cruzamento de SMA edge-triggered
(`_mark_momentum`/`_mark_reversao`) e que momentum e reversão são exatamente
opostos na direção.

Roda sem pytest:  python3 tests/test_measure_baseline_value.py
Ou com pytest:    pytest tests/test_measure_baseline_value.py
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
from scripts.measure_baseline_value import (
    BASELINE_FAST,
    BASELINE_SLOW,
    _baseline_direction,
    _mark_momentum,
    _mark_reversao,
    _limitations,
)


def _snap(i, close):
    s = IRAISnapshot(
        timestamp=f"2026-07-10T10:{(i * 5) % 60:02d}:00",
        session_date="2026-07-10", bar_idx=i, t_frac=1.0, p_up=50.0,
        score=0.0, verdict="", verdict_color="",
    )
    s.win_current = close
    return s


def _down_up_down(leg=BASELINE_SLOW + 6):
    """Cai `leg` barras, sobe `leg`, cai `leg`. A queda inicial deixa a fast
    ABAIXO da slow (fast reage mais rápido); a subida força um cruzamento
    PRA CIMA (compra no momentum); a queda final força um cruzamento PRA
    BAIXO (venda no momentum). Garante os dois cruzamentos genuínos —
    subida/queda monotônica DESDE o início não cruzaria (a fast já nasceria
    do lado certo e nunca trocaria)."""
    closes = [200.0 - 2.0 * i for i in range(leg)]          # cai 200 -> ~
    bottom = closes[-1]
    closes += [bottom + 2.0 * (i + 1) for i in range(leg)]  # sobe
    top = closes[-1]
    closes += [top - 2.0 * (i + 1) for i in range(leg)]     # cai de novo
    return [_snap(i, c) for i, c in enumerate(closes)]


# ── 1. _mark_momentum ────────────────────────────────────────────────────

def test_momentum_marca_compra_no_cruzamento_pra_cima():
    """A perna de SUBIDA (após a queda inicial) faz a fast cruzar acima da
    slow -> momentum estampa COMPRA (segue a tendência). Nada é estampado
    antes de haver SLOW barras."""
    snaps = _down_up_down()
    _mark_momentum(snaps)
    compras = [i for i, s in enumerate(snaps) if s.baseline_compra is not None]
    assert compras, "a subida deveria gerar ao menos 1 cruzamento de compra"
    assert min(compras) >= BASELINE_SLOW - 1, "não pode marcar antes de ter SLOW barras"


def test_momentum_marca_venda_no_cruzamento_pra_baixo():
    snaps = _down_up_down()
    _mark_momentum(snaps)
    compras = [i for i, s in enumerate(snaps) if s.baseline_compra is not None]
    vendas = [i for i, s in enumerate(snaps) if s.baseline_venda is not None]
    assert compras and vendas, "subida-depois-queda deveria gerar compra E venda"
    assert min(compras) < min(vendas), "compra (subida) antes da venda (queda final)"


def test_reversao_e_exatamente_o_oposto_do_momentum():
    """Na MESMA série, reversão troca compra<->venda em relação ao momentum,
    barra a barra."""
    snaps_m = _down_up_down()
    snaps_r = _down_up_down()
    _mark_momentum(snaps_m)
    _mark_reversao(snaps_r)
    for sm, sr in zip(snaps_m, snaps_r):
        # onde momentum marca compra, reversão marca venda e vice-versa
        assert (sm.baseline_compra is not None) == (sr.baseline_venda is not None)
        assert (sm.baseline_venda is not None) == (sr.baseline_compra is not None)


def test_sem_cruzamento_nao_gera_evento():
    """Preço constante -> fast == slow, nunca cruza -> nenhum evento."""
    snaps = [_snap(i, 100.0) for i in range(BASELINE_SLOW + 20)]
    _mark_momentum(snaps)
    assert all(s.baseline_compra is None and s.baseline_venda is None for s in snaps)


# ── 2. _baseline_direction ───────────────────────────────────────────────

def test_direction_le_campos_estampados():
    s = _snap(0, 100.0)
    s.baseline_compra = 100.0
    s.baseline_venda = None
    assert _baseline_direction(s) == "buy"
    s.baseline_compra = None
    s.baseline_venda = 100.0
    assert _baseline_direction(s) == "sell"
    s.baseline_venda = None
    assert _baseline_direction(s) is None


# ── 3. Integração via run() (fake replay) ────────────────────────────────

def test_run_usa_preprocess_e_direction_do_baseline():
    dates = ["2026-07-10"]

    class _FakeCandidates:
        pass
    _FakeCandidates.dates = dates
    _FakeCandidates.discarded = []

    @contextmanager
    def fake_replay(db_path):
        def compute(date, target):
            return _down_up_down()
        yield compute, None

    with patch.object(psv, "candidate_sessions", lambda db, target, limit: _FakeCandidates), \
         patch.object(psv, "chronological_replay", fake_replay):
        report = psv.run(
            "unused.db", ["WIN$N"], limit=1, iterations=50, burn_in_sessions=0,
            direction_of=_baseline_direction, preprocess=_mark_momentum,
            limitations=_limitations("momentum", point_in_time=False))

    t = report["targets"]["WIN$N"]
    assert t["by_direction"]["all"]["n_events"] >= 1, (
        "sem preprocess=_mark_momentum, nenhum baseline_compra/venda seria estampado")


def test_limitations_incluem_ressalva_de_regua_e_invariancia():
    lim = _limitations("momentum", point_in_time=False)
    assert any("RÉGUA" in x for x in lim)
    assert any("INVARIANTE ao modo point-in-time" in x for x in lim)
    assert any("IRAI-4/VAL-04" in x for x in lim)


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
