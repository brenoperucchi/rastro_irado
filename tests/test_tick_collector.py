"""Specs do coletor dedicado de ticks WIN (IRAI-20)."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.workers.tick_collector_wsl import (
    TickCollector,
    TickCursor,
    deduplicate_ticks,
    discover_active_win_contract,
    load_state,
    partition_path,
    save_state,
    validate_portable_terminal,
    write_parquet,
)


def _tick(time_msc, *, last=140_000.0, flags=8, volume_real=1.0):
    return {
        "time": time_msc // 1000,
        "time_msc": time_msc,
        "bid": last - 5,
        "ask": last + 5,
        "last": last,
        "volume": int(volume_real),
        "flags": flags,
        "volume_real": volume_real,
    }


def test_descobre_contrato_vigente_na_descricao_da_serie_continua():
    info = {"name": "WIN$N", "description": "IBOVESPA MINI - Por Liquidez (WINQ26) - Sem Ajustes"}

    assert discover_active_win_contract(info) == "WINQ26"


def test_deduplica_ticks_repetidos_e_preserva_negocios_no_mesmo_milissegundo():
    cursor = TickCursor(last_time_msc=1_000, last_keys={"1000|99995|100005|100000|1|8|1"})
    repeated = _tick(1_000, last=100_000.0)
    distinct_same_ms = _tick(1_000, last=100_005.0)
    next_tick = _tick(1_001, last=100_010.0)

    selected, updated = deduplicate_ticks(
        [repeated, distinct_same_ms, next_tick], cursor,
    )

    assert selected == [distinct_same_ms, next_tick]
    assert updated.last_time_msc == 1_001
    assert len(updated.last_keys) == 1


def test_estado_do_cursor_sobrevive_a_restart(tmp_path):
    path = tmp_path / "state.json"
    state = {
        "WIN$N": TickCursor(last_time_msc=123_456, last_keys={"a", "b"}),
        "WINQ26": TickCursor(last_time_msc=123_460, last_keys={"c"}),
    }

    save_state(path, state)
    loaded = load_state(path)

    assert loaded == state
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_particao_parquet_e_deterministica_por_data_e_simbolo(tmp_path):
    path = partition_path(tmp_path, "WIN$N", 1_752_672_600_000)

    assert path == tmp_path / "date=2025-07-16" / "instrument=WIN%24N"


def test_rejeita_terminal_que_nao_esta_no_data_path_portable_dedicado():
    expected = r"E:\MetaTradersWSL\wdowin\ira_ticks\terminal64.exe"
    validate_portable_terminal(
        {"data_path": r"E:\MetaTradersWSL\wdowin\ira_ticks", "connected": True},
        expected,
    )

    with pytest.raises(RuntimeError, match="portable"):
        validate_portable_terminal(
            {"data_path": r"C:\Users\user\AppData\Roaming\MetaQuotes\Terminal\ABC", "connected": True},
            expected,
        )


def test_launcher_declara_flag_portable():
    launcher = Path(__file__).resolve().parents[1] / "scripts" / "systemd" / "start-mt5-portable.ps1"
    content = launcher.read_text(encoding="utf-8")

    assert 'ArgumentList "/portable"' in content


def test_coletor_mantem_uma_conexao_mt5_entre_ciclos(tmp_path, monkeypatch):
    class FakeMT5:
        COPY_TICKS_ALL = 0

        def __init__(self):
            self.initialize_calls = 0
            self.shutdown_calls = 0
            self.tick_time = 1_752_672_600_000

        def initialize(self, **_kwargs):
            self.initialize_calls += 1
            return True

        def terminal_info(self):
            return {
                "data_path": r"E:\MetaTradersWSL\wdowin\ira_ticks",
                "connected": True,
            }

        def symbol_select(self, _symbol, _selected):
            return True

        def symbol_info(self, _symbol):
            return {"description": "IBOVESPA MINI - Por Liquidez (WINQ26) - Sem Ajustes"}

        def copy_ticks_range(self, *_args):
            self.tick_time += 1
            return [_tick(self.tick_time)]

        def shutdown(self):
            self.shutdown_calls += 1

        def last_error(self):
            return (0, "ok")

    fake = FakeMT5()
    writes = []
    monkeypatch.setattr(
        "backend.workers.tick_collector_wsl.write_parquet",
        lambda _root, symbol, ticks: writes.append((symbol, len(ticks))) or [],
    )
    collector = TickCollector(
        fake,
        terminal_path=r"E:\MetaTradersWSL\wdowin\ira_ticks\terminal64.exe",
        output_root=tmp_path,
        initial_backfill_minutes=15,
    )

    collector.cycle()
    collector.cycle()

    assert fake.initialize_calls == 1
    assert fake.shutdown_calls == 0
    assert writes == []  # não cria um arquivo minúsculo a cada poll de 2s

    collector.flush_pending()
    assert sorted(writes) == [("WIN$N", 2), ("WINQ26", 2)]


def test_consulta_ticks_no_relogio_brt_codificado_pelo_broker_xp(tmp_path):
    """A XP devolve epoch no relógio BRT: 12:00 BRT aparece como 12:00 UTC
    no campo bruto, embora o instante real seja 15:00 UTC. Consultar a janela
    UTC atual pergunta pelo futuro do feed e retorna vazio."""
    class FakeMT5:
        COPY_TICKS_ALL = 0

        def __init__(self):
            self.window = None

        def copy_ticks_range(self, _symbol, start, end, _flags):
            self.window = (start, end)
            return []

    fake = FakeMT5()
    collector = TickCollector(
        fake,
        terminal_path=r"E:\MetaTradersWSL\wdowin\ira_ticks\terminal64.exe",
        output_root=tmp_path,
        initial_backfill_minutes=15,
    )
    now = datetime(2026, 7, 16, 15, 0, tzinfo=timezone.utc)

    collector.collect_symbol("WIN$N", now)

    assert fake.window == (
        now - timedelta(hours=3, minutes=15),
        now - timedelta(hours=3),
    )


def test_health_fica_degraded_sem_ticks_durante_pregao_b3(tmp_path):
    class FakeMT5:
        COPY_TICKS_ALL = 0

        def initialize(self, **_kwargs):
            return True

        def terminal_info(self):
            return {
                "data_path": r"E:\MetaTradersWSL\wdowin\ira_ticks",
                "connected": True,
            }

        def symbol_select(self, _symbol, _selected):
            return True

        def symbol_info(self, _symbol):
            return {"description": "IBOVESPA MINI - Por Liquidez (WINQ26) - Sem Ajustes"}

        def copy_ticks_range(self, *_args):
            return []

        def shutdown(self):
            pass

        def last_error(self):
            return (1, "Success")

    collector = TickCollector(
        FakeMT5(),
        terminal_path=r"E:\MetaTradersWSL\wdowin\ira_ticks\terminal64.exe",
        output_root=tmp_path,
        initial_backfill_minutes=15,
    )

    health = collector.cycle(
        now=datetime(2026, 7, 16, 15, 0, tzinfo=timezone.utc),
    )

    assert health["b3_session_open"] is True
    assert health["status"] == "degraded"
    assert health["degraded_reason"] == "no_ticks_during_b3_session"


def test_parquet_preserva_schema_e_conteudo(tmp_path):
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    row = {
        "symbol": "WIN$N",
        **_tick(1_752_672_600_000),
        "collected_at": "2025-07-16T12:10:00+00:00",
    }
    files = write_parquet(tmp_path, "WIN$N", [row])

    assert len(files) == 1
    table = pq.ParquetFile(files[0]).read()
    assert table.column_names == [
        "symbol", "time", "time_msc", "bid", "ask", "last", "volume",
        "flags", "volume_real", "collected_at",
    ]
    assert table.to_pylist()[0]["last"] == 140_000.0
