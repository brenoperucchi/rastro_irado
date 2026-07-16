"""Regressões do backfill causal de GEX histórico do WIN.

As fixtures representam somente os campos usados dos arquivos oficiais B3.
EOD de D nunca pode ser associado à própria sessão D: a primeira sessão
operável é o próximo pregão WIN presente no ledger de mercado.
"""

import io
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backfill_gex_history import (
    assemble_ibov_options,
    audit_existing_sessions,
    decide_persistence,
    gex_validity_reasons,
    gex_diagnostic_warnings,
    ensure_history_schema,
    ensure_safe_sqlite_runtime,
    next_effective_win_session,
    parse_equity_premiums,
    parse_ibov_open_interest,
    parse_ibov_spot,
    parse_win_front_settle,
    rate_at_or_before,
    save_history_result,
    migrate_historical_rows_from_live,
    reclassify_history_validity,
    open_backfill_database,
    process_session,
)
from backend import gex_official


PRICE_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<Document xmlns="urn:bvmf.052.01.xsd"><Xchg>
  <BizGrp><Document xmlns="urn:bvmf.217.01.xsd"><PricRpt>
    <SctyId><TckrSymb>IBOVG140</TckrSymb></SctyId>
    <FinInstrmAttrbts><OpnIntrst>1200</OpnIntrst></FinInstrmAttrbts>
  </PricRpt></Document></BizGrp>
  <BizGrp><Document xmlns="urn:bvmf.217.01.xsd"><PricRpt>
    <SctyId><TckrSymb>IBOVS139</TckrSymb></SctyId>
    <FinInstrmAttrbts><OpnIntrst>800</OpnIntrst></FinInstrmAttrbts>
  </PricRpt></Document></BizGrp>
  <BizGrp><Document xmlns="urn:bvmf.217.01.xsd"><PricRpt>
    <SctyId><TckrSymb>PETRA40</TckrSymb></SctyId>
    <FinInstrmAttrbts><OpnIntrst>9999</OpnIntrst></FinInstrmAttrbts>
  </PricRpt></Document></BizGrp>
</Xchg></Document>"""


DERIVATIVE_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<Document xmlns="urn:bvmf.052.01.xsd"><Xchg>
  <BizGrp><Document xmlns="urn:bvmf.217.01.xsd"><PricRpt>
    <SctyId><TckrSymb>WINQ26</TckrSymb></SctyId>
    <TradDtls><RglrTxsQty>250</RglrTxsQty></TradDtls>
    <FinInstrmAttrbts><AdjstdQt>181250</AdjstdQt><OpnIntrst>5000</OpnIntrst></FinInstrmAttrbts>
  </PricRpt></Document></BizGrp>
  <BizGrp><Document xmlns="urn:bvmf.217.01.xsd"><PricRpt>
    <SctyId><TckrSymb>WINV26</TckrSymb></SctyId>
    <TradDtls><RglrTxsQty>4</RglrTxsQty></TradDtls>
    <FinInstrmAttrbts><AdjstdQt>183500</AdjstdQt><OpnIntrst>8000</OpnIntrst></FinInstrmAttrbts>
  </PricRpt></Document></BizGrp>
</Xchg></Document>"""


INDEX_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<Document xmlns="urn:bvmf.087.01.xsd"><IndxInf>
  <SctyInf><SctyId><TckrSymb>IBOV</TckrSymb></SctyId></SctyInf>
  <ClsgPric Ccy="BRL">178432.17</ClsgPric><IndxVal Ccy="BRL">178432.17</IndxVal>
</IndxInf></Document>"""


def test_parsers_oficiais_recuperam_oi_spot_e_contrato_win_mais_negociado():
    assert parse_ibov_open_interest(io.BytesIO(PRICE_XML)) == {
        "IBOVG140": 1200.0,
        "IBOVS139": 800.0,
    }
    assert parse_ibov_spot(io.BytesIO(INDEX_XML)) == pytest.approx(178432.17)
    settle = parse_win_front_settle(io.BytesIO(DERIVATIVE_XML))
    assert settle == {
        "ticker": "WINQ26",
        "settle": 181250.0,
        "trades": 250,
        "open_interest": 5000.0,
    }


def test_premio_b3_mapeia_call_put_e_join_descarta_serie_sem_oi():
    raw = io.StringIO(
        "20260715\n"
        "IBOVG140;C;E;20260819;140000.0;40250.0;22.1\n"
        "IBOVS139;V;E;20260819;139000.0;115.0;19.4\n"
        "IBOVG141;C;E;20260819;141000.0;39000.0;21.8\n"
        "PETRA40;C;A;20260821;40.0;2.0;30.0\n"
    )
    premiums = parse_equity_premiums(raw)
    options = assemble_ibov_options(
        {"IBOVG140": 1200.0, "IBOVS139": 800.0}, premiums,
    )
    assert options == [
        {
            "ticker": "IBOVG140", "oi": 1200.0, "strike": 140000.0,
            "is_call": True, "expiry": "2026-08-19", "premium": 40250.0,
        },
        {
            "ticker": "IBOVS139", "oi": 800.0, "strike": 139000.0,
            "is_call": False, "expiry": "2026-08-19", "premium": 115.0,
        },
    ]


def test_eod_fica_disponivel_somente_no_proximo_pregao_win():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE market_bars(symbol TEXT, timeframe TEXT, timestamp_utc TEXT)")
    conn.executemany(
        "INSERT INTO market_bars VALUES ('WIN$N','M5',?)",
        [("2026-07-15T09:00:00Z",), ("2026-07-17T09:00:00Z",)],
    )
    assert next_effective_win_session(conn, "2026-07-15") == "2026-07-17"
    assert next_effective_win_session(conn, "2026-07-17") is None


def test_backfill_recusa_caminho_inexistente_sem_criar_sqlite_vazio(tmp_path):
    missing = tmp_path / "caminho-errado.db"

    with pytest.raises(ValueError, match="base IRAI não existe"):
        open_backfill_database(missing)

    assert not missing.exists()


def test_backfill_recusa_sqlite_sem_tabelas_de_producao(tmp_path):
    empty = tmp_path / "vazia.db"
    sqlite3.connect(empty).close()

    with pytest.raises(ValueError, match="market_bars"):
        open_backfill_database(empty)


def test_backfill_linux_recusa_sqlite_hospedado_em_drvfs_windows():
    with pytest.raises(ValueError, match="Python do Windows"):
        ensure_safe_sqlite_runtime(
            Path("/mnt/c/Users/teste/rastro_irado/data/irai.db"),
            platform="linux",
        )


def test_backfill_windows_aceita_sqlite_no_volume_windows():
    ensure_safe_sqlite_runtime(
        Path("C:/Users/teste/rastro_irado/data/irai.db"),
        platform="win32",
    )


def test_auditoria_gex_explica_todos_os_gates_reprovados():
    result = {
        "spot": 170_000.0,
        "gamma_min_ibov": 165_000.0,
        "gamma_flip_ibov": None,
        "gamma_max_ibov": 180_000.0,
        "liquid_strikes": 7,
    }

    assert gex_validity_reasons(result, grid_step=1_000.0) == [
        "missing_gamma_flip",
        "insufficient_liquid_strikes",
    ]

    result.update(gamma_flip_ibov=190_000.0, liquid_strikes=12)
    assert gex_validity_reasons(result, grid_step=1_000.0) == [
        "gamma_flip_too_far_from_spot",
    ]
    assert gex_diagnostic_warnings(result) == [
        "gamma_flip_not_between_pointwise_extrema",
    ]


def test_flip_fora_dos_extremos_nao_e_motivo_de_rejeicao_do_backfill():
    result = {
        "spot": 176_000.0,
        "gamma_min_ibov": 170_000.0,
        "gamma_flip_ibov": 184_000.0,
        "gamma_max_ibov": 180_000.0,
        "liquid_strikes": 12,
    }

    assert gex_validity_reasons(result, grid_step=1_000.0) == []
    assert gex_diagnostic_warnings(result) == [
        "gamma_flip_not_between_pointwise_extrema",
    ]


def test_auditoria_gex_valido_nao_inventa_motivo():
    result = {
        "spot": 170_000.0,
        "gamma_min_ibov": 165_000.0,
        "gamma_flip_ibov": 171_000.0,
        "gamma_max_ibov": 180_000.0,
        "liquid_strikes": 8,
    }

    assert gex_validity_reasons(result, grid_step=1_000.0) == []


def test_auditoria_existente_consolida_proveniencia_sem_recalcular():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_history_schema(conn)
    meta = {
        "effective_session_date": "2026-07-16",
        "validity_reasons": [],
        "source_counts": {"joined_series": 789},
        "source_files": {
            name: {"sha256": name * 8}
            for name in ("equities", "derivatives", "premiums", "index")
        },
        "win_contract": {"ticker": "WINQ26"},
    }
    walls = [{"type": "wall"}, {"type": "mid_wall"}]
    conn.execute(
        """INSERT INTO gex_history_levels
           (source_session_date, effective_session_date, target, valid,
            gamma_max, gamma_flip, gamma_min, walls, meta)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("2026-07-15", "2026-07-16", "WIN$N", 1, 190000, 185000,
         175000, json.dumps(walls), json.dumps(meta)),
    )

    rows = audit_existing_sessions(conn, [("2026-07-15", "2026-07-16")])

    assert rows == [{
        "source_session_date": "2026-07-15",
        "effective_session_date": "2026-07-16",
        "action": "audit_existing",
        "valid": True,
        "validity_reasons": [],
        "gamma_max": 190000.0,
        "gamma_flip": 185000.0,
        "gamma_min": 175000.0,
        "wall_count": 1,
        "mid_wall_count": 1,
        "counts": {"joined_series": 789},
        "win_contract": "WINQ26",
        "provenance_complete": True,
    }]


def test_backfill_historico_nunca_sobrescreve_tabela_gex_live():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE gex_levels(
               session_date TEXT, target TEXT, valid INTEGER,
               gamma_max REAL, gamma_flip REAL, gamma_min REAL,
               PRIMARY KEY(session_date, target)
           )"""
    )
    conn.execute(
        "INSERT INTO gex_levels VALUES (?,?,?,?,?,?)",
        ("2026-07-15", "WIN$N", 0, 182497.0, 186421.0, 171806.0),
    )
    historical = {
        "gamma_max": 191863.0, "gamma_min": 171806.0, "gamma_flip": 186364.0,
        "gamma_max_ibov": 189885.0, "gamma_min_ibov": 170034.0,
        "gamma_flip_ibov": 184443.0, "spot": 176010.9,
        "future_settle": 177844.0, "conv_factor": 1.0104,
        "n_strikes": 97, "valid": True, "walls": [],
        "meta": {"source_session_date": "2026-07-15",
                 "effective_session_date": "2026-07-16"},
    }

    save_history_result(
        conn, "2026-07-15", "2026-07-16", historical, target="WIN$N",
    )

    live = conn.execute(
        "SELECT valid, gamma_max, gamma_flip FROM gex_levels"
    ).fetchone()
    history = conn.execute(
        """SELECT valid, gamma_max, gamma_flip, effective_session_date
           FROM gex_history_levels"""
    ).fetchone()
    assert tuple(live) == (0, 182497.0, 186421.0)
    assert tuple(history) == (1, 191863.0, 186364.0, "2026-07-16")


def test_migracao_remove_somente_backfill_legado_da_tabela_live():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE gex_levels(
               session_date TEXT, target TEXT, gamma_max REAL, gamma_min REAL,
               gamma_flip REAL, gamma_max_ibov REAL, gamma_min_ibov REAL,
               gamma_flip_ibov REAL, spot REAL, future_settle REAL,
               conv_factor REAL, n_strikes INTEGER, valid INTEGER,
               walls TEXT, meta TEXT, computed_at TEXT,
               PRIMARY KEY(session_date, target)
           )"""
    )
    historical_meta = json.dumps({
        "effective_session_date": "2026-07-16",
        "source_files": {"equities": {"sha256": "abc"}},
    })
    conn.executemany(
        "INSERT INTO gex_levels VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("2026-07-15", "WIN$N", 191863, 171806, 186364,
             189885, 170034, 184443, 176010, 177844, 1.0104, 97, 1,
             "[]", historical_meta, "2026-07-16T12:00:00Z"),
            ("2026-07-15", "WDO$N", 5601, 4995, None,
             5602, 4996, None, 5099, 5098, 0.9998, 72, 0,
             "[]", json.dumps({"iv_source": "mt5"}), "2026-07-16T11:34:00Z"),
        ],
    )

    assert migrate_historical_rows_from_live(conn) == 1
    assert conn.execute(
        "SELECT session_date, target FROM gex_levels"
    ).fetchall() == [("2026-07-15", "WDO$N")]
    assert conn.execute(
        """SELECT source_session_date, effective_session_date, target
           FROM gex_history_levels"""
    ).fetchall() == [("2026-07-15", "2026-07-16", "WIN$N")]


def test_reclassificacao_promove_somente_reprovacao_por_ordem_flip_extremos():
    conn = sqlite3.connect(":memory:")
    ensure_history_schema(conn)

    def insert(source, spot, gmin, flip, gmax, reasons):
        meta = {
            "grid_step": 1_000.0,
            "liquid_strikes": 12,
            "validity_reasons": reasons,
        }
        conn.execute(
            """INSERT INTO gex_history_levels
               (source_session_date, effective_session_date, target, valid,
                gamma_max_ibov, gamma_min_ibov, gamma_flip_ibov, spot, meta)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (source, "2026-07-16", "WIN$N", 0, gmax, gmin, flip, spot,
             json.dumps(meta)),
        )

    insert("2026-07-13", 176_000, 170_000, 184_000, 180_000,
           ["gamma_flip_not_between_extrema"])
    insert("2026-07-14", 176_000, 170_000, 196_000, 180_000,
           ["gamma_flip_not_between_extrema", "gamma_flip_too_far_from_spot"])
    insert("2026-07-15", 176_000, 170_000, None, 180_000,
           ["missing_gamma_flip"])
    conn.commit()

    report = reclassify_history_validity(conn)

    assert report == {
        "sessions": 3, "valid_before": 0, "valid_after": 1,
        "promoted": 1, "demoted": 0,
    }
    rows = conn.execute(
        "SELECT source_session_date, valid, meta FROM gex_history_levels ORDER BY 1"
    ).fetchall()
    assert [row[1] for row in rows] == [1, 0, 0]
    promoted_meta = json.loads(rows[0][2])
    assert promoted_meta["validity_reasons"] == []
    assert promoted_meta["diagnostic_warnings"] == [
        "gamma_flip_not_between_pointwise_extrema",
    ]


def test_selic_nunca_busca_taxa_futura_para_preencher_falha():
    rates = {"2026-07-14": 0.149, "2026-07-16": 0.150}
    assert rate_at_or_before(rates, "2026-07-15") == ("2026-07-14", 0.149)
    with pytest.raises(ValueError, match="indisponível"):
        rate_at_or_before(rates, "2026-07-13")


@pytest.mark.parametrize(
    "existing_valid,candidate_valid,replace,expected",
    [
        (None, False, False, "insert_invalid"),
        (None, True, False, "insert_valid"),
        (True, True, False, "skip_existing_valid"),
        (True, False, False, "skip_existing_valid"),
        (False, True, False, "replace_with_valid"),
        (False, False, False, "skip_existing_invalid"),
        (True, False, True, "replace_forced"),
    ],
)
def test_politica_idempotente_nao_sobrescreve_valido_silenciosamente(
    existing_valid, candidate_valid, replace, expected,
):
    assert decide_persistence(existing_valid, candidate_valid, replace=replace) == expected


def test_live_e_backfill_compartilham_exatamente_o_mesmo_snapshot_oficial(monkeypatch, tmp_path):
    """Regressão da divergência LIVE BDI/MT5 vs. backfill SPRE/PE/IR/SPRD.

    O backfill não pode reconstruir sua própria perna oficial em paralelo ao
    LIVE: ambos precisam chamar a mesma implementação, para que os mesmos
    arquivos/hashes/Selic gerem níveis, validade e walls idênticos.
    """
    from backend.workers import gex_worker as worker

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_history_schema(conn)
    expected = {
        "gamma_max": 191863.0,
        "gamma_min": 171806.0,
        "gamma_flip": 186364.0,
        "gamma_max_ibov": 189885.0,
        "gamma_min_ibov": 170034.0,
        "gamma_flip_ibov": 184443.0,
        "spot": 176010.9,
        "future_settle": 177844.0,
        "conv_factor": 1.0104,
        "n_strikes": 97,
        "liquid_strikes": 12,
        "valid": True,
        "walls": [{"type": "wall", "price": 177000}],
        "meta": {
            "source_session_date": "2026-07-15",
            "effective_session_date": "2026-07-16",
            "source_files": {
                name: {"name": name, "sha256": name * 8, "retrieved_at": "2026-07-16T07:00:00Z"}
                for name in ("equities", "derivatives", "premiums", "index")
            },
            "source_counts": {"oi_series": 789, "premium_series": 789, "joined_series": 789},
            "win_contract": {"ticker": "WINQ26", "settle": 177844.0},
            "risk_free_source": "BCB SGS 1178",
            "risk_free_source_date": "2026-07-15",
            "risk_free": 0.149,
            "validity_reasons": [],
            "diagnostic_warnings": [],
        },
    }
    calls = []

    def shared_snapshot(source, effective, risk_free, rate_source, *, cache_dir):
        calls.append((source, effective, risk_free, rate_source, cache_dir))
        return expected

    monkeypatch.setattr(worker, "compute_official_win_snapshot", shared_snapshot, raising=False)

    row = process_session(
        conn,
        "2026-07-15",
        "2026-07-16",
        0.149,
        "2026-07-15",
        cache_dir=tmp_path,
        replace=False,
        dry_run=False,
    )

    assert calls == [("2026-07-15", "2026-07-16", 0.149, "2026-07-15", tmp_path)]
    stored = conn.execute(
        "SELECT gamma_max, gamma_flip, gamma_min, valid, walls, meta FROM gex_history_levels"
    ).fetchone()
    assert tuple(stored[:4]) == (191863.0, 186364.0, 171806.0, 1)
    assert json.loads(stored[4]) == expected["walls"]
    assert json.loads(stored[5]) == expected["meta"]
    assert row["valid"] is True


def _bundle_fixture(tmp_path, filename_date="260715", internal_date="2026-07-15", count=50):
    paths = {
        "equities": tmp_path / f"SPRE{filename_date}.zip",
        "derivatives": tmp_path / f"SPRD{filename_date}.zip",
        "premiums": tmp_path / f"PE{filename_date}.ex_",
        "index": tmp_path / f"IR{filename_date}.zip",
    }
    compact = internal_date.replace("-", "")
    reports = "".join(
        f"<PricRpt><TckrSymb>IBOVX{i:03}</TckrSymb><OpnIntrst>100</OpnIntrst></PricRpt>"
        for i in range(count)
    )
    equities = (
        f"<Document><CreDtAndTm>{internal_date}T20:00:00</CreDtAndTm>{reports}</Document>"
    ).encode()
    derivatives = (
        f"<Document><CreDtAndTm>{internal_date}T20:00:00</CreDtAndTm>"
        "<PricRpt><TckrSymb>WINQ26</TckrSymb><AdjstdQt>177844</AdjstdQt>"
        "<RglrTxsQty>100</RglrTxsQty><OpnIntrst>1000</OpnIntrst></PricRpt></Document>"
    ).encode()
    index = (
        f"<Document><CreDtAndTm>{internal_date}T20:00:00</CreDtAndTm>"
        "<IndxInf><TckrSymb>IBOV</TckrSymb><ClsgPric>176010.9</ClsgPric></IndxInf></Document>"
    ).encode()
    premiums = (compact + "\n" + "".join(
        f"IBOVX{i:03};{'C' if i >= count // 2 else 'V'};E;20260819;"
        f"{151000 + i * 1000}.0;5000.0;20.0\n"
        for i in range(count)
    )).encode("latin-1")
    for kind, payload in {
        "equities": equities, "derivatives": derivatives,
        "premiums": premiums, "index": index,
    }.items():
        with zipfile.ZipFile(paths[kind], "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{kind}-{internal_date}.txt", payload)
    return paths


def test_bundle_com_nome_correto_e_data_interna_adulterada_e_rejeitado(tmp_path):
    paths = _bundle_fixture(tmp_path, internal_date="2020-01-02")

    with pytest.raises(ValueError, match="data.*2020-01-02.*2026-07-15"):
        gex_official.parse_official_bundle(paths, "2026-07-15")


def test_bundle_rejeita_data_divergente_em_apenas_um_dos_quatro_arquivos(tmp_path):
    paths = _bundle_fixture(tmp_path)
    with zipfile.ZipFile(paths["index"], "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "index.xml",
            "<Document><CreDtAndTm>2026-07-14T20:00:00</CreDtAndTm>"
            "<IndxInf><TckrSymb>IBOV</TckrSymb><ClsgPric>176010.9</ClsgPric>"
            "</IndxInf></Document>",
        )

    with pytest.raises(ValueError, match="index=2026-07-14"):
        gex_official.parse_official_bundle(paths, "2026-07-15")


def test_proveniencia_dos_mesmos_bytes_independe_do_mtime(tmp_path):
    paths = _bundle_fixture(tmp_path)
    first = gex_official.source_file_provenance(paths)
    for path in paths.values():
        os.utime(path, (1_700_000_000, 1_700_000_000))
    second = gex_official.source_file_provenance(paths)

    assert first == second
    assert all("retrieved_at" not in item for item in first.values())


def test_integracao_live_backfill_com_bundle_real_de_fixture_e_byte_identica(tmp_path):
    from backend.workers import gex_worker as worker

    source, effective = "2026-07-15", "2026-07-16"
    cache = tmp_path / "cache"
    bundle_dir = cache / source
    bundle_dir.mkdir(parents=True)
    _bundle_fixture(bundle_dir)

    live = worker.compute_official_win_snapshot(
        source, effective, 0.1415, source, cache_dir=cache,
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_history_schema(conn)
    process_session(
        conn, source, effective, 0.1415, source,
        cache_dir=cache, replace=False, dry_run=False,
    )
    stored = conn.execute(
        """SELECT gamma_max, gamma_min, gamma_flip, valid, walls, meta
           FROM gex_history_levels WHERE source_session_date=?""",
        (source,),
    ).fetchone()

    assert tuple(stored[:4]) == (
        live["gamma_max"], live["gamma_min"], live["gamma_flip"], int(live["valid"]),
    )
    assert json.loads(stored[4]) == live["walls"]
    assert json.loads(stored[5]) == live["meta"]
    assert len(live["meta"]["source_files"]) == 4
    assert all(item["sha256"] for item in live["meta"]["source_files"].values())
