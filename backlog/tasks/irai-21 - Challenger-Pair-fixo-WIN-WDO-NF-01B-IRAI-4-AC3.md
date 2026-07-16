---
id: IRAI-21
title: Challenger Pair fixo WIN-WDO (NF-01B / IRAI-4 AC#3)
status: Done
assignee:
  - '@claude'
created_date: '2026-07-16 15:04'
updated_date: '2026-07-16 15:43'
labels:
  - tactical
  - validation
  - challenger
dependencies: []
priority: high
ordinal: 21000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Executa o challenger Pair FIXO WIN-WDO como contraste ao Pair dinâmico (par escolhido pelo Kalman). Independente do engine/calibração (não sofre C1-a): lê WIN e WDO do market_bars, beta OLS rolling, mesma entrada executável (open da barra seguinte) e custos do Pair dinâmico. Comparação bruta e com frequência equivalente contra Pair dinâmico e baselines momentum/reversão. Metodologia congelada ANTES dos resultados. Artefato separado. Referencia IRAI-4 AC#3 (que fica com o codex no braço executável).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Metodologia congelada e commitada antes de qualquer resultado
- [x] #2 Par fixo WIN-WDO usa a mesma entrada executável e custos do Pair dinâmico
- [x] #3 Compara challenger vs Pair dinâmico vs baselines, bruta e com frequência equivalente
- [x] #4 Challenger é causal (sem lookahead) e reproduzível, com testes permanentes
- [x] #5 Artefato JSON separado versionado + comando/hash/limitações
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Challenger Pair fixo WIN-WDO concluído.

METODOLOGIA CONGELADA ANTES DOS RESULTADOS: docs/plans/2026-07-16-challenger-
pair-fixo-win-wdo.md, commit 06f20ba (antes de qualquer execução). Só ajustada
depois no eixo temporal (deslocamento +offset, ainda antes dos resultados) e na
ressalva de janela; §6 (resultados) adicionada após rodar.

IMPLEMENTAÇÃO (sem tocar em measure_pair_signal_value.py, que o codex edita no
IRAI-4): scripts/measure_pair_fixed_value.py força o par WIN<->WDO, computado
INDEPENDENTE do engine/Kalman/calibração (lê market_bars, beta OLS rolling 20 sem
intercepto, reusa zscore.py inalterado). Mesma entrada executável (open da barra
seguinte), custos, MFE/MAE OHLC e 4 timestamps causais do dinâmico via
extract_trade_outcomes; agregação via run() injetado por patch local.

COMPARAÇÃO (scripts/build_challenger_artifact.py): challenger vs pair dinâmico +
baselines do artefato irai-4. Bruta (retorno médio/evento) + frequência
equivalente (expectativa por sessão = média/evento × eventos/sessão).

RESULTADO (h=6, líquido de custo, produção --limit 2000):
  WIN$N: pair_fixo -10.89*** (exp/sessão -43.24) vs pair dinâmico -7.47
         (-30.04); momentum -12.39***, reversao -7.61.
  WDO$N: pair_fixo -0.84*** (-3.36) vs pair -1.00*** (-4.18); momentum
         -0.90***, reversao -1.10***.
TODOS negativos em ambos os alvos. Fixar o par NÃO recupera edge — em WIN$N é
mais negativo que o dinâmico, em WDO$N parecidos e adversos. Regra simples não
vence a complexa: as duas perdem. Controle limpo (sem C1-a) que reforça o achado
central do NF-01.

ARTEFATO SEPARADO: docs/artifacts/irai-21/ (summary legível + completo gzipado
com 10464 eventos + README). git.commit == origin_main == 5b67100 (localizável).
Comando/hash/parâmetros/limitações no artefato.

RESSALVA registrada: janelas de medição não idênticas (challenger toda a base
~1250 sessões; dinâmico PIT ~880 de 2022-12+). Expectativa por sessão normaliza a
frequência, não a janela temporal.

TESTES: 270 passed, 18 skipped. Novos: tests/test_measure_pair_fixed_value.py
(beta OLS, alinhamento WIN-WDO, marker edge-triggered causal, timestamp
deslocado, integração run_fixed) e tests/test_build_challenger_artifact.py
(comparação bruta+por-sessão, metadata, ressalva de janela). Commits: 06f20ba,
5b67100, 25fba06.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
created: 2026-07-16 15:39
---
Review /fable-reasoner aplicada (SOMENTE o challenger IRAI-21; measure_pair_signal_value.py do codex NÃO tocado).

Veredito do fable: GO em A-D,F,G; NO-GO parcial só no ranking do README (item E) por comparar janelas diferentes. Correções aplicadas (commits 7a24348, 25d57d5):

E (crítico): pair_fixo_windowed — recorta o challenger na MESMA janela do dinâmico PIT (session_date > 1º cutoff) e re-bootstrapa. O ranking se CONFIRMA apples-to-apples: WIN$N challenger -11.02 vs dinâmico -7.47 (a diferença de janela NÃO explicava o gap); WDO$N -0.72 vs -1.00, ambos adversos. A conclusão 'todos negativos, nenhum edge' e o ranking WIN$N agora estão sustentados pela comparação de mesma janela.

B: data_quality no relatório — descarte por alinhamento (barras WIN xor WDO) quantificado e NEGLIGÍVEL na base real: WIN 32/138646 (0,02%), WDO 1279/139790 (0,9%), 0 sessões vazias.

A: comentário sobre o lookahead INERTE de t_frac (run() não lê). C: limitação sobre ausência do guarda de σ-quase-nula vs o dinâmico. F: redação corrigida (buy<->sell direto também dispara).

G (gaps de teste): +test_anti_lookahead_prefixo_identico_ao_completo (prova DIRETA da causalidade: markers das K primeiras barras idênticos em prefixo vs sessão completa), +isolamento entre sessões (não vaza resíduo), +offset de inverno (5h), +data_quality. Suíte: 288 passed, 18 skipped.

Fica em Review.
---

author: codex
created: 2026-07-16 15:43
---
Revisão independente concluída: GO. Conferidos o recorte pair_fixo_windowed na mesma janela do Pair dinâmico PIT, o re-bootstrap por sessão, a causalidade por prefixo, o isolamento entre sessões e o diagnóstico de alinhamento. Validação local: python3 -m pytest -q tests/test_measure_pair_fixed_value.py tests/test_build_challenger_artifact.py -> 19 passed. Conclusão econômica preservada: Pair fixo não recupera edge; não promover como setup.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Challenger Pair fixo WIN-WDO validado e encerrado. A comparação apples-to-apples confirma retorno líquido negativo: WIN h=6 -11,02 pontos/evento (IC95% exclui zero) contra -7,47 do Pair dinâmico; WDO -0,72 contra -1,00, ambos adversos. O controle simples e independente de calibração reforça que o marker Pair não deve ser promovido.
<!-- SECTION:FINAL_SUMMARY:END -->
