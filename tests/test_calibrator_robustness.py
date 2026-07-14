"""Regressões da calibração robusta e do holdout temporal."""

import importlib.util
import os

import pytest
import numpy as np
import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "calibrate_universal_robustness",
    os.path.join(ROOT, "scripts", "calibrate_universal.py"),
)
calibrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calibrator)


def test_ridge_estabiliza_coeficientes_de_cesta_colinear():
    rng = np.random.default_rng(42)
    base = rng.normal(size=180)
    x = np.column_stack(
        [base, base + rng.normal(scale=1e-9, size=180), rng.normal(size=180)]
    )
    y = 0.8 * base + 0.2 * x[:, 2] + rng.normal(scale=0.02, size=180)
    y_perturbado = y.copy()
    y_perturbado[-1] += 1e-5

    beta_ols = np.linalg.lstsq(np.column_stack([x, np.ones(len(x))]), y, rcond=None)[0]
    beta_ols_perturbado = np.linalg.lstsq(
        np.column_stack([x, np.ones(len(x))]), y_perturbado, rcond=None
    )[0]
    ridge = calibrator.fit_ridge(x, y, alpha=1.0)
    ridge_perturbado = calibrator.fit_ridge(x, y_perturbado, alpha=1.0)

    assert np.linalg.norm(beta_ols[:3] - beta_ols_perturbado[:3]) > 10
    assert np.linalg.norm(ridge.coef - ridge_perturbado.coef) < 1e-4
    assert np.isfinite(ridge.coef).all()


def test_guard_usa_condicao_da_matriz_original_nao_das_equacoes_normais():
    rng = np.random.default_rng(7)
    base = rng.normal(size=120)
    colinear = np.column_stack([base, base + rng.normal(scale=1e-5, size=120)])
    independente = np.column_stack([base, rng.normal(size=120)])

    assert calibrator.design_condition_number(colinear) > calibrator.MAX_CONDITION_NUMBER
    assert calibrator.design_condition_number(independente) < calibrator.MAX_CONDITION_NUMBER


def test_descarta_ultima_sessao_antes_de_limitar_a_janela():
    dates = pd.date_range("2025-01-01", periods=260, freq="B").date
    frame = pd.DataFrame({"target": np.arange(260)}, index=dates)

    complete, discarded = calibrator.discard_latest_session(frame)

    assert discarded == dates[-1]
    assert dates[-1] not in complete.index
    assert complete.index[-1] == dates[-2]
    assert len(complete.iloc[-252:]) == 252


def test_alpha_e_escolhido_sem_consultar_holdout(monkeypatch):
    # Este teste exercita `calibrate_target` de verdade (é o que o torna útil, em
    # vez do teste-vácuo que ele substituiu), e o caminho real puxa sklearn — que
    # não existe nesta máquina de dev. Mesmo padrão de skip dos testes vizinhos.
    pytest.importorskip("sklearn", reason="sklearn não instalado neste ambiente")

    rng = np.random.default_rng(9)
    dates = pd.date_range("2025-01-01", periods=171, freq="B").date
    fator_a = rng.normal(size=len(dates))
    fator_b = rng.normal(size=len(dates))
    target = fator_a - 0.3 * fator_b + rng.normal(scale=0.1, size=len(dates))

    # As últimas 20 sessões são o holdout; o valor extremo denuncia qualquer
    # vazamento nos arrays usados para escolher alpha.
    fator_a[-21:-1] = 999_999.0
    fator_b[-21:-1] = -999_999.0
    target[-21:-1] = np.where(np.arange(20) % 2 == 0, 999_999.0, -999_999.0)
    daily = {
        "TARGET": pd.Series(target, index=dates),
        "FATOR_A": pd.Series(fator_a, index=dates),
        "FATOR_B": pd.Series(fator_b, index=dates),
    }
    monkeypatch.setattr(calibrator, "load_daily_returns", lambda *args: daily)

    chamadas = []
    choose_original = calibrator.choose_ridge_alpha

    def choose_spy(x, y, alphas=calibrator.RIDGE_ALPHAS):
        chamadas.append((np.asarray(x).copy(), np.asarray(y).copy()))
        return choose_original(x, y, alphas)

    monkeypatch.setattr(calibrator, "choose_ridge_alpha", choose_spy)

    resultado = calibrator.calibrate_target(
        None,
        "TARGET",
        forced_factors=["FATOR_A", "FATOR_B"],
        holdout_sessions=20,
    )

    assert resultado is not None
    assert len(chamadas) == 2
    for x_recebido, y_recebido in chamadas:
        assert len(x_recebido) == len(y_recebido) == 150
        assert np.abs(x_recebido).max() < 999_999.0
        assert np.abs(y_recebido).max() < 999_999.0


def test_as_of_isola_cesta_pesos_sigmas_e_logistica_de_sessoes_futuras(monkeypatch):
    pytest.importorskip("sklearn", reason="sklearn não instalado neste ambiente")

    rng = np.random.default_rng(20260713)
    dates = pd.date_range("2025-08-01", periods=230, freq="B").date
    cutoff = pd.Timestamp("2026-04-30").date()
    fator_a = rng.normal(size=len(dates))
    fator_b = rng.normal(size=len(dates))
    target = 0.7 * fator_a - 0.2 * fator_b + rng.normal(scale=0.15, size=len(dates))

    original = {
        "TARGET": pd.Series(target, index=dates),
        "FATOR_A": pd.Series(fator_a, index=dates),
        "FATOR_B": pd.Series(fator_b, index=dates),
    }
    envenenado = {nome: serie.copy() for nome, serie in original.items()}
    futuro = np.asarray(dates) > cutoff
    envenenado["TARGET"].iloc[futuro] = np.where(np.arange(futuro.sum()) % 2, 1e12, -1e12)
    envenenado["FATOR_A"].iloc[futuro] = 1e15
    envenenado["FATOR_B"].iloc[futuro] = -1e15
    envenenado["FATOR_SO_FUTURO"] = pd.Series(
        np.full(futuro.sum(), 1e20), index=np.asarray(dates)[futuro]
    )
    monkeypatch.setattr(calibrator, "ALL_FACTORS", ["FATOR_A", "FATOR_B", "FATOR_SO_FUTURO"])

    def calibrar(daily):
        monkeypatch.setattr(calibrator, "load_daily_returns", lambda *args: daily)
        return calibrator.calibrate_target(
            None,
            "TARGET",
            min_factors=1,
            max_factors=2,
            holdout_sessions=20,
            as_of="2026-04-30",
        )

    resultado_original = calibrar(original)
    resultado_envenenado = calibrar(envenenado)

    assert resultado_original["factors"] == resultado_envenenado["factors"]
    assert resultado_original["weights"] == pytest.approx(resultado_envenenado["weights"])
    assert resultado_original["sigmas"] == pytest.approx(resultado_envenenado["sigmas"])
    assert resultado_original["alpha"] == pytest.approx(resultado_envenenado["alpha"])
    assert resultado_original["intercept"] == pytest.approx(resultado_envenenado["intercept"])
