"""
Verifica o que está registrado em asset_models para WIN e WDO.
Compara com o FACTOR_MAP.md.
"""
import sys, json; sys.path.insert(0, '.')
from backend.db import get_connection

conn = get_connection()

for slug in ['win', 'wdo']:
    print(f"=== {slug.upper()} asset_models ===")
    r = conn.execute(
        "SELECT target, slug, display_name, factors, factor_labels, "
        "session_start_h, session_end_h, accuracy, r_squared "
        "FROM asset_models WHERE slug=?", (slug,)
    ).fetchone()
    if r:
        print(f"  target:    {r['target']}")
        print(f"  acc:       {r['accuracy']}")
        print(f"  r2:        {r['r_squared']}")
        print(f"  session:   {r['session_start_h']}h - {r['session_end_h']}h")
        print(f"  factors:   {json.loads(r['factors'])}")
        print(f"  labels:    {json.loads(r['factor_labels'])}")
    print()

# Mostrar quais params win_ existem com qual fator
print("=== Params win_ mais recentes (por param) ===")
rows = conn.execute("""
    SELECT mp.param_name, mp.value, mp.effective_from
    FROM model_params mp
    INNER JOIN (
        SELECT param_name, MAX(effective_from) as max_eff
        FROM model_params WHERE param_name LIKE 'win_%'
        GROUP BY param_name
    ) latest ON mp.param_name = latest.param_name AND mp.effective_from = latest.max_eff
    WHERE mp.param_name LIKE 'win_%'
    ORDER BY mp.param_name
""").fetchall()
for r in rows:
    print(f"  {r['param_name']:<35} = {r['value']:>10.6f}  [{r['effective_from']}]")

conn.close()
