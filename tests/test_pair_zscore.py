"""Spec do núcleo puro do pair z-score (backend/irai/zscore.py).

Ref: .planning/notes/pair-zscore-signal.md

Cobre seleção do par ativo (maior |β|), resíduo, σ rolling e a tabela de
sinal. A parte de integração com o engine v2 (betas do Kalman → snapshot) só
é validável com banco + pykalman; aqui garantimos que a matemática está certa.

Roda sem pytest:  python3 tests/test_pair_zscore.py
Ou com pytest:    pytest tests/test_pair_zscore.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import math

from backend.irai.zscore import (
    select_active_pair, pairwise_residual, rolling_sigma, pair_zscore,
    pair_signal, normalized_zscore, DEFAULT_SIGMA,
)


def test_seleciona_maior_beta_absoluto():
    betas = [0.5, -0.2, 0.8, -0.9]          # [intercept, f0, f1, f2]
    labels = ["US500", "WDO$N", "USTEC"]
    pair = select_active_pair(betas, labels, min_beta=0.1)
    assert pair["label"] == "USTEC"
    assert pair["beta"] == -0.9
    assert pair["index"] == 2


def test_par_inativo_abaixo_do_min_beta():
    betas = [0.0, 0.05, -0.09]
    labels = ["A", "B"]
    assert select_active_pair(betas, labels, min_beta=0.1) is None


def test_residuo():
    # target +0.4%, fator +1.0%, β=0.5 → esperado +0.5% → resíduo -0.1%
    assert abs(pairwise_residual(0.004, 0.5, 0.010) - (-0.001)) < 1e-12


def test_rolling_sigma_poucos_pontos_zero():
    assert rolling_sigma([0.001], window=20) == 0.0
    assert rolling_sigma([], window=20) == 0.0


def test_rolling_sigma_janela():
    vals = [1.0, 1.0, 1.0, 5.0, 5.0, 5.0]
    # últimos 3 iguais → σ 0
    assert rolling_sigma(vals, window=3) == 0.0
    # nos 6: média 3, var = (2²·3 + 2²·3)/6 = 4 → σ = 2
    assert abs(rolling_sigma(vals, window=6) - 2.0) < 1e-12


def test_sinal_inverso_beta_negativo():
    assert pair_signal(-2.0, beta=-0.7, threshold=1.5) == "buy"
    assert pair_signal(+2.0, beta=-0.7, threshold=1.5) == "sell"


def test_sinal_direto_beta_positivo():
    assert pair_signal(-2.0, beta=+0.7, threshold=1.5) == "sell"
    assert pair_signal(+2.0, beta=+0.7, threshold=1.5) == "buy"


def test_sinal_neutro_dentro_da_banda():
    assert pair_signal(1.0, beta=-0.7, threshold=1.5) == "neutral"
    assert pair_signal(-1.0, beta=0.7, threshold=1.5) == "neutral"
    assert pair_signal(5.0, beta=0.0, threshold=1.5) == "neutral"   # β=0 → sem par


def test_normalized_zscore_piso_default_sigma():
    # z de FATOR (não do par): σ=0 usa o piso DEFAULT_SIGMA (não explode nem zera).
    assert abs(normalized_zscore(-0.001, 0.002, 1.0) - (-0.5)) < 1e-12
    assert normalized_zscore(-0.001, 0.0, 1.0) == -0.001 / (DEFAULT_SIGMA * 1.0)


# ── pair_zscore: reversão à média, centrado, sem √t (fix do z degenerado) ──

def test_pair_zscore_tendencia_suave_limitada():
    # Resíduo em tendência suave (hedge ruim): a fórmula ANTIGA dava z≈29.
    # A nova é limitada por √3·√((n-1)/(n+1)) ≈ 1.65 em n=20 — nunca explode.
    trend = [0.0004 * i for i in range(30)]
    z = pair_zscore(trend, window=20)
    assert abs(z) <= 1.66, f"tendência suave deve ficar <=~1.65, veio {z}"


def test_pair_zscore_oscilando_sano():
    osc = [0.001 * math.sin(i) for i in range(30)]
    assert abs(pair_zscore(osc, window=20)) < 3.0


def test_pair_zscore_salto_dispara():
    # deslocamento genuíno do equilíbrio → z deve exceder o threshold típico
    disloc = [0.0001 * i for i in range(19)] + [0.02]
    assert pair_zscore(disloc, window=20) > 2.0


def test_pair_zscore_poucas_amostras_zero():
    assert pair_zscore([0.5], window=20) == 0.0
    assert pair_zscore([], window=20) == 0.0


def test_pair_zscore_sigma_zero_zera_sinal():
    # resíduo constante → σ=0 → SEM sinal (0.0), NÃO o piso DEFAULT_SIGMA
    # (oposto do z de fator): no par, σ degenerado não pode fabricar magnitude.
    assert pair_zscore([0.3] * 20, window=20) == 0.0


# ── vol-guard em select_active_pair: exclui fatores de σ quase-nula do par ──

def test_vol_guard_exclui_sigma_quase_nula():
    # f1 tem o maior |β| (0.9) MAS σ quase-nula (bond ETF) → não pode ser o par.
    betas = [0.0, -0.3, 0.9, 0.4]              # [intercept, f0, f1, f2]
    labels = ["WDO$N", "iSharesTreasury1-3+", "US30"]
    sigmas = [0.0045, 0.00005, 0.0065]         # f1 muito abaixo da mediana
    pair = select_active_pair(betas, labels, min_beta=0.1, sigmas=sigmas)
    assert pair["label"] != "iSharesTreasury1-3+"
    assert pair["label"] == "US30"             # próximo maior |β| com σ normal


def test_vol_guard_sem_sigmas_e_legado():
    # sigmas=None → comportamento antigo (só maior |β|)
    betas = [0.0, -0.3, 0.9, 0.4]
    labels = ["WDO$N", "iSharesTreasury1-3+", "US30"]
    assert select_active_pair(betas, labels, min_beta=0.1)["label"] == "iSharesTreasury1-3+"


def test_vol_guard_sigma_zero_excluido():
    # σ exatamente 0 (glitch) também deve ser excluído do par (não TypeError)
    betas = [0.0, 0.9, 0.4]
    labels = ["iSharesTreasury1-3+", "US30"]
    sigmas = [0.0, 0.0065]
    assert select_active_pair(betas, labels, min_beta=0.1, sigmas=sigmas)["label"] == "US30"


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
