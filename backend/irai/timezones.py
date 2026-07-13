"""Regras puras para alinhar relógios locais dos provedores do IRAI."""

from datetime import datetime


def _nth_sunday(year: int, month: int, occurrence: int) -> int:
    first = datetime(year, month, 1)
    first_sunday = 1 + (6 - first.weekday()) % 7
    return first_sunday + 7 * (occurrence - 1)


def brt_to_tickmill_offset_hours(dt: datetime) -> int:
    """Retorna o deslocamento BRT -> relógio do servidor Tickmill."""
    # Regra derivada das barras de produção, não de política oficial do broker:
    # o servidor observado segue as datas sazonais americanas.
    dst_start = datetime(dt.year, 3, _nth_sunday(dt.year, 3, 2)).date()
    dst_end = datetime(dt.year, 11, _nth_sunday(dt.year, 11, 1)).date()
    return 6 if dst_start <= dt.date() < dst_end else 5
