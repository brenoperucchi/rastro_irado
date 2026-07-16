"""Specs da auditoria reproduzível de rollover das séries contínuas B3."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.audit_continuous_rollover import (
    DailyBar,
    audit_rollovers,
    calendar_for_symbol,
    expected_win_expiries,
    infer_continuous_method,
)


def _bar(session_date: str, open_: float, close: float) -> DailyBar:
    return DailyBar(
        session_date=session_date,
        open=open_,
        high=max(open_, close),
        low=min(open_, close),
        close=close,
        volume=100.0,
        bars=1,
    )


def test_calendario_win_usa_quarta_mais_proxima_do_dia_15_em_meses_pares():
    expiries = expected_win_expiries("2026-01-01", "2026-12-31")

    assert [expiry.isoformat() for expiry in expiries] == [
        "2026-02-18",
        "2026-04-15",
        "2026-06-17",
        "2026-08-12",
        "2026-10-14",
        "2026-12-16",
    ]


def test_vencimento_sem_pregao_mapeia_para_proxima_sessao_e_janela_observada():
    bars = [
        _bar("2022-10-11", 100_000.0, 100_100.0),
        # 12/10/2022 foi a quarta de vencimento, mas não houve sessão.
        _bar("2022-10-13", 103_000.0, 103_200.0),
        _bar("2022-10-14", 103_250.0, 103_300.0),
    ]

    report = audit_rollovers(
        bars,
        expected_expiries=expected_win_expiries("2022-10-01", "2022-10-31"),
        window_sessions=1,
    )

    rollover = report["rollovers"][0]
    assert rollover["contractual_expiry"] == "2022-10-12"
    assert rollover["effective_session"] == "2022-10-13"
    assert rollover["previous_session"] == "2022-10-11"
    assert rollover["overnight_gap_points"] == 2_900.0
    assert report["excluded_sessions"] == [
        "2022-10-11",
        "2022-10-13",
        "2022-10-14",
    ]


def test_descricao_mt5_sem_ajustes_classifica_serie_crua_por_liquidez():
    description = "IBOVESPA MINI - Por Liquidez (WINQ26) - Sem Ajustes"

    assert infer_continuous_method(description) == "liquidity_continuous_unadjusted"


def test_auditor_nao_aplica_calendario_win_ao_wdo_silenciosamente():
    try:
        calendar_for_symbol("WDO$N", "2026-01-01", "2026-12-31")
    except NotImplementedError as exc:
        assert "WDO$N" in str(exc)
    else:
        raise AssertionError("WDO não pode herdar a regra de vencimento do WIN")
