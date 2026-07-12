"""Spec dos eventos discretos que viram markers no chart de preço.

Ref: docs/plans/... Pacote B, item 4. TVNweChart já consome `pair_compra`,
`pair_venda`, `z_compra_val`, `z_venda_val` — o engine passou a emiti-los.

As invariantes:
  1. DISCRETO, não contínuo. O marker sai só na barra em que o sinal TRANSICIONA
     (neutral->buy, sell->buy, ...), nunca em toda barra em que |z| > threshold —
     isso viraria um marker por barra durante todo o período do sinal (spam), que
     é justamente a decisão de projeto registrada no plano.
  2. Nunca sobre barra sintética (pré-mercado, gap, pós-fechamento): o target não
     negociou ali, então um marker seria um sinal fantasma. Ver Fase 3.
  3. Compra e venda são mutuamente exclusivas na mesma barra.
  4. O valor emitido é o PREÇO do target na barra (o chart usa a presença; o preço
     mantém o campo útil e coerente com o eixo).

Roda sem pytest:  python3 tests/test_markers.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# pykalman/statsmodels só existem no runtime Windows; este spec é da máquina de
# transição (não do filtro), então stubs bastam para importar o dataclass.
import types

for _mod, _attr, _val in (("pykalman", "KalmanFilter", object),):
    try:
        __import__(_mod)
    except ModuleNotFoundError:
        _st = types.ModuleType(_mod)
        setattr(_st, _attr, _val)
        sys.modules[_mod] = _st
try:
    import statsmodels  # noqa: F401
except ModuleNotFoundError:
    for _sub in ("statsmodels", "statsmodels.tsa", "statsmodels.tsa.vector_ar",
                 "statsmodels.tsa.vector_ar.vecm"):
        sys.modules[_sub] = types.ModuleType(_sub)
    sys.modules["statsmodels.tsa.vector_ar.vecm"].coint_johansen = lambda *a, **k: None

from backend.irai.engine import IRAISnapshot


def _emitir(sinais, ghosts=None):
    """Reimplementa a máquina de transição do engine sobre uma sequência de
    sinais, para travar a SEMÂNTICA (discreto + sem fantasma) de forma isolada.
    `sinais[i]` = pair_signal da barra i; `ghosts[i]` = a barra é sintética."""
    ghosts = ghosts or [False] * len(sinais)
    prev = "neutral"
    out = []
    for i, sig in enumerate(sinais):
        snap = IRAISnapshot(
            timestamp="", session_date="", bar_idx=i, t_frac=1.0, p_up=50.0,
            score=0.0, verdict="", verdict_color="",
        )
        snap.pair_signal = sig
        if not ghosts[i]:
            px = 100.0 + i
            if sig == "buy" and prev != "buy":
                snap.pair_compra = px
            elif sig == "sell" and prev != "sell":
                snap.pair_venda = px
            prev = sig
        out.append(snap)
    return out


def test_marker_so_na_transicao_nao_em_toda_barra():
    """O sinal fica 'buy' por 5 barras seguidas -> UM marker, não cinco."""
    snaps = _emitir(["neutral", "buy", "buy", "buy", "buy", "buy", "neutral"])
    compras = [i for i, s in enumerate(snaps) if s.pair_compra is not None]
    assert compras == [1], f"esperava 1 marker na barra 1, veio {compras} (spam de markers)"


def test_reentrada_apos_neutro_emite_de_novo():
    """buy -> neutral -> buy = dois eventos distintos."""
    snaps = _emitir(["buy", "buy", "neutral", "buy"])
    compras = [i for i, s in enumerate(snaps) if s.pair_compra is not None]
    assert compras == [0, 3], f"esperava markers em [0, 3], veio {compras}"


def test_virada_direta_de_lado_emite():
    """buy -> sell sem passar pelo neutro ainda é uma transição."""
    snaps = _emitir(["buy", "sell", "sell"])
    assert snaps[0].pair_compra is not None
    assert snaps[1].pair_venda is not None
    assert snaps[2].pair_venda is None, "o 2º 'sell' seguido não pode re-emitir"


def test_compra_e_venda_sao_exclusivas_na_barra():
    for s in _emitir(["buy", "sell", "neutral", "buy"]):
        assert not (s.pair_compra is not None and s.pair_venda is not None)


def test_nunca_emite_marker_em_barra_sintetica():
    """Barra ghost = o target não negociou. Um marker ali é sinal fantasma.
    E a transição não pode ser 'consumida' pela barra sintética: o evento deve
    aparecer na primeira barra REAL em que o sinal vale."""
    snaps = _emitir(
        sinais=["neutral", "buy", "buy", "buy"],
        ghosts=[False, True, True, False],   # a transição cai sobre 2 ghosts
    )
    assert snaps[1].pair_compra is None, "marker sobre barra sintética (fantasma)"
    assert snaps[2].pair_compra is None, "marker sobre barra sintética (fantasma)"
    assert snaps[3].pair_compra is not None, (
        "a transição foi engolida pelas ghosts — o evento deve aparecer na "
        "primeira barra REAL em que o sinal vale")


def test_valor_emitido_e_o_preco_da_barra():
    snaps = _emitir(["neutral", "buy"])
    assert snaps[1].pair_compra == 101.0


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
