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
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backfill_gex_history import (
    assemble_ibov_options,
    audit_existing_sessions,
    decide_persistence,
    gex_validity_reasons,
    ensure_safe_sqlite_runtime,
    next_effective_win_session,
    parse_equity_premiums,
    parse_ibov_open_interest,
    parse_ibov_spot,
    parse_win_front_settle,
    rate_at_or_before,
    open_backfill_database,
)


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

    with pytest.raises(ValueError, match="market_bars.*gex_levels"):
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
        "gamma_flip_not_between_extrema",
        "gamma_flip_too_far_from_spot",
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
    conn.execute(
        """CREATE TABLE gex_levels(
               session_date TEXT, target TEXT, valid INTEGER,
               gamma_max REAL, gamma_flip REAL, gamma_min REAL,
               walls TEXT, meta TEXT
           )"""
    )
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
        "INSERT INTO gex_levels VALUES (?,?,?,?,?,?,?,?)",
        ("2026-07-15", "WIN$N", 1, 190000, 185000, 175000,
         json.dumps(walls), json.dumps(meta)),
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
