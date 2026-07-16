# IRAI-4 — artefato econômico PIT

Reavalia os cinco geradores do NF-01A com uma política de execução mais
realista, sem alterar o artefato histórico do IRAI-2.

## Arquivos

- `nf01_executable_pit.json.gz`: ledger completo com 18.005 eventos.
  SHA-256: `779c1e78703f5fc0bff8aca41f62c352b971b9b0756824fea07fbc9992973d8b`.
- `nf01_executable_pit_summary.json`: mesmo artefato sem eventos individuais.
  SHA-256: `62f8dc89095dd748ac6b5d050c539c0250041c82f38e35b280d3e04a6f8d782e`.
- `win-rollover-sensitivity-executable-v1.json`: sensibilidade do novo ledger
  às janelas de vencimento do WIN.

O resumo foi validado como cópia exata do artefato completo depois de remover
`events`; as contagens também coincidem. Os 18.005 eventos possuem os campos
temporais obrigatórios, sem violações de ordem, e em todos
`entry_at == signal_available_at`.

## Política econômica desta rodada

- sinal confirmado apenas em barra M5 fechada;
- entrada no `open` da M5 seguinte, primeiro tick agregado pelo MT5 em ou após
  `signal_available_at`;
- `h=3/6/10/20` encerra após exatamente esse número de barras completas desde
  a entrada;
- MFE/MAE usa `high/low` desde a própria barra de entrada;
- evento sem `open` real é rejeitado, sem fallback para `close`; `high/low`
  incompleto preserva o retorno por `close`, mas deixa MFE/MAE ausentes;
- custos reportados em `0,5x`, `1,0x`, `1,5x` e `2,0x`;
- bootstrap de 10.000 iterações, clusterizado por sessão;
- labels, MFE e MAE nunca cruzam a sessão.

OHLC não revela se stop ou alvo ocorreu primeiro quando ambos foram tocados na
mesma M5. Essa ambiguidade exige política conservadora ou replay de ticks.
Além disso, ausência de OHLC pode estar correlacionada com leilão, halt ou queda
de feed. Sem `open` de entrada, o trade é excluído mas o sinal consome cooldown;
`high/low` incompleto no caminho preserva o retorno por `close` e anula MFE/MAE.
O artefato publicado não teve eventos afetados, mas a regra e o possível viés de
seleção passam a fazer parte explícita do contrato para novas gerações.

## Comando reproduzível

```bash
python3 -X utf8 scripts/build_nf01_artifact.py \
  --db data/irai.db --targets WIN$N WDO$N --point-in-time --limit 2000 \
  --bootstrap 10000 \
  --output docs/artifacts/irai-4/nf01_executable_pit.json.gz \
  --summary-output docs/artifacts/irai-4/nf01_executable_pit_summary.json
```

Executado no Ryzen/Windows sobre o commit `8b9ac12`, já publicado em
`origin/main`. `dirty=true` registra arquivos de dados não versionados no host;
o código gerador coincide com `origin/main`.

## Leitura de negócio

### WIN

- **Pair dinâmico (3.697 eventos):** médias líquidas agregadas de -6,82,
  -7,47, -5,71 e -13,54 pontos em h=3/6/10/20; todos os IC95% incluem zero.
  Mesmo com metade do custo, todas as médias permanecem negativas.
- **Z (119 eventos):** médias positivas em h=6/10/20, mas todos os IC95%
  incluem zero. A amostra está apenas acima do mínimo e há múltiplas
  comparações; não confirma edge.
- **Pair∩Z (93 eventos):** abaixo do gate mínimo de 100; inconclusivo.
- **Baselines SMA:** momentum e reversão não apresentam edge líquido robusto.

A remoção das janelas de rollover exclui 7,06% dos eventos Pair e não cria
edge positivo. O agregado h=3 passa a significativamente negativo, indicando
que o rollover mascarava parte da perda.

### WDO

- **Pair dinâmico (3.837 eventos):** negativo e estatisticamente significativo
  em todos os horizontes e direções no custo principal. Continua
  significativamente negativo nos quatro horizontes mesmo com custo de 0,5x.
- **Z (120 eventos):** sem padrão positivo robusto; venda h=3 é negativa.
- **Pair∩Z (97 eventos):** abaixo do gate de amostra; sinais negativos não
  autorizam promoção.

### MFE/MAE

Usar extremos OHLC aumentou as excursões Pair do WIN de aproximadamente
`+292/-298` pontos por close para `+358/-367` pontos por high/low. Isso torna o
ledger útil para estudar stop e take profit, mas não transforma excursão em
edge: a sequência intrabarra e o resultado conjunto TP/SL ainda precisam ser
simulados conservadoramente.

## Veredito provisório

O realismo econômico não resgatou os markers atuais. Pair/Z permanecem
diagnósticos; nenhuma hipótese deve chegar a `CONFIRMADO` ou autorizar NF-02/
NF-03. O IRAI-4 continua aberto para o challenger de par fixo com frequência
comparável e para a regra local com/sem gate IRAI. O WDO também permanece
provisório até concluir sua auditoria de rollover.

O banco ganhou uma sessão entre o IRAI-2 e esta rodada. A comparação isolada
nos eventos comuns confirmou o mesmo veredito; as diferenças principais vêm
da política de execução, não dos quatro eventos adicionais por perna.
