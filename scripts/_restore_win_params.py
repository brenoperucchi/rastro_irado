"""
Insere os parâmetros corretos do WIN e WDO conforme FACTOR_MAP.md.
Baseado na calibração universal que gerou os asset_models corretos.
"""
import sys, json; sys.path.insert(0, '.')
from backend.db import get_connection
from datetime import datetime, timezone

conn = get_connection()
cursor = conn.cursor()

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ── WIN$N conforme FACTOR_MAP.md seção 11 ──────────────────
# ACC 74.8% | R²=0.4671 | alpha=1.3845 | intercept=0.0245
# Fatores: DOL(-0.504), DI1(-0.481), USDMXN(-0.246), USDCAD(-0.196),
#          US30(+0.192), USDCHF(+0.150), GBPUSD(-0.028)
# Sigmas (da calibração universal):
#   dol=0.006676, di1=0.007122, usdmxn=0.003980, usdcad=0.002231,
#   us30=0.005915, usdchf=0.003842, gbpusd=0.003277

WIN_PARAMS = {
    "win_alpha":              1.384544,
    "win_intercept":          0.024489,
    "win_w_dol":             -0.504052,
    "win_w_di1":             -0.481067,
    "win_w_usdmxn":          -0.245899,
    "win_w_usdcad":          -0.195920,
    "win_w_us30":            +0.191812,
    "win_w_usdchf":          +0.149724,
    "win_w_gbpusd":          -0.027812,
    "win_sigma_dol":          0.006676,
    "win_sigma_di1":          0.007122,
    "win_sigma_usdmxn":       0.003980,
    "win_sigma_usdcad":       0.002231,
    "win_sigma_us30":         0.005915,
    "win_sigma_usdchf":       0.003842,
    "win_sigma_gbpusd":       0.003277,
}

# ── WDO$N conforme FACTOR_MAP.md seção 10 ──────────────────
# ACC 76.0% | R²=0.4763 | alpha=2.1437
# Fatores: DI1(+0.382), USDCAD(+0.295), WIN(-0.227), EURUSD(-0.221),
#          USDCHF(-0.144), US30(+0.112), BTCUSD(-0.047), BRENT(-0.030)
# (WDO params já estão corretos no banco — verificado)

print("Inserindo params do WIN conforme FACTOR_MAP.md...")
for name, value in WIN_PARAMS.items():
    cursor.execute(
        "INSERT INTO model_params (param_name, value, effective_from) VALUES (?, ?, ?)",
        (name, value, now)
    )
    print(f"  {name:<35} = {value:>10.6f}")

conn.commit()
print(f"\nFinalizado. effective_from = {now}")
print("Reinicie o backend para carregar os parametros corretos.")

# Verificar
print()
print("=== Verificacao: WIN params mais recentes ===")
rows = conn.execute("""
    SELECT mp.param_name, mp.value FROM model_params mp
    INNER JOIN (
        SELECT param_name, MAX(effective_from) as max_eff
        FROM model_params WHERE param_name LIKE 'win_%'
        GROUP BY param_name
    ) latest ON mp.param_name = latest.param_name AND mp.effective_from = latest.max_eff
    WHERE mp.param_name LIKE 'win_%'
    ORDER BY mp.param_name
""").fetchall()
for r in rows:
    print(f"  {r['param_name']:<35} = {r['value']:>10.6f}")

conn.close()
