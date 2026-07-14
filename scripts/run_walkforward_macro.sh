#!/usr/bin/env bash
# Walk-forward ancorado do macro layer — o único desenho que atinge poder estatístico.
#
# POR QUE ESTE SCRIPT EXISTE
# O Gate 3b mostrou que 49 sessões OOS não decidem nada: para detectar ΔAUC=+0,02 a
# 80% de poder são precisas 690 sessões (h=3). E as cestas INCUMBENTES nunca poderão
# chegar lá — o iSharesCurrencyBond+ (cesta do WDO) só existe desde 2025-05-27, o que
# trava a interseção em 283 sessões.
#
# A saída é uma cesta de HISTÓRIA LONGA (só fatores com >=1000 sessões): a interseção
# sobe para 1049 sessões (abr/2022..jul/2026), e oito folds acumulam cerca de 670
# sessões OOS — poder suficiente para h=3/6, não para h=20.
#
# Pode levar horas; todos os artefatos ficam fora do banco e são agregados ao final.
set -euo pipefail

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SCRATCH="/tmp/claude-1000/-home-brenoperucchi-Devs-miqueias-rastro-irado/5492199e-05a7-45f3-bc41-2c65682106d5/scratchpad"
PY="${PY:-$SCRATCH/venv/bin/python}"
SNAP="${SNAP:-$SCRATCH/irai_prod_snapshot.db}"
OUT="${OUT:-$SCRATCH/walkforward_fixed}"
BOOTSTRAP="${BOOTSTRAP:-2000}"
# Os ICs fold a fold não são usados no veredito; este orçamento serve apenas às
# tabelas diagnósticas. O IC decisório usa BOOTSTRAP sobre as predições acumuladas.
FOLD_BOOTSTRAP="${FOLD_BOOTSTRAP:-50}"
mkdir -p "$OUT"

cd "$REPO"

# Cesta de história longa. Os iShares ficam FORA de propósito: eles melhoram o fit
# mas tornam a validação impossível. Este é o trade-off explícito do experimento.
# Sem USDCAD/USDCHF: ambos começam em 2022-07-04 e encurtam desnecessariamente
# a pista. A cesta abaixo tem interseção desde 2022-04-14.
WIN_FACTORS='WDO$N,DI1$N,DE40,US500,VIX,USTEC,XAUUSD'
WDO_FACTORS='WIN$N,DI1$N,DE40,US500,VIX,USTEC,XAUUSD'

# Folds ancorados: o treino cresce, a janela OOS avança. Cada fold é honestamente
# out-of-sample (os pesos do fold i só veem dados <= cutoff_i).
CUTOFFS=(
  "2023-10-25:2023-10-26:2024-02-29"
  "2024-02-29:2024-03-01:2024-06-28"
  "2024-06-28:2024-07-01:2024-10-31"
  "2024-10-31:2024-11-01:2025-02-28"
  "2025-02-28:2025-03-03:2025-06-30"
  "2025-06-30:2025-07-01:2025-10-31"
  "2025-10-31:2025-11-03:2026-02-27"
  "2026-02-27:2026-03-02:2026-07-10"
)

echo "=== WALK-FORWARD DO MACRO LAYER ==="
echo "início: $(date -Is)"
echo "cesta profunda (sem iShares, sem USDCAD/USDCHF) — 1049 sessões, ~669 OOS"
echo "bootstrap: $FOLD_BOOTSTRAP draws diagnósticos/fold; $BOOTSTRAP draws no OOS acumulado"
echo

for fold in "${CUTOFFS[@]}"; do
  IFS=':' read -r CUT EVAL_START EVAL_END <<< "$fold"
  TAG="${CUT//-/}"
  echo "--- fold cutoff=$CUT  eval=$EVAL_START..$EVAL_END ---"

  for pair in "WIN\$N:$WIN_FACTORS:win" "WDO\$N:$WDO_FACTORS:wdo"; do
    IFS=':' read -r TGT FACTORS SLUG <<< "$pair"
    JSON="$OUT/cal_${SLUG}_${TAG}.json"

    # Calibra SÓ com dados <= cutoff. Cesta forçada (sem busca) para que o walk-forward
    # meça o MACRO, não a capacidade da busca por força bruta de sobreajustar.
    "$PY" scripts/calibrate_universal.py \
      --target "$TGT" --factors "$FACTORS" \
      --as-of "$CUT" --dry-run --db "$SNAP" \
      --output-json "$JSON" >> "$OUT/calib_${TAG}.log" 2>&1 || {
        echo "  !! calibração falhou: $TGT @ $CUT (ver $OUT/calib_${TAG}.log)"; exit 1; }
  done

  "$PY" scripts/measure_tactical_gate3.py \
    --db "$SNAP" \
    --calibration-json "$OUT/cal_win_${TAG}.json" "$OUT/cal_wdo_${TAG}.json" \
    --target 'WIN$N' 'WDO$N' \
    --cutoff "$CUT" --eval-start "$EVAL_START" --eval-end "$EVAL_END" \
    --window-name "wf_${TAG}" --version both \
    --train-sessions 120 --crossfit-folds 4 --crossfit-holdout 10 \
    --min-history-date 2022-04-14 --bootstrap "$FOLD_BOOTSTRAP" \
    --output-json "$OUT/gate3b_${TAG}.json" > "$OUT/gate3b_${TAG}.log" 2>&1 || {
      echo "  !! gate3b falhou @ $CUT (ver $OUT/gate3b_${TAG}.log)"; exit 1; }

  echo "  ok -> $OUT/gate3b_${TAG}.json"
done

echo
echo "=== FIM: $(date -Is) ==="
echo "resultados: $OUT/gate3b_*.json"
RESULTS=("$OUT"/gate3b_*.json)
"$PY" scripts/aggregate_walkforward_macro.py "${RESULTS[@]}" \
  --bootstrap "$BOOTSTRAP" --output-json "$OUT/aggregate.json" | tee "$OUT/aggregate.log"
