#!/usr/bin/env python3
"""Coletor dedicado de ticks WIN para execução e backtest intrabar.

Windows-only: usa o pacote MetaTrader5 do Python 3.12, iniciado pelo wrapper
systemd depois que o terminal dedicado foi aberto com ``/portable``.

Persistência:
  data/ticks/win/date=YYYY-MM-DD/symbol=WIN%24N/part-*.parquet

O cursor atômico guarda o último ``time_msc`` e todas as identidades vistas
naquele milissegundo. Isso preserva negócios distintos com o mesmo timestamp e
remove a borda inclusiva devolvida novamente por ``copy_ticks_range``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import ntpath
import os
import re
import signal
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote


SCHEMA_VERSION = 1
DEFAULT_TERMINAL = r"E:\MetaTradersWSL\wdowin\ira_ticks\terminal64.exe"
DEFAULT_OUTPUT_ROOT = "data/ticks/win"
CONTINUOUS_SYMBOL = "WIN$N"
WIN_CONTRACT_RE = re.compile(r"\b(WIN[A-Z]\d{2})\b", re.IGNORECASE)

log = logging.getLogger("irai.tick_collector")


@dataclass(frozen=True)
class TickCursor:
    last_time_msc: int = 0
    last_keys: set[str] | None = None

    def __post_init__(self):
        if self.last_keys is None:
            object.__setattr__(self, "last_keys", set())


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "_asdict"):
        return dict(value._asdict())
    return {
        name: getattr(value, name)
        for name in dir(value)
        if not name.startswith("_") and not callable(getattr(value, name))
    }


def discover_active_win_contract(symbol_info: Any) -> str | None:
    """Extrai ``WINQ26`` da descrição broker da série por liquidez."""
    info = _mapping(symbol_info)
    haystack = " ".join(str(info.get(key) or "") for key in ("name", "description", "path"))
    match = WIN_CONTRACT_RE.search(haystack)
    return match.group(1).upper() if match else None


def _number_key(value: Any) -> str:
    if value is None:
        return "0"
    return format(float(value), ".15g")


def tick_identity(tick: Mapping[str, Any]) -> str:
    fields = (
        str(int(tick.get("time_msc") or 0)),
        _number_key(tick.get("bid")),
        _number_key(tick.get("ask")),
        _number_key(tick.get("last")),
        _number_key(tick.get("volume")),
        str(int(tick.get("flags") or 0)),
        _number_key(tick.get("volume_real")),
    )
    return "|".join(fields)


def deduplicate_ticks(
    ticks: Sequence[dict[str, Any]], cursor: TickCursor,
) -> tuple[list[dict[str, Any]], TickCursor]:
    """Remove a borda já persistida sem colapsar eventos legítimos no mesmo ms."""
    selected: list[dict[str, Any]] = []
    seen_in_batch: set[str] = set()
    boundary_keys = set(cursor.last_keys or set())

    for tick in ticks:
        time_msc = int(tick.get("time_msc") or 0)
        if time_msc < cursor.last_time_msc:
            continue
        key = tick_identity(tick)
        if key in seen_in_batch:
            continue
        seen_in_batch.add(key)
        if time_msc == cursor.last_time_msc and key in boundary_keys:
            continue
        selected.append(tick)

    if not selected:
        return [], TickCursor(cursor.last_time_msc, set(boundary_keys))

    max_time = max(int(tick["time_msc"]) for tick in selected)
    max_keys = {
        tick_identity(tick)
        for tick in selected
        if int(tick["time_msc"]) == max_time
    }
    if max_time == cursor.last_time_msc:
        max_keys.update(boundary_keys)
    return selected, TickCursor(max_time, max_keys)


def load_state(path: Path) -> dict[str, TickCursor]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError(f"schema de cursor incompatível em {path}")
    return {
        symbol: TickCursor(
            last_time_msc=int(value.get("last_time_msc") or 0),
            last_keys=set(value.get("last_keys") or []),
        )
        for symbol, value in payload.get("symbols", {}).items()
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def save_state(path: Path, state: Mapping[str, TickCursor]) -> None:
    _atomic_json(path, {
        "schema_version": SCHEMA_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbols": {
            symbol: {
                "last_time_msc": cursor.last_time_msc,
                "last_keys": sorted(cursor.last_keys or set()),
            }
            for symbol, cursor in sorted(state.items())
        },
    })


def partition_path(root: Path, symbol: str, time_msc: int) -> Path:
    session_date = datetime.fromtimestamp(time_msc / 1000, timezone.utc).date().isoformat()
    return root / f"date={session_date}" / f"symbol={quote(symbol, safe='')}"


def validate_portable_terminal(terminal_info: Any, terminal_path: str) -> dict[str, Any]:
    """Falha fechado se o MT5 conectado não usa o data_path do terminal dedicado."""
    info = _mapping(terminal_info)
    expected_data_path = ntpath.normcase(ntpath.normpath(ntpath.dirname(terminal_path)))
    actual_data_path = ntpath.normcase(ntpath.normpath(str(info.get("data_path") or "")))
    if actual_data_path != expected_data_path:
        raise RuntimeError(
            "terminal não está em modo portable dedicado: "
            f"data_path={actual_data_path!r}, esperado={expected_data_path!r}"
        )
    if not info.get("connected"):
        raise RuntimeError("terminal MT5 portable está desconectado")
    return info


def _scalar(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else value


def normalize_ticks(raw_ticks: Any, symbol: str, collected_at: str) -> list[dict[str, Any]]:
    if raw_ticks is None:
        return []
    names = getattr(getattr(raw_ticks, "dtype", None), "names", None)
    result = []
    for raw in raw_ticks:
        if names:
            tick = {name: _scalar(raw[name]) for name in names}
        elif isinstance(raw, Mapping):
            tick = dict(raw)
        elif hasattr(raw, "_asdict"):
            tick = dict(raw._asdict())
        else:
            raise TypeError(f"formato de tick não suportado: {type(raw)!r}")
        time_msc = int(tick.get("time_msc") or int(tick.get("time") or 0) * 1000)
        result.append({
            "symbol": symbol,
            "time": int(tick.get("time") or time_msc // 1000),
            "time_msc": time_msc,
            "bid": float(tick.get("bid") or 0.0),
            "ask": float(tick.get("ask") or 0.0),
            "last": float(tick.get("last") or 0.0),
            "volume": int(tick.get("volume") or 0),
            "flags": int(tick.get("flags") or 0),
            "volume_real": float(tick.get("volume_real") or 0.0),
            "collected_at": collected_at,
        })
    return result


def _parquet_schema(pa):
    return pa.schema([
        ("symbol", pa.string()),
        ("time", pa.int64()),
        ("time_msc", pa.int64()),
        ("bid", pa.float64()),
        ("ask", pa.float64()),
        ("last", pa.float64()),
        ("volume", pa.int64()),
        ("flags", pa.int32()),
        ("volume_real", pa.float64()),
        ("collected_at", pa.string()),
    ])


def write_parquet(root: Path, symbol: str, ticks: Sequence[dict[str, Any]]) -> list[Path]:
    """Escreve um chunk atômico por partição UTC; arquivo é content-addressed."""
    if not ticks:
        return []
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("pyarrow é obrigatório para persistir ticks em Parquet") from exc

    grouped: dict[Path, list[dict[str, Any]]] = {}
    for tick in ticks:
        grouped.setdefault(partition_path(root, symbol, int(tick["time_msc"])), []).append(tick)

    written = []
    for directory, rows in grouped.items():
        directory.mkdir(parents=True, exist_ok=True)
        digest_input = "\n".join(tick_identity(row) for row in rows).encode("utf-8")
        digest = hashlib.sha256(digest_input).hexdigest()[:16]
        first_ms = min(int(row["time_msc"]) for row in rows)
        last_ms = max(int(row["time_msc"]) for row in rows)
        destination = directory / f"part-{first_ms}-{last_ms}-{digest}.parquet"
        temporary = destination.with_suffix(".parquet.tmp")
        table = pa.Table.from_pylist(list(rows), schema=_parquet_schema(pa))
        pq.write_table(table, temporary, compression="zstd")
        os.replace(temporary, destination)
        written.append(destination)
    return written


class TickCollector:
    def __init__(
        self,
        mt5,
        *,
        terminal_path: str,
        output_root: Path,
        initial_backfill_minutes: int,
    ):
        self.mt5 = mt5
        self.terminal_path = terminal_path
        self.output_root = output_root
        self.initial_backfill_minutes = initial_backfill_minutes
        self.state_path = output_root / "state.json"
        self.health_path = output_root / "health.json"
        self.state = load_state(self.state_path)
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.total_written = 0
        self._connected = False

    def connect(self) -> tuple[dict[str, Any], list[str]]:
        if not self._connected:
            if not self.mt5.initialize(path=self.terminal_path, timeout=60_000):
                raise RuntimeError(f"mt5.initialize falhou: {self.mt5.last_error()}")
            self._connected = True
        terminal = validate_portable_terminal(self.mt5.terminal_info(), self.terminal_path)
        if not self.mt5.symbol_select(CONTINUOUS_SYMBOL, True):
            raise RuntimeError(f"symbol_select({CONTINUOUS_SYMBOL}) falhou: {self.mt5.last_error()}")
        continuous_info = self.mt5.symbol_info(CONTINUOUS_SYMBOL)
        contract = discover_active_win_contract(continuous_info)
        symbols = [CONTINUOUS_SYMBOL]
        if contract:
            if self.mt5.symbol_select(contract, True):
                symbols.append(contract)
            else:
                log.warning("contrato %s descoberto, mas indisponível: %s", contract, self.mt5.last_error())
        return terminal, symbols

    def disconnect(self) -> None:
        if self._connected:
            self.mt5.shutdown()
            self._connected = False

    def _from_time(self, symbol: str, now: datetime) -> datetime:
        cursor = self.state.get(symbol, TickCursor())
        if cursor.last_time_msc:
            return datetime.fromtimestamp(max(0, cursor.last_time_msc - 1) / 1000, timezone.utc)
        return now - timedelta(minutes=self.initial_backfill_minutes)

    def collect_symbol(self, symbol: str, now: datetime) -> tuple[int, int, list[str]]:
        collected_at = now.isoformat(timespec="milliseconds")
        raw = self.mt5.copy_ticks_range(
            symbol,
            self._from_time(symbol, now),
            now,
            self.mt5.COPY_TICKS_ALL,
        )
        normalized = normalize_ticks(raw, symbol, collected_at)
        selected, cursor = deduplicate_ticks(normalized, self.state.get(symbol, TickCursor()))
        files = write_parquet(self.output_root, symbol, selected)
        if selected:
            self.state[symbol] = cursor
            save_state(self.state_path, self.state)
            self.total_written += len(selected)
        return len(normalized), len(selected), [str(path) for path in files]

    def cycle(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        terminal, symbols = self.connect()
        results = {}
        for symbol in symbols:
            received, written, files = self.collect_symbol(symbol, now)
            results[symbol] = {
                "received": received,
                "written": written,
                "files": files,
                "last_time_msc": self.state.get(symbol, TickCursor()).last_time_msc,
            }
        health = {
            "schema_version": SCHEMA_VERSION,
            "status": "ok",
            "started_at": self.started_at,
            "checked_at": now.isoformat(timespec="seconds"),
            "terminal_path": self.terminal_path,
            "terminal_data_path": terminal.get("data_path"),
            "portable_validated": True,
            "symbols": symbols,
            "cycle": results,
            "total_written_since_start": self.total_written,
        }
        _atomic_json(self.health_path, health)
        return health

    def record_error(self, exc: Exception) -> None:
        _atomic_json(self.health_path, {
            "schema_version": SCHEMA_VERSION,
            "status": "error",
            "started_at": self.started_at,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "terminal_path": self.terminal_path,
            "error": f"{type(exc).__name__}: {exc}",
            "total_written_since_start": self.total_written,
        })


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal", default=DEFAULT_TERMINAL)
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--initial-backfill-minutes", type=int, default=15)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        import MetaTrader5 as mt5
    except ImportError:
        log.error("MetaTrader5 é obrigatório; execute com o Python Windows 3.12")
        return 2

    stop_requested = False

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    collector = TickCollector(
        mt5,
        terminal_path=args.terminal,
        output_root=Path(args.output_root),
        initial_backfill_minutes=args.initial_backfill_minutes,
    )
    log.info("coletor iniciado: terminal=%s output=%s", args.terminal, args.output_root)
    while not stop_requested:
        try:
            health = collector.cycle()
            summary = ", ".join(
                f"{symbol}=+{row['written']}" for symbol, row in health["cycle"].items()
            )
            log.info("ciclo ok: %s", summary)
        except Exception as exc:  # noqa: BLE001 — serviço deve registrar e tentar novamente
            log.exception("ciclo falhou")
            collector.disconnect()
            collector.record_error(exc)
            if args.once:
                return 1
        if args.once:
            break
        deadline = time.monotonic() + max(0.25, args.poll_seconds)
        while not stop_requested and time.monotonic() < deadline:
            time.sleep(min(0.25, deadline - time.monotonic()))
    collector.disconnect()
    log.info("coletor encerrado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
