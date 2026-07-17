"""Contrato leve do seletor visual P Dinâmico no dashboard."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend" / "src" / "App.jsx").read_text()
CHART = (ROOT / "frontend" / "src" / "charts" / "TVProbabilityChart.jsx").read_text()


def test_frontend_busca_comparacao_no_backend_em_vez_de_firebase_direto():
    assert "/api/irai/p-dynamic-comparison" in APP
    assert "if (!API || selectedTarget !== 'WIN$N' || !effectiveDate)" in APP


def test_frontend_expoe_toggles_das_quatro_curvas_e_chart_multiserie():
    for label in ("Miqueias público", "IRAI v1", "IRAI v2", "Miqueias estático"):
        assert label in APP
    assert "comparisonSeries" in CHART
    assert "seriesByIdRef" in CHART
