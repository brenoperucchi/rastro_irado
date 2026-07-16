"""Contrato mínimo do controle GEX no dashboard.

O backend pode invalidar um snapshot GEX durante uma sessão. O operador não
pode continuar com o toggle clicável/ativo por estado React antigo.
"""

from pathlib import Path


APP = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx").read_text()


def test_botao_gex_inativo_usa_disabled_nativo():
    assert "disabled={!gex.active}" in APP


def test_gex_e_reconsultado_periodicamente_para_nao_reter_estado_antigo():
    assert "setInterval(refreshGex, 60_000)" in APP
    assert "if (!d?.active)" in APP
