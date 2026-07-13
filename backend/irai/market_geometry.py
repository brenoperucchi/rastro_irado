"""Geometria temporal compartilhada entre serving e calibração."""

from datetime import datetime, timedelta

from backend.irai.timezones import brt_to_tickmill_offset_hours


def align_market_bar(row):
    """Projeta uma barra crua no eixo do servidor usado pelo engine."""
    aligned = dict(row)
    timestamp = datetime.fromisoformat(
        aligned["timestamp_utc"].replace("Z", "+00:00")
    )
    aligned["session_date"] = timestamp.date()
    if aligned["source"] == "br":
        timestamp += timedelta(hours=brt_to_tickmill_offset_hours(timestamp))
    aligned["timestamp"] = timestamp
    aligned["timestamp_utc"] = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    aligned["hour"] = timestamp.hour
    return aligned


def align_market_bars(rows):
    return [align_market_bar(row) for row in rows]


def return_from_open(open_price, current_price):
    """Mesma variável de retorno usada pelo serving e pela regressão."""
    if open_price > 0 and current_price > 0:
        return (current_price - open_price) / open_price
    return 0.0


def serving_daily_returns(rows, target_symbol, min_bars=10):
    """Retornos finais que o engine teria servido em cada sessão do target.

    A sessão é a data da consulta crua do engine (00:00–24:00 no banco). Após
    alinhar por origem, fatores são fechados no último preço observável até a
    última barra alinhada do target. Isso importa no inverno, quando o target
    B3 termina uma hora antes do fechamento do dia Tickmill.
    """
    result = {}
    current_date = None
    session_rows = []

    def finish_session(session_date, session_rows):
        target_rows = sorted(
            (row for row in session_rows if row["symbol"] == target_symbol),
            key=lambda row: row["timestamp"],
        )
        if len(target_rows) < min_bars:
            return
        cutoff = target_rows[-1]["timestamp"]

        symbols = {row["symbol"] for row in session_rows}
        for symbol in symbols:
            symbol_rows = sorted(
                (row for row in session_rows if row["symbol"] == symbol),
                key=lambda row: row["timestamp"],
            )
            observable = [row for row in symbol_rows if row["timestamp"] <= cutoff]
            if len(observable) < min_bars:
                continue
            result.setdefault(symbol, {})[session_date] = return_from_open(
                float(symbol_rows[0]["open"]), float(observable[-1]["close"])
            )

    # O cursor SQL do calibrador chega ordenado por timestamp cru. Processar
    # uma sessão por vez evita materializar anos de M5 em memória.
    for row in rows:
        aligned = align_market_bar(row)
        row_date = aligned["session_date"]
        if current_date is not None and row_date != current_date:
            finish_session(current_date, session_rows)
            session_rows = []
        current_date = row_date
        session_rows.append(aligned)
    if current_date is not None:
        finish_session(current_date, session_rows)
    return result
