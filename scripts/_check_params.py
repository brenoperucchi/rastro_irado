import sys; sys.path.insert(0, '.')
from backend.db import get_connection
conn = get_connection()

print("=== TODOS os model_params ===")
rows = conn.execute("SELECT param_name, value, effective_from FROM model_params ORDER BY effective_from DESC, param_name").fetchall()
for r in rows:
    print(f"  {r['param_name']:<35} = {r['value']:>12.6f}  [{r['effective_from']}]")

print()
print("=== asset_models ativos ===")
rows2 = conn.execute("SELECT slug, target, display_name, accuracy, r_squared FROM asset_models WHERE active=1 ORDER BY slug").fetchall()
for r in rows2:
    print(f"  {r['slug']:<12} | {r['target']:<12} | acc={r['accuracy']} r2={r['r_squared']}")

conn.close()
