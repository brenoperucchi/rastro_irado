"""Regressão do guard de sigma no z-score do engine.

Bug original (engine.py:313-316, pré-fix):
    if state.sigma > 0:
        state.z_score = state.ret / (state.sigma * sqrt_t)
    else:
        state.z_score = 0.0        # σ≤0 → contribuição do fator morta

Um fator com σ ≤ 0 (ausente/gravado como 0/degenerado) tinha z-score zerado,
tirando-o do P_up silenciosamente. Se acontecesse com todos os fatores, o P_up
congelava. Correção: normalized_zscore usa o piso DEFAULT_SIGMA quando σ ≤ 0.

Roda sem pytest:  python3 tests/test_zscore.py
Ou com pytest:    pytest tests/test_zscore.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.irai.zscore import DEFAULT_SIGMA, normalized_zscore


def test_sigma_zero_nao_mata_o_sinal():
    """O bug: antes retornava 0.0; agora usa o piso DEFAULT_SIGMA."""
    z = normalized_zscore(ret=0.005, sigma=0.0, sqrt_t=1.0)
    assert z != 0.0, "σ=0 não pode zerar o z-score (sinal morto)"
    assert z == 0.005 / (DEFAULT_SIGMA * 1.0)


def test_sigma_negativo_usa_piso():
    z = normalized_zscore(ret=0.005, sigma=-1.0, sqrt_t=1.0)
    assert z == 0.005 / (DEFAULT_SIGMA * 1.0)


def test_sigma_positivo_calcula_normal():
    z = normalized_zscore(ret=0.006, sigma=0.003, sqrt_t=2.0)
    assert z == 0.006 / (0.003 * 2.0)


def test_sqrt_t_zero_nao_divide_por_zero():
    """Início de sessão (t=0) não pode dar divisão por zero."""
    assert normalized_zscore(ret=0.01, sigma=0.01, sqrt_t=0.0) == 0.0


def test_ret_zero_da_zero():
    assert normalized_zscore(ret=0.0, sigma=0.0, sqrt_t=1.0) == 0.0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passaram")
    sys.exit(1 if failed else 0)
