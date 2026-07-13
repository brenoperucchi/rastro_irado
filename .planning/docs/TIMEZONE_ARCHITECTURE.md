# IRAI Timezone Architecture & Data Alignment

## Relógios armazenados no banco

`market_bars.timestamp_utc` contém o relógio local do terminal coletor, embora o
campo termine em `Z`. A origem confiável desse relógio é `market_bars.source`:

| `source` | Relógio observado | Tratamento na engine |
|---|---|---|
| `br` | XP/B3 em BRT (UTC-3, sem DST) | deslocar para o eixo Tickmill |
| `tickmill` | servidor Tickmill | manter |
| `axi` | servidor Axi, offset não medido | manter |

O offset do Axi não foi medido. Até existir evidência, suas barras não devem ser
deslocadas.

## Eixo de referência Tickmill

A engine usa o relógio do servidor Tickmill como eixo comum. O relógio observado
no banco alterna sazonalmente:

| Período | Relógio Tickmill observado | BRT -> Tickmill |
|---|---|---|
| 2º domingo de março até a véspera do 1º domingo de novembro | UTC+3 | +6h |
| 1º domingo de novembro até a véspera do 2º domingo de março | UTC+2 | +5h |

Essa regra foi derivada empiricamente das barras de produção; não representa uma
política oficial publicada pelo broker. As transições medidas foram 2025-11-02 e
2026-03-08, compatíveis com o calendário sazonal americano. A próxima transição
calculada é 2026-11-01.

Ao carregar as barras, `engine.py` inclui `source` no `SELECT` e desloca toda
linha de origem `br`, seja target ou fator:

```python
if d["source"] == "br":
    offset_h = brt_to_tickmill_offset_hours(ts_dt)
    ts_dt += timedelta(hours=offset_h)
```

O alinhamento por origem é obrigatório. Deslocar somente o target B3 deixaria
fatores B3, como WDO$N e DI1$N na cesta do WIN$N, seis ou cinco horas à frente
do target no cursor causal. Targets globais usam fatores globais e, portanto,
permanecem no eixo original.

## Ghost bars e pré-mercado

Antes da abertura do target B3, a união de timestamps contém apenas fatores
globais. A engine gera barras sintéticas (ghost) ancoradas no último fechamento
do target e força retorno de 0%. Essas barras não alimentam observações do
Kalman/Johansen nem geram sinais.

## API e frontend

O formato do JSON não muda: timestamps calculados continuam no eixo Tickmill.
O frontend ainda reconstrói o eixo BRT com uma subtração fixa de seis horas.
Esse código só é correto durante o período UTC+3 do Tickmill; torná-lo sazonal é
uma lacuna separada de apresentação e não altera a causalidade interna da engine.
