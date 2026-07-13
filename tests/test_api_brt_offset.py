"""O contrato da API tem de carregar o offset BRT da sessão.

Sem `brt_offset_h` no payload, o frontend não tem como reconstruir o eixo BRT:
ele assumiria -6h fixo e erraria 1h fora do DST americano (próxima virada em
2026-11-01). Este teste trava o campo e o valor nas duas estações.
"""

from datetime import datetime

from backend.irai.timezones import brt_to_tickmill_offset_hours


def test_offset_da_sessao_muda_com_a_estacao():
    """O valor que a API expõe é o mesmo que o engine usou para deslocar."""
    verao = brt_to_tickmill_offset_hours(datetime.fromisoformat("2026-07-10"))
    inverno = brt_to_tickmill_offset_hours(datetime.fromisoformat("2026-01-15"))

    assert verao == 6
    assert inverno == 5
    # Se estes forem iguais, o eixo BRT do frontend não precisaria do campo —
    # e a regressão inteira perde o sentido.
    assert verao != inverno


def test_eixo_brt_reconstruido_bate_com_a_abertura_da_b3():
    """09:00 BRT (abertura da B3) tem de voltar a 09:00 nas duas estações.

    É exatamente a conta que o frontend faz: hora_do_eixo - brt_offset_h.
    """
    for session, hora_no_eixo in (("2026-07-10", 15), ("2026-01-15", 14)):
        offset = brt_to_tickmill_offset_hours(datetime.fromisoformat(session))
        assert hora_no_eixo - offset == 9, (
            f"sessão {session}: o eixo BRT reconstruído não bate com a abertura"
        )
