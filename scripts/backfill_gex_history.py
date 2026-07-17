#!/usr/bin/env python3
"""Backfill causal dos níveis GEX do WIN com arquivos históricos oficiais.

Fontes por pregão D:
  * SPRE: preço/posição das opções IBOV (OI por ticker);
  * PE: cadastro e prêmio de referência das opções de ações/índices;
  * SPRD: ajuste dos contratos WIN (escolhe o mais negociado);
  * IR: fechamento oficial do IBOV;
  * BCB SGS 1178: Selic anualizada vigente em D.

O snapshot fechado em D só é associado ao próximo pregão WIN existente no
banco. O histórico é persistido em ``gex_history_levels``, separado de
``gex_levels`` — esta última é exclusiva do cálculo LIVE consumido pela API.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterable, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db import DB_PATH, get_connection
from backend import gex_official
from backend.workers import gex_worker as gex


B3_DOWNLOAD_BASE = "https://www.b3.com.br/pesquisapregao/download"
BCB_SELIC_BASE = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.1178/dados"
DEFAULT_CACHE_DIR = Path("data/gex_history_cache")
WIN_CONTRACT_RE = re.compile(r"^WIN[A-Z]\d{2}$")

# Schema e writer de gex_history_levels vivem em backend/workers/gex_worker.py
# (GEX_HISTORY_SCHEMA / save_history_result) — o worker EOD agora grava a
# mesma tabela a cada rodada, então schema/persistência não podem divergir
# entre os dois escritores.
GEX_HISTORY_SCHEMA = gex.GEX_HISTORY_SCHEMA


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _values(element) -> dict[str, str]:
    return {
        _local(child.tag): child.text.strip()
        for child in element.iter()
        if child.text and child.text.strip()
    }


def _iter_elements(stream: BinaryIO, local_name: str):
    for _event, element in ET.iterparse(stream, events=("end",)):
        if _local(element.tag) == local_name:
            yield element
            element.clear()


def parse_ibov_open_interest(stream: BinaryIO) -> dict[str, float]:
    """Lê OI das opções IBOV no BVBG.186/SPRE sem carregar o XML inteiro."""
    out: dict[str, float] = {}
    for report in _iter_elements(stream, "PricRpt"):
        values = _values(report)
        ticker = values.get("TckrSymb", "")
        try:
            oi = float(values.get("OpnIntrst", "0"))
        except ValueError:
            oi = 0.0
        if ticker.startswith("IBOV") and oi > 0:
            out[ticker] = oi
    return out


def parse_win_front_settle(stream: BinaryIO) -> dict:
    """Escolhe causalmente o WIN de maior número de negócios no BVBG.187."""
    candidates = []
    for report in _iter_elements(stream, "PricRpt"):
        values = _values(report)
        ticker = values.get("TckrSymb", "")
        if not WIN_CONTRACT_RE.fullmatch(ticker) or not values.get("AdjstdQt"):
            continue
        try:
            candidates.append({
                "ticker": ticker,
                "settle": float(values["AdjstdQt"]),
                "trades": int(float(values.get("RglrTxsQty", "0"))),
                "open_interest": float(values.get("OpnIntrst", "0")),
            })
        except ValueError:
            continue
    if not candidates:
        raise ValueError("SPRD sem contrato WIN com preço de ajuste")
    return max(
        candidates,
        key=lambda item: (item["trades"], item["open_interest"], item["ticker"]),
    )


def parse_ibov_spot(stream: BinaryIO) -> float:
    """Lê o fechamento oficial do IBOV no BVBG.087/IR."""
    for info in _iter_elements(stream, "IndxInf"):
        values = _values(info)
        if values.get("TckrSymb") == "IBOV":
            raw = values.get("ClsgPric") or values.get("IndxVal")
            if raw:
                return float(raw)
    raise ValueError("IR sem fechamento do índice IBOV")


def parse_equity_premiums(stream: TextIO) -> dict[str, dict]:
    """Lê PE: ticker;C/V;estilo;AAAAMMDD;strike;prêmio;IV_B3."""
    out = {}
    for line in stream:
        fields = line.strip().split(";")
        if len(fields) < 6 or not fields[0].startswith("IBOV"):
            continue
        ticker, option_type, _style, expiry, strike, premium = fields[:6]
        if option_type not in {"C", "V"} or len(expiry) != 8:
            continue
        try:
            out[ticker] = {
                "ticker": ticker,
                "strike": float(strike),
                "is_call": option_type == "C",
                "expiry": f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:]}",
                "premium": float(premium),
            }
        except ValueError:
            continue
    return out


def assemble_ibov_options(oi_by_ticker: dict[str, float], premiums: dict[str, dict]) -> list[dict]:
    options = []
    for ticker in sorted(oi_by_ticker):
        premium = premiums.get(ticker)
        if premium is not None:
            options.append({**premium, "oi": float(oi_by_ticker[ticker])})
    return options


@contextmanager
def open_zip_member(path: Path, *, text: bool = False):
    """Abre o único/último membro de ZIP comum ou SFX sem extrair em disco."""
    with zipfile.ZipFile(path) as archive:
        names = sorted(name for name in archive.namelist() if not name.endswith("/"))
        if not names:
            raise ValueError(f"arquivo oficial vazio: {path}")
        with archive.open(names[-1]) as raw:
            if text:
                with io.TextIOWrapper(raw, encoding="latin-1") as wrapped:
                    yield wrapped
            else:
                yield raw


def _stamp(session_date: str) -> str:
    parsed = date.fromisoformat(session_date)
    return parsed.strftime("%y%m%d")


def expected_bundle_names(session_date: str) -> dict[str, str]:
    stamp = _stamp(session_date)
    return {
        "equities": f"SPRE{stamp}.zip",
        "derivatives": f"SPRD{stamp}.zip",
        "premiums": f"PE{stamp}.ex_",
        "index": f"IR{stamp}.zip",
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_b3_bundle(session_date: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> dict[str, Path]:
    """Baixa uma vez e materializa somente os quatro payloads esperados."""
    names = expected_bundle_names(session_date)
    target_dir = Path(cache_dir) / session_date
    target_dir.mkdir(parents=True, exist_ok=True)
    paths = {kind: target_dir / name for kind, name in names.items()}
    if all(path.is_file() and path.stat().st_size > 0 for path in paths.values()):
        return paths

    query = urllib.parse.urlencode({"filelist": ",".join(names.values()) + ","})
    request = urllib.request.Request(
        f"{B3_DOWNLOAD_BASE}?{query}", headers={"User-Agent": "IRAI-GEX-History/1.0"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as outer:
        outer_names = set(outer.namelist())
        missing = [name for name in names.values() if name not in outer_names]
        if missing:
            raise ValueError(f"bundle B3 {session_date} sem arquivos: {missing}")
        for kind, name in names.items():
            destination = paths[kind]
            fd, tmp_name = tempfile.mkstemp(prefix=destination.name, dir=target_dir)
            try:
                with os.fdopen(fd, "wb") as tmp:
                    tmp.write(outer.read(name))
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.replace(tmp_name, destination)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
    return paths


def parse_official_bundle(paths: dict[str, Path]) -> dict:
    with open_zip_member(paths["equities"]) as stream:
        oi = parse_ibov_open_interest(stream)
    with open_zip_member(paths["derivatives"]) as stream:
        win = parse_win_front_settle(stream)
    with open_zip_member(paths["index"]) as stream:
        spot = parse_ibov_spot(stream)
    with open_zip_member(paths["premiums"], text=True) as stream:
        premiums = parse_equity_premiums(stream)
    options = assemble_ibov_options(oi, premiums)
    return {
        "spot": spot,
        "win": win,
        "options": options,
        "oi_series": len(oi),
        "premium_series": len(premiums),
        "joined_series": len(options),
    }


def next_effective_win_session(conn, source_session_date: str) -> str | None:
    row = conn.execute(
        """SELECT MIN(date(timestamp_utc))
           FROM market_bars
           WHERE symbol='WIN$N' AND timeframe='M5'
             AND date(timestamp_utc) > ?""",
        (source_session_date,),
    ).fetchone()
    return row[0] if row and row[0] else None


def win_session_pairs(conn, from_date: str | None, to_date: str | None) -> list[tuple[str, str]]:
    rows = conn.execute(
        """SELECT DISTINCT date(timestamp_utc) AS d
           FROM market_bars
           WHERE symbol='WIN$N' AND timeframe='M5'
           ORDER BY d"""
    ).fetchall()
    dates = [row[0] for row in rows]
    pairs = list(zip(dates, dates[1:]))
    if from_date:
        pairs = [pair for pair in pairs if pair[0] >= from_date]
    if to_date:
        pairs = [pair for pair in pairs if pair[0] <= to_date]
    return pairs


def fetch_selic_history(start_date: str, end_date: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> dict[str, float]:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"selic-1178-{start_date}-{end_date}.json"
    if cache.exists():
        payload = json.loads(cache.read_text(encoding="utf-8"))
    else:
        query = urllib.parse.urlencode({
            "formato": "json",
            "dataInicial": date.fromisoformat(start_date).strftime("%d/%m/%Y"),
            "dataFinal": date.fromisoformat(end_date).strftime("%d/%m/%Y"),
        })
        request = urllib.request.Request(
            f"{BCB_SELIC_BASE}?{query}", headers={"User-Agent": "IRAI-GEX-History/1.0"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
        cache.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        datetime.strptime(item["data"], "%d/%m/%Y").date().isoformat():
            float(str(item["valor"]).replace(",", ".")) / 100.0
        for item in payload
    }


def rate_at_or_before(rates: dict[str, float], session_date: str) -> tuple[str, float]:
    available = [key for key in rates if key <= session_date]
    if not available:
        raise ValueError(f"Selic SGS 1178 indisponível até {session_date}")
    source = max(available)
    return source, rates[source]


# API pública preservada para os testes/CLI existentes, mas a implementação
# autoritativa é única e vive fora do worker e do script para evitar
# circularidade. LIVE e backfill resolvem exatamente estes mesmos callables.
assemble_ibov_options = gex_official.assemble_ibov_options
download_b3_bundle = gex_official.download_b3_bundle
expected_bundle_names = gex_official.expected_bundle_names
fetch_selic_history = gex_official.fetch_selic_history
open_zip_member = gex_official.open_zip_member
parse_equity_premiums = gex_official.parse_equity_premiums
parse_ibov_open_interest = gex_official.parse_ibov_open_interest
parse_ibov_spot = gex_official.parse_ibov_spot
parse_official_bundle = gex_official.parse_official_bundle
parse_win_front_settle = gex_official.parse_win_front_settle
rate_at_or_before = gex_official.rate_at_or_before
_sha256 = gex_official.sha256_file


def gex_validity_reasons(result: dict, *, grid_step: float) -> list[str]:
    """Espelha os gates de ``compute_gex`` em motivos auditáveis."""
    flip = result.get("gamma_flip_ibov")
    reasons = []
    if flip is None:
        reasons.append("missing_gamma_flip")
    else:
        if abs(flip - result["spot"]) >= 15 * grid_step:
            reasons.append("gamma_flip_too_far_from_spot")
    if result.get("liquid_strikes", 0) < 8:
        reasons.append("insufficient_liquid_strikes")
    return reasons


def gex_diagnostic_warnings(result: dict) -> list[str]:
    """Condições informativas que não reprovam a qualidade do snapshot."""
    flip = result.get("gamma_flip_ibov")
    if flip is not None and not (
        result["gamma_max_ibov"] > flip > result["gamma_min_ibov"]
    ):
        return ["gamma_flip_not_between_pointwise_extrema"]
    return []


def ensure_safe_sqlite_runtime(db_path: str | Path, *, platform: str | None = None) -> None:
    """Impede escritor SQLite Linux sobre arquivo hospedado no DrvFS/Windows.

    WAL e locks não são interoperáveis entre o SQLite Linux e processos
    Windows no mesmo arquivo em ``/mnt/<drive>``. O backfill deve usar o mesmo
    runtime dos serviços que escrevem a base: Python do Windows.
    """
    platform = platform or sys.platform
    if platform == "win32":
        return
    raw_posix = os.fspath(db_path).replace("\\", "/")
    resolved = Path(db_path).expanduser().resolve(strict=False)
    parts = resolved.parts
    raw_is_drvfs = re.match(r"^/mnt/[A-Za-z](?:/|$)", raw_posix) is not None
    resolved_is_drvfs = (
        len(parts) >= 3 and parts[0] == "/" and parts[1] == "mnt" and len(parts[2]) == 1
    )
    if raw_is_drvfs or resolved_is_drvfs:
        raise ValueError(
            f"SQLite hospedado no Windows ({resolved}) não pode ser escrito pelo "
            "Python Linux/WSL; execute este backfill com o Python do Windows"
        )


def open_backfill_database(db_path: str | Path):
    """Abre somente uma base IRAI existente e com as tabelas exigidas.

    ``backend.db.get_connection`` cria o arquivo quando o caminho está errado,
    comportamento útil na inicialização da aplicação, mas perigoso em backfill.
    Aqui falhamos antes de criar ou escrever qualquer SQLite acidental.
    """
    ensure_safe_sqlite_runtime(db_path)
    path = Path(db_path).expanduser()
    if not path.is_file():
        raise ValueError(f"base IRAI não existe: {path}")
    conn = get_connection(os.fspath(path))
    required = {"market_bars"}
    present = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            tuple(sorted(required)),
        )
    }
    missing = sorted(required - present)
    if missing:
        conn.close()
        raise ValueError(
            "base IRAI sem tabela obrigatória market_bars: "
            + ", ".join(missing)
        )
    ensure_history_schema(conn)
    return conn


# Writer único (ver comentário acima de GEX_HISTORY_SCHEMA) — evita duas
# implementações de INSERT OR REPLACE divergindo sobre as mesmas colunas.
ensure_history_schema = gex.ensure_history_schema
save_history_result = gex.save_history_result

# Política de sobrescrita única (revisão tri-r do F4) — o worker EOD agendado
# e este backfill manual gravam a MESMA tabela gex_history_levels, então os
# dois têm que respeitar a mesma regra: um snapshot válido existente nunca é
# degradado por um candidato inválido a menos que --replace seja explícito.
# decide_persistence/existing_validity seguem exportadas para uso
# informativo/testes da tabela de decisão; a gravação de fato passa por
# save_history_result_gated, que decide e escreve no mesmo statement
# atômico (revisão tri-r do F4, achado P1: ver _upsert_history_row_gated).
decide_persistence = gex.decide_persistence
existing_validity = gex.existing_validity
save_history_result_gated = gex.save_history_result_gated


def migrate_historical_rows_from_live(conn) -> int:
    """Move para o histórico somente linhas de gex_levels identificadas como
    LEGADO INEQUÍVOCO: proveniência PIT completa (``meta.source_files`` +
    ``meta.effective_session_date``) E AUSÊNCIA do marcador
    ``meta.history_dual_write``.

    Por que a ausência do marcador, e não a proveniência sozinha (revisão
    tri-r do F4, fechamento do achado P1-2): proveniência completa sozinha
    NÃO distingue sobra do backfill legado de linha gravada pelo worker
    atual — qualquer WIN$N computado por ``compute_official_win_snapshot``
    carrega os dois campos, seja legado ou atual. O que só o legado real
    pode não ter é a chave ``history_dual_write``: ela é gravada por
    ``compute_official_win_snapshot`` desde este fix, e portanto está
    presente em todo snapshot computado por código atual (worker EOD via
    main(), ou este próprio módulo) — uma linha sem ela só pode ter sido
    gravada por código anterior a esta mudança, ou seja, é legado por
    construção, não por inferência sobre o estado de outra tabela. Uma linha
    que carrega o marcador nunca é candidata a migração, mesmo que
    gex_history_levels esteja sem par (ausente) ou com par inválido para a
    mesma chave — esses dois estados são exatamente o cenário que o guard
    sozinho não cobria (achado original: existente ausente permite INSERT
    incondicional; existente inválido + candidato válido permite upgrade
    incondicional — os dois liberam o DELETE em seguida mesmo quando a linha
    é do worker atual, não legado).

    Por isso a escrita no destino, para o subconjunto que passa neste filtro,
    ainda passa pelo MESMO guard atômico do resto do módulo
    (``_upsert_history_row_gated`` — nunca ``replace=True`` aqui: essa
    migração não tem como saber se o candidato do LIVE é mais confiável que
    um histórico já persistido, então nunca força sobrescrita) e o DELETE em
    gex_levels só acontece quando esse guard efetivamente gravou a linha no
    histórico. Se já existe um snapshot válido em gex_history_levels para o
    mesmo (source_session_date, target), o candidato do LIVE é descartado e
    a linha correspondente permanece intacta em gex_levels — nem sobrescreve
    o histórico válido, nem apaga o dado ao vivo sem ter para onde movê-lo
    (revisão tri-r do F4, achado P1: a versão anterior fazia INSERT OR
    REPLACE incondicional seguido de DELETE incondicional, e podia degradar
    um histórico válido E apagar o nível live no mesmo golpe). Cada linha é
    decidida e gravada dentro da mesma transação do laço.
    """
    ensure_history_schema(conn)
    rows = conn.execute(
        """SELECT session_date, target, gamma_max, gamma_min, gamma_flip,
                  gamma_max_ibov, gamma_min_ibov, gamma_flip_ibov,
                  spot, future_settle, conv_factor, n_strikes, valid,
                  walls, meta, computed_at
           FROM gex_levels"""
    ).fetchall()
    historical = []
    for row in rows:
        meta = json.loads(row[14] or "{}")
        effective = meta.get("effective_session_date")
        if not meta.get("source_files") or not effective:
            continue
        if meta.get("history_dual_write"):
            continue
        historical.append((row, effective))

    migrated = 0
    with conn:
        for row, effective in historical:
            session_date, target = row[0], row[1]
            candidate = {
                "gamma_max": row[2], "gamma_min": row[3], "gamma_flip": row[4],
                "gamma_max_ibov": row[5], "gamma_min_ibov": row[6],
                "gamma_flip_ibov": row[7],
                "spot": row[8], "future_settle": row[9], "conv_factor": row[10],
                "n_strikes": row[11], "valid": bool(row[12]),
                "walls": json.loads(row[13] or "[]"),
                "meta": json.loads(row[14] or "{}"),
            }
            written = save_history_result_gated(
                conn, session_date, effective, candidate, target=target,
                replace=False, commit=False, computed_at=row[15],
            )
            if not written:
                continue
            conn.execute(
                "DELETE FROM gex_levels WHERE session_date=? AND target=?",
                (session_date, target),
            )
            migrated += 1
    return migrated


def reclassify_history_validity(conn, *, target: str = "WIN$N") -> dict[str, int]:
    """Reaplica apenas os gates atuais aos snapshots já persistidos.

    Níveis, walls, fontes e timestamps permanecem byte a byte; somente
    ``valid``, ``validity_reasons`` e ``diagnostic_warnings`` são atualizados.
    """
    ensure_history_schema(conn)
    rows = conn.execute(
        """SELECT source_session_date, valid, gamma_max_ibov, gamma_min_ibov,
                  gamma_flip_ibov, spot, meta
           FROM gex_history_levels WHERE target=?""",
        (target,),
    ).fetchall()
    before = sum(bool(row[1]) for row in rows)
    promoted = demoted = 0
    with conn:
        for source, old_valid, gamma_max, gamma_min, flip, spot, raw_meta in rows:
            meta = json.loads(raw_meta or "{}")
            result = {
                "gamma_max_ibov": gamma_max,
                "gamma_min_ibov": gamma_min,
                "gamma_flip_ibov": flip,
                "spot": spot,
                "liquid_strikes": meta.get("liquid_strikes", 0),
            }
            reasons = gex_validity_reasons(
                result, grid_step=float(meta.get("grid_step", gex.GRID_STEP)),
            )
            warnings = gex_diagnostic_warnings(result)
            new_valid = not reasons
            promoted += bool(new_valid and not old_valid)
            demoted += bool(old_valid and not new_valid)
            meta["validity_reasons"] = reasons
            meta["diagnostic_warnings"] = warnings
            conn.execute(
                """UPDATE gex_history_levels SET valid=?, meta=?
                   WHERE source_session_date=? AND target=?""",
                (int(new_valid), json.dumps(meta, ensure_ascii=False), source, target),
            )
    return {
        "sessions": len(rows),
        "valid_before": before,
        "valid_after": before + promoted - demoted,
        "promoted": promoted,
        "demoted": demoted,
    }


def process_session(
    conn,
    source_session_date: str,
    effective_session_date: str,
    risk_free: float,
    rate_source_date: str,
    *,
    cache_dir: Path,
    replace: bool,
    dry_run: bool,
) -> dict:
    result = gex.compute_official_win_snapshot(
        source_session_date,
        effective_session_date,
        risk_free,
        rate_source_date,
        cache_dir=cache_dir,
    )
    validity_reasons = list(result["meta"].get("validity_reasons", []))
    diagnostic_warnings = list(result["meta"].get("diagnostic_warnings", []))

    # Decisão e escrita são o MESMO statement atômico (save_history_result_gated
    # -> _upsert_history_row_gated) — sem leitura prévia de validade que outra
    # conexão concorrente (o worker EOD, por exemplo) pudesse invalidar entre o
    # "checar" e o "gravar" (revisão tri-r do F4, achado P1).
    #
    # --dry-run não deve gravar nada; a leitura informativa de `existing`
    # aqui só serve para rotular a ação no relatório, nunca para decidir a
    # escrita real (que, fora do dry-run, é sempre o guard SQL).
    if dry_run:
        try:
            existing = existing_validity(conn, source_session_date)
        except sqlite3.OperationalError:
            existing = None
        action = f"dry_run_{decide_persistence(existing, bool(result['valid']), replace=replace)}"
    else:
        written = save_history_result_gated(
            conn, source_session_date, effective_session_date, result,
            target="WIN$N", replace=replace,
        )
        # "skipped": o guard bloqueou -- ou já havia um snapshot válido (o
        # candidato nunca sobrescreve sem --replace), ou o candidato também
        # era inválido e não havia motivo para trocar um pelo outro. Não
        # tentamos rotular qual dos dois com mais precisão aqui: fazer isso
        # exigiria uma leitura extra pré-escrita puramente informativa, que
        # é exatamente o padrão check-then-act que este fix elimina.
        action = "written" if written else "skipped"
    return {
        "source_session_date": source_session_date,
        "effective_session_date": effective_session_date,
        "action": action,
        "valid": bool(result["valid"]),
        "validity_reasons": validity_reasons,
        "diagnostic_warnings": diagnostic_warnings,
        "gamma_max": result["gamma_max"],
        "gamma_flip": result["gamma_flip"],
        "gamma_min": result["gamma_min"],
        "wall_count": sum(wall["type"] == "wall" for wall in result["walls"]),
        "mid_wall_count": sum(wall["type"] == "mid_wall" for wall in result["walls"]),
        "counts": result["meta"]["source_counts"],
        "win_contract": result["meta"]["win_contract"]["ticker"],
        "risk_free": risk_free,
        "risk_free_source_date": rate_source_date,
    }


def audit_existing_sessions(conn, pairs: Iterable[tuple[str, str]]) -> list[dict]:
    """Consolida cobertura/proveniência já persistida sem recalcular o GEX."""
    ensure_history_schema(conn)
    rows = []
    for source, effective in pairs:
        stored = conn.execute(
            """SELECT valid, gamma_max, gamma_flip, gamma_min, walls, meta
               FROM gex_history_levels
               WHERE source_session_date=? AND target='WIN$N'""",
            (source,),
        ).fetchone()
        if stored is None:
            rows.append({
                "source_session_date": source,
                "effective_session_date": effective,
                "action": "missing_existing",
                "valid": False,
                "validity_reasons": ["missing_existing_gex_row"],
                "provenance_complete": False,
            })
            continue
        meta = json.loads(stored["meta"] or "{}")
        walls = json.loads(stored["walls"] or "[]")
        source_files = meta.get("source_files", {})
        provenance_complete = (
            meta.get("effective_session_date") == effective
            and len(source_files) == 4
            and all(item.get("sha256") for item in source_files.values())
        )
        reasons = list(meta.get("validity_reasons", []))
        if meta.get("effective_session_date") != effective:
            reasons.append("effective_session_date_mismatch")
        rows.append({
            "source_session_date": source,
            "effective_session_date": effective,
            "action": "audit_existing" if provenance_complete else "audit_incomplete_provenance",
            "valid": bool(stored["valid"]),
            "validity_reasons": reasons,
            "gamma_max": stored["gamma_max"],
            "gamma_flip": stored["gamma_flip"],
            "gamma_min": stored["gamma_min"],
            "wall_count": sum(wall.get("type") == "wall" for wall in walls),
            "mid_wall_count": sum(wall.get("type") == "mid_wall" for wall in walls),
            "counts": meta.get("source_counts", {}),
            "win_contract": meta.get("win_contract", {}).get("ticker"),
            "provenance_complete": provenance_complete,
        })
    return rows


def summarize(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    reasons = sorted({reason for row in rows for reason in row.get("validity_reasons", [])})
    return {
        "sessions": len(rows),
        "valid": sum(bool(row.get("valid")) for row in rows),
        "invalid": sum(row.get("valid") is False for row in rows),
        "actions": {
            action: sum(row.get("action") == action for row in rows)
            for action in sorted({row.get("action") for row in rows})
        },
        "rejection_reasons": {
            reason: sum(reason in row.get("validity_reasons", []) for row in rows)
            for reason in reasons
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--limit", type=int, default=20,
                        help="número das sessões-fonte mais recentes (0=todas)")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--audit-only", action="store_true",
                        help="não baixa/recalcula; audita somente gex_history_levels")
    parser.add_argument("--output-json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        conn = open_backfill_database(args.db)
    except ValueError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    try:
        pairs = win_session_pairs(conn, args.from_date, args.to_date)
        if args.limit > 0:
            pairs = pairs[-args.limit:]
        if not pairs:
            print("nenhum par source/effective WIN encontrado", file=sys.stderr)
            return 1
        if args.audit_only:
            rows = audit_existing_sessions(conn, pairs)
            for row in rows:
                print(json.dumps(row, ensure_ascii=False), flush=True)
        else:
            rates = fetch_selic_history(pairs[0][0], pairs[-1][0], args.cache_dir)
            rows = []
            for source, effective in pairs:
                try:
                    rate_source, rate = rate_at_or_before(rates, source)
                    row = process_session(
                        conn, source, effective, rate, rate_source,
                        cache_dir=args.cache_dir, replace=args.replace, dry_run=args.dry_run,
                    )
                except Exception as exc:
                    row = {
                        "source_session_date": source,
                        "effective_session_date": effective,
                        "action": "error",
                        "valid": False,
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
        report = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "db": str(args.db),
            "causal_policy": "B3 EOD D -> next observed WIN session",
            "limitations": [
                "Os arquivos oficiais históricos são baixados na versão hoje disponível. "
                "A B3 pode republicar/retificar um pregão; hashes e retrieved_at fixam a "
                "vintage usada neste backfill, mas não provam que ela é idêntica ao arquivo "
                "originalmente disponível em D+1.",
                "O preço executável da futura regra manual e a ordem intrabarra de toque em "
                "GEX/MID ainda não fazem parte deste backfill; aqui somente preparamos e "
                "auditamos os níveis causais diários.",
            ],
            "summary": summarize(rows),
            "sessions": rows,
        }
        if args.output_json:
            Path(args.output_json).write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        return 1 if report["summary"]["actions"].get("error") else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
