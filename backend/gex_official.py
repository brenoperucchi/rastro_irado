"""Fonte oficial causal do GEX WIN (B3 EOD + Selic BCB).

Este módulo não conhece persistência nem o cálculo financeiro. Ele é a única
implementação de aquisição e parsing do bundle SPRE/PE/IR/SPRD compartilhada
pelos caminhos LIVE e histórico.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO, TextIO


B3_DOWNLOAD_BASE = "https://www.b3.com.br/pesquisapregao/download"
BCB_SELIC_BASE = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.1178/dados"
DEFAULT_CACHE_DIR = Path("data/gex_history_cache")
WIN_CONTRACT_RE = re.compile(r"^WIN[A-Z]\d{2}$")
MIN_OFFICIAL_SERIES = 50


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
    return max(candidates, key=lambda item: (
        item["trades"], item["open_interest"], item["ticker"],
    ))


def parse_ibov_spot(stream: BinaryIO) -> float:
    for info in _iter_elements(stream, "IndxInf"):
        values = _values(info)
        if values.get("TckrSymb") == "IBOV":
            raw = values.get("ClsgPric") or values.get("IndxVal")
            if raw:
                return float(raw)
    raise ValueError("IR sem fechamento do índice IBOV")


def parse_equity_premiums(stream: TextIO) -> dict[str, dict]:
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


def assemble_ibov_options(
    oi_by_ticker: dict[str, float], premiums: dict[str, dict],
) -> list[dict]:
    return [
        {**premiums[ticker], "oi": float(oi_by_ticker[ticker])}
        for ticker in sorted(oi_by_ticker)
        if ticker in premiums
    ]


@contextmanager
def open_zip_member(path: Path, *, text: bool = False):
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


def expected_bundle_names(session_date: str) -> dict[str, str]:
    stamp = date.fromisoformat(session_date).strftime("%y%m%d")
    return {
        "equities": f"SPRE{stamp}.zip",
        "derivatives": f"SPRD{stamp}.zip",
        "premiums": f"PE{stamp}.ex_",
        "index": f"IR{stamp}.zip",
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_b3_bundle(
    session_date: str, cache_dir: Path = DEFAULT_CACHE_DIR,
) -> dict[str, Path]:
    names = expected_bundle_names(session_date)
    target_dir = Path(cache_dir) / session_date
    target_dir.mkdir(parents=True, exist_ok=True)
    paths = {kind: target_dir / name for kind, name in names.items()}
    if all(path.is_file() and path.stat().st_size > 0 for path in paths.values()):
        return paths

    query = urllib.parse.urlencode({"filelist": ",".join(names.values()) + ","})
    request = urllib.request.Request(
        f"{B3_DOWNLOAD_BASE}?{query}",
        headers={"User-Agent": "IRAI-GEX-Official/1.0"},
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


def _xml_payload_date(path: Path) -> str:
    with open_zip_member(path) as stream:
        for _event, element in ET.iterparse(stream, events=("end",)):
            if _local(element.tag) == "CreDtAndTm" and element.text:
                return date.fromisoformat(element.text.strip()[:10]).isoformat()
    raise ValueError(f"arquivo oficial sem CreDtAndTm: {path.name}")


def _premium_payload_date(path: Path) -> str:
    with open_zip_member(path, text=True) as stream:
        first = next((line.strip() for line in stream if line.strip()), "")
    if not re.fullmatch(r"\d{8}", first):
        raise ValueError(f"PE sem data interna: {path.name}")
    return datetime.strptime(first, "%Y%m%d").date().isoformat()


def parse_official_bundle(paths: dict[str, Path], source_session_date: str) -> dict:
    expected = {"equities", "derivatives", "premiums", "index"}
    if set(paths) != expected:
        raise ValueError(f"bundle oficial inconsistente: esperado {sorted(expected)}")
    expected_names = expected_bundle_names(source_session_date)
    wrong_names = {
        kind: path.name
        for kind, path in paths.items()
        if path.name != expected_names[kind]
    }
    if wrong_names:
        raise ValueError(f"bundle oficial com nomes incompatíveis: {wrong_names}")
    internal_dates = {
        "equities": _xml_payload_date(paths["equities"]),
        "derivatives": _xml_payload_date(paths["derivatives"]),
        "premiums": _premium_payload_date(paths["premiums"]),
        "index": _xml_payload_date(paths["index"]),
    }
    mismatches = {
        kind: value
        for kind, value in internal_dates.items()
        if value != source_session_date
    }
    if mismatches:
        actual = ", ".join(f"{kind}={value}" for kind, value in sorted(mismatches.items()))
        raise ValueError(
            f"data interna do bundle ({actual}) difere de {source_session_date}"
        )
    with open_zip_member(paths["equities"]) as stream:
        oi = parse_ibov_open_interest(stream)
    with open_zip_member(paths["derivatives"]) as stream:
        win = parse_win_front_settle(stream)
    with open_zip_member(paths["index"]) as stream:
        spot = parse_ibov_spot(stream)
    with open_zip_member(paths["premiums"], text=True) as stream:
        premiums = parse_equity_premiums(stream)
    options = assemble_ibov_options(oi, premiums)
    counts = {
        "oi_series": len(oi),
        "premium_series": len(premiums),
        "joined_series": len(options),
    }
    if any(counts[key] < MIN_OFFICIAL_SERIES for key in counts):
        raise ValueError(f"bundle oficial incompleto: {counts}")
    return {"spot": spot, "win": win, "options": options, **counts}


def source_file_provenance(paths: dict[str, Path]) -> dict[str, dict]:
    return {
        kind: {
            "name": path.name,
            "sha256": sha256_file(path),
        }
        for kind, path in paths.items()
    }


def fetch_selic_history(
    start_date: str,
    end_date: str,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> dict[str, float]:
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
            f"{BCB_SELIC_BASE}?{query}",
            headers={"User-Agent": "IRAI-GEX-Official/1.0"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
        cache.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return {
        datetime.strptime(item["data"], "%d/%m/%Y").date().isoformat():
            float(str(item["valor"]).replace(",", ".")) / 100.0
        for item in payload
    }


def rate_at_or_before(
    rates: dict[str, float], session_date: str,
) -> tuple[str, float]:
    available = [key for key in rates if key <= session_date]
    if not available:
        raise ValueError(f"Selic SGS 1178 indisponível até {session_date}")
    source = max(available)
    return source, rates[source]
