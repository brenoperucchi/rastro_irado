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
# sobe para 996 sessões (jul/2022..jul/2026) e o walk-forward acumula 746 OOS — o
# bastante para h=3, marginal para h=6, e jamais para h=20 (precisaria de ~22 anos).
#
# Roda em background por HORAS. Não bloqueia o worker do codex.
set -euo pipefail

REPO="/home/brenoperucchi/Devs/miqueias/rastro_irado"
SCRATCH="/tmp/claude-1000/-home-brenoperucchi-Devs-miqueias-rastro-irado/5492199e-05a7-45f3-bc41-2c65682106d5/scratchpad"
PY="$SCRATCH/venv/bin/python"
SNAP="$SCRATCH/irai_prod_snapshot.db"
OUT="$SCRATCH/walkforward"
mkdir -p "$OUT"

cd "$REPO"

# Cesta de história longa. Os iShares ficam FORA de propósito: eles melhoram o fit
# mas tornam a validação impossível. Este é o trade-off explícito do experimento.
WIN_FACTORS='WDO$N,DI1$N,DE40,US500,VIX,USTEC,XAUUSD,USDCAD,USDCHF'
WDO_FACTORS='WIN$N,DI1$N,DE40,US500,VIX,USTEC,XAUUSD,USDCAD,USDCHF'

# Folds ancorados: o treino cresce, a janela OOS avança. Cada fold é honestamente
# out-of-sample (os pesos do fold i só veem dados <= cutoff_i).
CUTOFFS=(
  "2023-06-30:2023-07-03:2023-12-29"
  "2023-12-29:2024-01-02:2024-06-28"
  "2024-06-28:2024-07-01:2024-12-30"
  "2024-12-30:2025-01-02:2025-06-30"
  "2025-06-30:2025-07-01:2025-12-31"
  "2025-12-31:2026-01-02:2026-07-10"
)

echo "=== WALK-FORWARD DO MACRO LAYER ==="
echo "início: $(date -Is)"
echo "cesta longa (sem iShares) — 996 sessões de interseção, 746 OOS acumuladas"
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
        echo "  !! calibração falhou: $TGT @ $CUT (ver $OUT/calib_${TAG}.log)"; continue; }
  done

  "$PY" scripts/measure_tactical_gate3.py \
    --db "$SNAP" \
    --calibration-json "$OUT/cal_win_${TAG}.json" "$OUT/cal_wdo_${TAG}.json" \
    --target 'WIN$N' 'WDO$N' \
    --cutoff "$CUT" --eval-start "$EVAL_START" --eval-end "$EVAL_END" \
    --window-name "wf_${TAG}" --version both \
    --train-sessions 120 --crossfit-folds 4 --crossfit-holdout 10 \
    --bootstrap 2000 \
    --output-json "$OUT/gate3b_${TAG}.json" > "$OUT/gate3b_${TAG}.log" 2>&1 || {
      echo "  !! gate3b falhou @ $CUT (ver $OUT/gate3b_${TAG}.log)"; continue; }

  echo "  ok -> $OUT/gate3b_${TAG}.json"
done

echo
echo "=== FIM: $(date -Is) ==="
echo "resultados: $OUT/gate3b_*.json"
echo "Agregue os folds e some as sessões OOS antes de qualquer veredito —"
echo "um fold isolado tem o mesmo problema de poder que o Gate 3b tinha."
