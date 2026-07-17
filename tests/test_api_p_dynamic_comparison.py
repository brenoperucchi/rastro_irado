"""Contrato da comparação visual do P Dinâmico para WIN$N.

A rota deve servir as curvas locais v1/v2, o challenger estático configurado e
a série pública do Miqueias sem deixar um feed de outra sessão parecer dado
comparável. O teste chama a rota diretamente e isola rede/engine reais.
"""
from __future__ import annotations

import asyncio
import math
import os
import sys
import types

import pytest

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
    statsmodels_stub = types.ModuleType("statsmodels")
    for submodule in ("statsmodels.tsa", "statsmodels.tsa.vector_ar",
                      "statsmodels.tsa.vector_ar.vecm"):
        sys.modules[submodule] = types.ModuleType(submodule)
    sys.modules["statsmodels"] = statsmodels_stub
    sys.modules["statsmodels.tsa.vector_ar.vecm"].coint_johansen = (
        lambda *args, **kwargs: None
    )

try:
    import backend.api.main as api_main
    _HAS_FASTAPI = True
except ModuleNotFoundError:
    _HAS_FASTAPI = False


MIQUEIAS_FACTORS = {
    "wdo": {"weight": -0.604859, "sigma": 0.006909},
    "di1": {"weight": -0.315301, "sigma": 0.008131},
    "brent": {"weight": -0.005800, "sigma": 0.020946},
    "btcusd": {"weight": 0.0, "sigma": 0.014342},
    "us30": {"weight": 0.076299, "sigma": 0.006229},
    "usdmxn": {"weight": -0.303354, "sigma": 0.004309},
    "cadchf": {"weight": 0.084927, "sigma": 0.002972},
    "isharestreasury1-3+": {"weight": 0.257738, "sigma": 0.000360},
}


def _skip_without_fastapi():
    if _HAS_FASTAPI:
        return False
    pytest.skip("fastapi não instalado neste ambiente")


def _local_bar(p_up):
    return {
        "timestamp": "2026-07-16T15:00:00+00:00",
        "p_up": p_up,
        "t_frac": 0.25,
        "is_ghost": False,
        "is_preview": False,
        "factors": {
            name: {"ret": 1.0 if name == "wdo" else 0.0}
            for name in MIQUEIAS_FACTORS
        },
    }


def _install_local_series(monkeypatch):
    async def fake_series(*, session_date, target, version):
        assert session_date == "2026-07-16"
        assert target == "WIN$N"
        return {
            "session_date": session_date,
            "target": target,
            "is_b3": True,
            "brt_offset_h": 6,
            "series": [_local_bar(41.0 if version == "v1" else 42.0)],
        }

    monkeypatch.setattr(api_main, "irai_series", fake_series)
    api_main.p_dynamic_comparison_cache.clear()
    api_main.miqueias_public_cache.clear()


def test_comparacao_expoe_quatro_series_e_static_usa_percent_e_sqrt_t(monkeypatch):
    _skip_without_fastapi()
    _install_local_series(monkeypatch)
    monkeypatch.setattr(api_main, "_fetch_miqueias_public_document", lambda: [{
        "timestamp": "2026-07-16T15:00:00+00:00", "p_up": 55.0,
    }])

    result = asyncio.run(api_main.p_dynamic_comparison(
        session_date="2026-07-16", target="WIN$N"))

    assert set(result["series"]) == {"miqueias_public", "v1", "v2", "miqueias_static"}
    assert result["series"]["v1"][0]["p_up"] == 41.0
    assert result["series"]["v2"][0]["p_up"] == 42.0
    assert result["series"]["miqueias_public"][0]["p_up"] == 55.0

    # Retorno serializado e 1.0%, portanto 0.01 em fração. Com t_frac=0.25,
    # o z usa sqrt(t)=0.5. A fórmula antiga, que lia 1.0 como fração e omitia
    # sqrt(t), produziria uma probabilidade completamente diferente.
    z_wdo = 0.01 / (0.006909 * math.sqrt(0.25))
    expected = 100.0 / (1.0 + math.exp(-((1.918606 * (-0.604859 * z_wdo)) - 0.25)))
    assert result["series"]["miqueias_static"][0]["p_up"] == pytest.approx(expected)
    assert result["availability"]["miqueias_static"]["available"] is True


def test_comparacao_recusa_feed_publico_de_outra_sessao(monkeypatch):
    _skip_without_fastapi()
    _install_local_series(monkeypatch)
    monkeypatch.setattr(api_main, "_fetch_miqueias_public_document", lambda: [{
        "timestamp": "2026-07-17T15:00:00+00:00", "p_up": 55.0,
    }])

    result = asyncio.run(api_main.p_dynamic_comparison(
        session_date="2026-07-16", target="WIN$N"))

    assert result["series"]["miqueias_public"] == []
    assert result["availability"]["miqueias_public"] == {
        "available": False,
        "reason": "série pública indisponível para a sessão 2026-07-16",
    }


def test_serie_publica_normaliza_offset_utc_e_recusa_timestamp_sem_fuso():
    _skip_without_fastapi()

    points = api_main._miqueias_public_rows([{
        # Ainda é 16/07 no eixo UTC/Tickmill; filtrar pelo dia de origem (17)
        # e plotar 00:30 deslocaria a curva em três horas.
        "timestamp": "2026-07-17T00:30:00+03:00", "p_up": 55.0,
    }], "2026-07-16")
    assert points == [{
        "timestamp": "2026-07-16T21:30:00Z",
        "p_up": 55.0,
        "is_ghost": False,
        "is_preview": False,
        "source_field": "p_up",
    }]

    with pytest.raises(ValueError, match="fuso explícito"):
        api_main._miqueias_public_rows([{
            "timestamp": "2026-07-16T21:30:00", "p_up": 55.0,
        }], "2026-07-16")


def test_notify_update_invalida_cache_local_e_publico_da_comparacao():
    _skip_without_fastapi()
    api_main.p_dynamic_comparison_cache[("WIN$N", "2026-07-16")] = {"old": True}
    api_main.miqueias_public_cache.update({"document": [], "fetched_at": 1.0})

    asyncio.run(api_main.notify_update())

    assert api_main.p_dynamic_comparison_cache == {}
    assert api_main.miqueias_public_cache == {}
