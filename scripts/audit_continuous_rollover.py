#!/usr/bin/env python3
"""Audita o rollover da série contínua WIN sem modificar o banco.

A série ``$N`` é fornecida pelo broker. Este utilitário combina a descrição
observada no MT5 com o calendário contratual da B3 e as descontinuidades
overnight do histórico M5. O resultado também contém as sessões que devem
ser excluídas numa análise de sensibilidade do backtest.

Exemplo (WIN):

    python3 -X utf8 scripts/audit_continuous_rollover.py \
      --db data/irai.db --symbol 'WIN$N' --source br \
      --series-description 'IBOVESPA MINI - Por Liquidez (WINQ26) - Sem Ajustes' \
      --output-json win_rollover_audit.json

O script não tenta inferir sozinho o instante intradiário da troca do contrato:
as séries vencidas não necessariamente permanecem disponíveis no servidor MT5.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Iterable, Sequence


SCHEMA_VERSION = "irai.rollover-audit.v1"
EVEN_MONTHS = (2, 4, 6, 8, 10, 12)
B3_WIN_CONTRACT_URL = (
    "https://www.b3.com.br/pt_br/produtos-e-servicos/negociacao/"
    "renda-variavel/futuro-mini-de-ibovespa.htm"
)


@dataclass(frozen=True)
class DailyBar:
    session_date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    bars: int


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _nearest_wednesday_to_15(year: int, month: int) -> date:
    fifteenth = date(year, month, 15)
    # weekday(): segunda=0, quarta=2. O deslocamento normalizado fica entre
    # -3 e +3, portanto escolhe inequivocamente a quarta mais próxima.
    offset = (2 - fifteenth.weekday() + 3) % 7 - 3
    return fifteenth + timedelta(days=offset)


def expected_win_expiries(start: str, end: str) -> list[date]:
    """Vencimentos contratuais do WIN no intervalo inclusivo.

    Regra B3: meses pares, quarta-feira mais próxima do dia 15. Se não houver
    pregão, ``audit_rollovers`` mapeia a data para a sessão seguinte observada.
    """
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    if end_date < start_date:
        raise ValueError("end deve ser igual ou posterior a start")

    result = []
    for year in range(start_date.year, end_date.year + 1):
        for month in EVEN_MONTHS:
            expiry = _nearest_wednesday_to_15(year, month)
            if start_date <= expiry <= end_date:
                result.append(expiry)
    return result


def calendar_for_symbol(symbol: str, start: str, end: str) -> list[date]:
    """Seleciona uma regra explicitamente validada para o ativo.

    O primeiro corte do IRAI-5 é deliberadamente WIN. Falhar de forma clara
    evita aplicar ao WDO, por conveniência, uma regra contratual que não foi
    implementada nem validada nesta fatia.
    """
    if symbol == "WIN$N":
        return expected_win_expiries(start, end)
    raise NotImplementedError(
        f"calendário de rollover de {symbol!r} ainda não implementado; "
        "esta versão do auditor cobre somente WIN$N"
    )


def infer_continuous_method(description: str | None) -> str:
    """Classifica apenas o que a descrição do símbolo MT5 permite afirmar."""
    normalized = (description or "").casefold()
    has_liquidity = "por liquidez" in normalized
    unadjusted = "sem ajustes" in normalized or "sem ajuste" in normalized
    if has_liquidity and unadjusted:
        return "liquidity_continuous_unadjusted"
    if unadjusted:
        return "continuous_unadjusted"
    if has_liquidity:
        return "liquidity_continuous_adjustment_unknown"
    return "unknown"


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def load_daily_bars(db_path: str, symbol: str, source: str = "br") -> list[DailyBar]:
    """Agrega M5 em sessões diárias preservando primeiro open e último close."""
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute(
            """
            SELECT timestamp_utc, open, high, low, close, COALESCE(volume, 0)
              FROM market_bars
             WHERE symbol = ? AND source = ? AND timeframe = 'M5'
             ORDER BY timestamp_utc
            """,
            (symbol, source),
        ).fetchall()

    grouped: dict[str, list[tuple]] = {}
    for row in rows:
        grouped.setdefault(str(row[0])[:10], []).append(row)

    daily = []
    for session_date, session_rows in grouped.items():
        daily.append(DailyBar(
            session_date=session_date,
            open=float(session_rows[0][1]),
            high=max(float(row[2]) for row in session_rows),
            low=min(float(row[3]) for row in session_rows),
            close=float(session_rows[-1][4]),
            volume=sum(float(row[5]) for row in session_rows),
            bars=len(session_rows),
        ))
    return daily


def audit_rollovers(
    daily_bars: Sequence[DailyBar],
    *,
    expected_expiries: Iterable[date],
    window_sessions: int = 1,
) -> dict:
    """Relaciona vencimentos contratuais às sessões e mede gaps overnight."""
    if window_sessions < 0:
        raise ValueError("window_sessions não pode ser negativo")

    bars = sorted(daily_bars, key=lambda bar: bar.session_date)
    dates = [_parse_date(bar.session_date) for bar in bars]
    gaps = [bars[i].open - bars[i - 1].close for i in range(1, len(bars))]
    absolute_gaps = [abs(value) for value in gaps]
    median_abs_gap = median(absolute_gaps) if absolute_gaps else None
    p95_abs_gap = _percentile(absolute_gaps, 0.95)

    excluded_indices: set[int] = set()
    rollovers = []
    for contractual_expiry in sorted(expected_expiries):
        effective_index = next(
            (index for index, session_date in enumerate(dates)
             if session_date >= contractual_expiry),
            None,
        )
        if effective_index is None:
            rollovers.append({
                "contractual_expiry": contractual_expiry.isoformat(),
                "effective_session": None,
                "status": "no_observed_session_on_or_after_expiry",
            })
            continue

        effective_bar = bars[effective_index]
        previous_bar = bars[effective_index - 1] if effective_index > 0 else None
        overnight_gap = (
            effective_bar.open - previous_bar.close if previous_bar is not None else None
        )
        ratio = (
            abs(overnight_gap) / median_abs_gap
            if overnight_gap is not None and median_abs_gap not in (None, 0.0)
            else None
        )
        rollovers.append({
            "contractual_expiry": contractual_expiry.isoformat(),
            "effective_session": effective_bar.session_date,
            "previous_session": previous_bar.session_date if previous_bar else None,
            "overnight_gap_points": overnight_gap,
            "absolute_gap_vs_median": ratio,
            "session_shift_days": (dates[effective_index] - contractual_expiry).days,
            "status": "observed",
        })
        lower = max(0, effective_index - window_sessions)
        upper = min(len(bars), effective_index + window_sessions + 1)
        excluded_indices.update(range(lower, upper))

    return {
        "sessions_observed": len(bars),
        "first_session": bars[0].session_date if bars else None,
        "last_session": bars[-1].session_date if bars else None,
        "median_absolute_overnight_gap_points": median_abs_gap,
        "p95_absolute_overnight_gap_points": p95_abs_gap,
        "window_sessions_each_side": window_sessions,
        "excluded_sessions": [bars[index].session_date for index in sorted(excluded_indices)],
        "rollovers": rollovers,
    }


def build_report(
    db_path: str,
    symbol: str,
    source: str,
    series_description: str | None,
    window_sessions: int,
) -> dict:
    bars = load_daily_bars(db_path, symbol, source)
    if not bars:
        raise RuntimeError(f"nenhuma barra M5 encontrada para {symbol!r}/{source!r}")
    expiries = calendar_for_symbol(symbol, bars[0].session_date, bars[-1].session_date)
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol,
        "source": source,
        "database": str(Path(db_path).resolve()),
        "mt5_series_description": series_description,
        "continuous_method": infer_continuous_method(series_description),
        "calendar_rule": (
            "B3 WIN: even months, Wednesday nearest the 15th; next trading "
            "session when the contractual date has no session"
        ),
        "calendar_source_url": B3_WIN_CONTRACT_URL,
        "audit": audit_rollovers(
            bars,
            expected_expiries=expiries,
            window_sessions=window_sessions,
        ),
        "limitations": [
            "The MT5 description classifies the broker series, but does not expose every historical switch timestamp.",
            "Contractual expiries are mapped to observed sessions; liquidity-driven switches may occur earlier.",
            "Event-result sensitivity must be computed from the NF-01 event ledger using excluded_sessions.",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", default="WIN$N")
    parser.add_argument("--source", default="br")
    parser.add_argument("--series-description")
    parser.add_argument("--window-sessions", type=int, default=1)
    parser.add_argument("--output-json")
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = build_report(
        args.db,
        args.symbol,
        args.source,
        args.series_description,
        args.window_sessions,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output_json:
        output = Path(args.output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
