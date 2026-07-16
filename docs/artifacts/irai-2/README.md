# Artefato NF-01A (backlog IRAI-2, comentário #3, item 4)

Artefato point-in-time versionado dos 5 geradores de evento do NF-01 — Pair
Signal, divergência macro-preço (Z), interseção Pair∩Z e os 2 baselines
(momentum, reversão) — sobre WIN$N e WDO$N.

## Arquivos

- **`nf01_pit.json.gz`** — artefato COMPLETO (gzip, ~712 KB; ~12,6 MB
  descomprimido). Contém metadata + resultados agregados + **os 17983 eventos
  individuais**, cada um com os 4 timestamps causais (`observation_bar_end`,
  `confirmation_bar_end`, `signal_available_at`, `entry_at`). Ler com:
  ```python
  import gzip, json
  a = json.load(gzip.open("nf01_pit.json.gz", "rt", encoding="utf-8"))
  ```
- **`nf01_pit_summary.json`** — o MESMO artefato SEM os eventos individuais
  (JSON puro, legível e diff-friendly). Inclui `event_counts` por sinal/alvo.

## Como foi gerado (reprodutível)

O campo `command` e `parameters` dentro do artefato registram exatamente a
invocação. Resumo:

```bash
python3 -X utf8 scripts/build_nf01_artifact.py \
  --db data/irai.db --targets WIN$N WDO$N --point-in-time --limit 2000 \
  --output docs/artifacts/irai-2/nf01_pit.json
```

- `git.commit` == `git.origin_main` == `f9f90b4` e `head_in_origin_main: true`
  — o código que gerou o artefato está publicado em `origin/main` (localizável
  por quem clonar o repo). `dirty: true` reflete só arquivos de DADOS
  não-versionados no host (o DB de produção, backups) — os SCRIPTS são os do
  commit registrado.
- Rodado no host de produção `ryzen5wsl` (Windows/WSL, onde `sklearn`/
  `pykalman`/`MetaTrader5` existem). O ambiente de dev Linux não roda o modo
  point-in-time (sem essas libs), por isso o artefato é gerado lá.

## Políticas PROVISÓRIAS (escopo IRAI-2 — NÃO é o realismo econômico)

Registradas em `provisional_policies` dentro do artefato:

- **entry_price** = close da PRÓXIMA barra M5 após o sinal — **fill
  hipotético**, não o primeiro preço realmente executável.
- **MFE/MAE** usam só o CLOSE de cada barra M5, não os extremos intrabar (OHLC).
- **custos** = `TARGET_COST_POINTS` (aproximado, ADR-002).

Primeiro preço executável, OHLC intrabar, custos completos e análise de
sensibilidade pertencem ao **IRAI-4 / VAL-04** — não a este artefato.

## Contagem de eventos (gate mínimo de 100 eventos por alvo)

| Sinal | WIN$N | WDO$N |
|---|---|---|
| pair | 3693 (gate OK) | 3833 (gate OK) |
| z | 119 (gate OK) | 120 (gate OK) |
| intersection | 93 (INCONCLUSIVO) | 97 (INCONCLUSIVO) |
| baseline_momentum | 2479 (gate OK) | 2535 (gate OK) |
| baseline_reversao | 2479 (gate OK) | 2535 (gate OK) |

Cada sinal testa até 24 combinações horizonte×direção; um `***` isolado NÃO é
confirmatório (ver `limitations` de cada sinal no artefato). A leitura
econômica comparativa contra os baselines é IRAI-4/VAL-04.
