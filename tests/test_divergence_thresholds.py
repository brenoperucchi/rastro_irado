"""Spec dos thresholds canônicos da divergência preço-vs-modelo (engine.py).

Contexto: os gates de `flow_confirms`/`price_diverges` (`snap.p_up > X` /
`< Y`) eram literais `55`/`45` hardcoded em DOIS lugares independentes
(`backend/irai/engine.py` e uma 2ª cópia em `backend/api/main.py::irai_overview`
— removida nesta mesma mudança), e o frontend re-derivava a DIREÇÃO da
divergência (`price_diverge_dir`) sozinho, com thresholds diferentes e
inconsistentes entre `App.jsx` (55) e `Overview.jsx` (60, o do gauge geral).

A correção: os gates viram configuráveis via `divergence_config` (chaves
`p_up_gate_hi`/`p_up_gate_lo`, default 55.0/45.0 — `DEFAULT_P_UP_GATE_HI`/
`DEFAULT_P_UP_GATE_LO` em `engine.py`), e a direção (`div_dir`) passa a ser
persistida em `snap.price_diverge_dir` — o frontend deve consumir esse campo
em vez de re-derivá-lo.

Estes testes travam:
  1. Com o gate DEFAULT, uma divergência que cruza 55/45 dispara normalmente
     (comportamento preservado).
  2. Um gate mais exigente (via `divergence_config.p_up_gate_hi/lo`) SUPRIME
     a mesma divergência — prova que o valor vem de config, não hardcode.
  3. `price_diverge_dir` é sempre coerente com `price_diverges` (None quando
     False; "buy"/"sell" quando True, nunca outro valor).

Roda sem pytest:  python3 tests/test_divergence_thresholds.py
Ou com pytest:    pytest tests/test_divergence_thresholds.py
"""
import os
import sys
import json
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_premarket as tp  # reaproveita _seed/_engine/SESSION/TARGET/SLUG

from backend.irai.engine import DEFAULT_P_UP_GATE_HI, DEFAULT_P_UP_GATE_LO


def _run_com_gate_e_pup_forcado(gate_hi=None, gate_lo=None,
                                 forced_p_up=60.0, forced_win_return=-2.0):
    """Semeia a fixture padrão, opcionalmente sobrescreve p_up_gate_hi/lo via
    divergence_config, e força p_up/win_return em TODA barra real (via wrap
    de eng.compute — barras ghost/pré-mercado não passam por compute(), então
    não são afetadas, confirmado empiricamente). Retorna as barras reais."""
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    tp._seed(db)
    if gate_hi is not None or gate_lo is not None:
        cfg = {}
        if gate_hi is not None:
            cfg["p_up_gate_hi"] = gate_hi
        if gate_lo is not None:
            cfg["p_up_gate_lo"] = gate_lo
        c = sqlite3.connect(db)
        c.execute("UPDATE asset_models SET divergence_config = ? WHERE slug = ?",
                  (json.dumps(cfg), tp.SLUG))
        c.commit()
        c.close()

    eng = tp._engine(db)
    orig_compute = eng.compute

    def wrapped(*a, **k):
        snap = orig_compute(*a, **k)
        snap.p_up = forced_p_up
        snap.win_return = forced_win_return
        return snap

    eng.compute = wrapped
    snaps = eng.compute_from_db(tp.SESSION, target=tp.TARGET, version="v1", persist_state=False)
    reais = [s for s in snaps if not s.is_ghost]
    assert reais, "fixture inválida: nenhuma barra real"
    return reais


def test_gate_default_dispara_divergencia_lado_compra():
    """p_up=60 (>55, default hi) + retorno bem negativo (ret_z << -0.5) ->
    'o modelo compra mas o preço não acompanhou' -> price_diverges=True,
    price_diverge_dir='buy'."""
    reais = _run_com_gate_e_pup_forcado()
    for s in reais:
        assert s.price_diverges is True
        assert s.price_diverge_dir == "buy"
        assert s.price_diverge_z is not None and s.price_diverge_z < -0.5


def test_gate_mais_exigente_suprime_a_mesma_divergencia():
    """MESMO p_up=60 e MESMO retorno negativo do teste anterior, mas com
    p_up_gate_hi=70 configurado -> 60 não passa de 70, o lado compra nem
    chega a ser avaliado. Prova que o gate vem de divergence_config, não do
    literal 55 hardcoded (senão este teste teria o mesmo resultado do
    anterior, já que p_up e ret_z não mudaram)."""
    reais = _run_com_gate_e_pup_forcado(gate_hi=70.0, gate_lo=30.0)
    for s in reais:
        assert s.price_diverges is False, (
            "gate configurado (70) foi ignorado — ainda usando o default (55)?")
        assert s.price_diverge_dir is None


def test_gate_default_bate_com_as_constantes_nomeadas():
    """DEFAULT_P_UP_GATE_HI/LO (importáveis, usadas pela API em
    /api/irai/targets) são exatamente o 55.0/45.0 que o engine sempre usou —
    documenta o valor, não só o comportamento."""
    assert DEFAULT_P_UP_GATE_HI == 55.0
    assert DEFAULT_P_UP_GATE_LO == 45.0


def test_price_diverge_dir_nunca_diverge_de_price_diverges():
    """Invariante de forma, sob qualquer gate: dir é None sse não diverge."""
    for reais in (
        _run_com_gate_e_pup_forcado(),
        _run_com_gate_e_pup_forcado(gate_hi=70.0, gate_lo=30.0),
        _run_com_gate_e_pup_forcado(forced_p_up=40.0, forced_win_return=2.0),  # lado venda
    ):
        for s in reais:
            if s.price_diverges:
                assert s.price_diverge_dir in ("buy", "sell")
            else:
                assert s.price_diverge_dir is None


def test_gate_lo_configuravel_dispara_lado_venda():
    """Espelho dos testes acima pro lado 'venda': p_up baixo + retorno
    positivo, com p_up_gate_lo customizado."""
    reais = _run_com_gate_e_pup_forcado(
        gate_hi=90.0, gate_lo=42.0, forced_p_up=41.0, forced_win_return=2.0)
    for s in reais:
        assert s.price_diverges is True
        assert s.price_diverge_dir == "sell"


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
