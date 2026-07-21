#!/usr/bin/env python3
"""Captura metadados read-only de séries contínuas em um terminal MT5 Windows.

Executar com o Python Windows que possui ``MetaTrader5`` instalado. O programa
consulta somente ``terminal_info``, ``account_info`` e ``symbol_info``; não
coleta barras, não seleciona símbolos e não altera o banco do IRAI.

Exemplo:

    py -3 scripts/capture_mt5_continuous_metadata.py \
      --terminal E:\\MetaTradersWSL\\wdowin\\irai\\terminal64.exe \
      --output docs/artifacts/irai-5/mt5-continuous-metadata-v1.json
"""

from __future__ import annotations

import argparse
import json
import ntpath
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "irai.mt5-continuous-metadata.v1"
DEFAULT_SYMBOLS = ("WIN$N", "WDO$N")
SYMBOL_FIELDS = (
    "name",
    "description",
    "path",
    "currency_base",
    "currency_profit",
    "trade_mode",
    "visible",
    "start_time",
    "expiration_time",
)


def _value(info: Any, field: str) -> Any:
    return getattr(info, field, None)


def _same_windows_path(left: str | None, right: str) -> bool:
    if not left:
        return False
    return ntpath.normcase(ntpath.normpath(left)) == ntpath.normcase(ntpath.normpath(right))


def _terminal_matches_request(terminal: Any, terminal_path: str) -> bool:
    """Aceita as duas formas que ``terminal_info`` expõe no MT5 portátil.

    No terminal XP portátil, ``terminal_info.path`` é o diretório de dados;
    em outras instalações pode ser o executável. Exigir também ``data_path``
    evita aceitar um terminal distinto só porque ele reporta um diretório.
    """
    expected_data_path = ntpath.dirname(terminal_path)
    reported_path = _value(terminal, "path")
    return (
        _same_windows_path(_value(terminal, "data_path"), expected_data_path)
        and (
            _same_windows_path(reported_path, terminal_path)
            or _same_windows_path(reported_path, expected_data_path)
        )
    )


def collect_metadata(mt5: Any, *, terminal_path: str, symbols: Iterable[str]) -> dict:
    """Consulta o terminal já autenticado e falha fechado sobre identidade/símbolo."""
    if not mt5.initialize(path=terminal_path, timeout=30_000):
        raise RuntimeError(f"mt5.initialize falhou: {mt5.last_error()}")
    try:
        terminal = mt5.terminal_info()
        if terminal is None or not _value(terminal, "connected"):
            raise RuntimeError("terminal MT5 não está conectado")
        if not _terminal_matches_request(terminal, terminal_path):
            raise RuntimeError(
                "terminal MT5 conectado não corresponde ao caminho solicitado: "
                f"path={_value(terminal, 'path')!r}, "
                f"data_path={_value(terminal, 'data_path')!r}, "
                f"solicitado={terminal_path!r}"
            )

        captured_symbols = {}
        for symbol in symbols:
            info = mt5.symbol_info(symbol)
            if info is None:
                raise RuntimeError(f"symbol_info({symbol!r}) não retornou metadados")
            if _value(info, "name") != symbol:
                raise RuntimeError(
                    f"symbol_info({symbol!r}) retornou {_value(info, 'name')!r}"
                )
            description = _value(info, "description")
            if not isinstance(description, str) or not description.strip():
                raise RuntimeError(f"{symbol!r} não possui descrição no MT5")
            captured_symbols[symbol] = {
                field: _value(info, field) for field in SYMBOL_FIELDS
            }

        account = mt5.account_info()
        return {
            "schema_version": SCHEMA_VERSION,
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "terminal": {
                "requested_executable": terminal_path,
                "path": _value(terminal, "path"),
                "data_path": _value(terminal, "data_path"),
                "company": _value(terminal, "company"),
                "connected": bool(_value(terminal, "connected")),
            },
            "account_company": _value(account, "company"),
            "symbols": captured_symbols,
        }
    finally:
        mt5.shutdown()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:  # pragma: no cover - ambiente Linux deliberadamente não importa MT5
        raise RuntimeError("MetaTrader5 só está disponível no Python Windows") from exc
    payload = collect_metadata(mt5, terminal_path=args.terminal, symbols=args.symbols)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Metadados MT5 gravados em {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
