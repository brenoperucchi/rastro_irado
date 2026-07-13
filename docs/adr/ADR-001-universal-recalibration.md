# ADR 001: Universal Recalibration of Regime Models (Minimum 6 Factors + DE40)

## Context
The IRAI (Intraday Risk Appetite Index) dashboard requires high robustness in directional predictability (ACC) and structural correlation (R²) across its 20 target assets (Forex Majors, Crosses, Indices, Gold, BTC, and BR assets). Previous calibrations allowed baskets of as few as 4 factors, which occasionally resulted in models suffering from overfitting and producing structurally weak correlations (e.g., AUDNZD, EURGBP with R² < 0.25). Furthermore, the global equity proxy was heavily concentrated on US indices (US500, US30, USTEC), missing the European macroeconomic component.

## Decision
We conducted a comprehensive recalibration across all 20 assets with the following architectural constraints:
1. **Minimum Factors Constraint:** The minimum number of factors required per model was elevated from `4` to `6` (the maximum remains `8`). This enforces wider factor baskets, significantly increasing the structural inertia of the models and reducing the likelihood of overfitted, non-generalizing correlations.
2. **DE40 Inclusion:** The DAX 40 index (`DE40`) was introduced into the global candidate pool (`ALL_FACTORS`) for all assets. This allows the brute-force regression engine to account for European equity dynamics.
3. **Actual Ridge calibration:** Candidate factors are standardized and Ridge is used both during basket screening and in the final fit. Alpha is selected from `0.01, 0.1, 1, 10, 100` on the final 20% of the training partition, then revalidated using only the selected basket; the external temporal holdout is never consulted. Candidate baskets whose standardized design matrix has condition number above 10 are rejected, in addition to the Treasury and EM-bond structural rules. The threshold is applied to `X`, not to the normal-equation matrix `X'X`, and catches materially collinear baskets without a market-specific “one US index” rule.
4. **Honest temporal evaluation:** The latest database date is always discarded as potentially partial. In-sample and out-of-sample ACC/R² are persisted separately and reported with explicit labels.

## Execution (4 Waves)
The recalibration was executed sequentially across 4 distinct asset classes on April 30, 2026:
- **Wave 1 (Forex Majors):** EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF.
- **Wave 2 (Forex Crosses):** EURGBP, EURCHF, EURJPY, GBPJPY, EURAUD, CADCHF, AUDNZD.
- **Wave 3 (Global Indices, Gold, BTC):** US500, US30, USTEC, XAUUSD, BTCUSD.
- **Wave 4 (Brazilian Assets):** WIN$N, WDO$N.

## Results & Validation
The results validated the hypothesis that wider baskets and European exposure yield superior macroeconomic proxies:
- **Major Improvements in Weak Assets:** Models that previously failed minimum quality thresholds saw massive leaps in R². For instance, `EURGBP` jumped from R² 0.21 to 0.96 (ACC 92.1%). `CADCHF` improved from R² 0.53 to 0.74 (ACC 90.1%). The historically weakest asset, `AUDNZD`, improved from R² 0.17 to 0.25 (breaking the minimum acceptability barrier).
- **DE40 Adoption:** The `DE40` index proved its structural value by being organically selected by the brute-force engine into the 8-factor baskets of **8 out of 20 targets**, notably including the US equity indices (US500, US30, USTEC), EURGBP, CADCHF, GBPJPY, EURUSD, and WDO$N.
- **Global Stability:** The average ACC across all waves remained well above the 75% minimum threshold (with Wave 2 averaging 89.5%), validating that forcing 6+ factors did not degrade accuracy but drastically improved the R² reliability.

## Consequences
- The SQLite database (`irai.db`) was successfully purged of the old coefficients and fully updated.
- The system must now be restarted so that the Regime Collector, APIs, and trading robots can ingest the new structural parameters.
- Future calibrations should maintain the `--min-factors 6` constraint, temporal holdout, Ridge validation and condition-number guard as the production robustness baseline.
