"""Specs da captura read-only de metadados de séries contínuas do MT5."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.capture_mt5_continuous_metadata import collect_metadata


TERMINAL = r"E:\MetaTradersWSL\wdowin\irai\terminal64.exe"


class FakeMt5:
    def __init__(
        self,
        *,
        connected=True,
        description="DOLAR MINI - Por Liquidez - Sem Ajustes",
        reported_path=TERMINAL,
    ):
        self.connected = connected
        self.description = description
        self.reported_path = reported_path
        self.shutdown_called = False

    def initialize(self, **kwargs):
        self.initialize_kwargs = kwargs
        return True

    def last_error(self):
        return (0, "ok")

    def terminal_info(self):
        return SimpleNamespace(
            path=self.reported_path,
            data_path=r"E:\MetaTradersWSL\wdowin\irai",
            company="MetaQuotes Software Corp.",
            connected=self.connected,
        )

    def account_info(self):
        return SimpleNamespace(company="XP Investimentos CCTVM S/A")

    def symbol_info(self, symbol):
        return SimpleNamespace(
            name=symbol,
            description=self.description,
            path=f"BMF\\SERIES CONTINUAS\\{symbol}",
            currency_base="BRL",
            currency_profit="BRL",
            trade_mode=0,
            visible=True,
            start_time=0,
            expiration_time=0,
        )

    def shutdown(self):
        self.shutdown_called = True


def test_captura_mt5_registra_identidade_do_terminal_e_series_sem_escrever_barras():
    mt5 = FakeMt5()

    report = collect_metadata(mt5, terminal_path=TERMINAL, symbols=["WDO$N"])

    assert mt5.initialize_kwargs == {"path": TERMINAL, "timeout": 30_000}
    assert mt5.shutdown_called
    assert report["terminal"]["connected"] is True
    assert report["terminal"]["requested_executable"] == TERMINAL
    assert report["symbols"]["WDO$N"]["description"].endswith("Sem Ajustes")


def test_captura_mt5_aceita_path_do_diretorio_em_terminal_portable():
    mt5 = FakeMt5(reported_path=r"E:\MetaTradersWSL\wdowin\irai")

    report = collect_metadata(mt5, terminal_path=TERMINAL, symbols=["WIN$N"])

    assert report["terminal"]["path"] == r"E:\MetaTradersWSL\wdowin\irai"


def test_captura_mt5_falha_fechado_quando_terminal_nao_esta_conectado():
    mt5 = FakeMt5(connected=False)

    try:
        collect_metadata(mt5, terminal_path=TERMINAL, symbols=["WDO$N"])
    except RuntimeError as exc:
        assert "não está conectado" in str(exc)
    else:
        raise AssertionError("captura não pode aceitar terminal desconectado")
    assert mt5.shutdown_called
