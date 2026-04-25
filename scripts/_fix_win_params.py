"""
Fix WIN model_params: remove params contaminados de outras calibrações
e mantém apenas os da última calibração M5 legítima (win_w_wdo, win_w_di, etc.)
"""
import sys; sys.path.insert(0, '.')
from backend.db import get_connection
from datetime import datetime, timezone

conn = get_connection()
cursor = conn.cursor()

# Identificar os params legítimos do WIN (calibração B3 com fatores corretos)
# Fatores esperados: wdo, di, vix, brent, dxy, china, mxn, iv
WIN_VALID_FACTORS = {'wdo', 'di', 'vix', 'brent', 'dxy', 'china', 'mxn', 'iv'}

# Buscar todos os params com prefixo win_
all_win = conn.execute(
    "SELECT DISTINCT param_name FROM model_params WHERE param_name LIKE 'win_%' ORDER BY param_name"
).fetchall()

print("=== Todos os params win_ no banco ===")
to_delete = []
to_keep = []
for r in all_win:
    name = r['param_name']
    clean = name[4:]  # remove 'win_'
    
    # Verificar se é um param legítimo
    is_valid = False
    if clean in ('alpha', 'intercept'):
        is_valid = True
    elif clean.startswith('w_'):
        factor = clean[2:]
        is_valid = factor in WIN_VALID_FACTORS
    elif clean.startswith('sigma_'):
        rest = clean.replace('sigma_', '').replace('_session', '').replace('_daily', '')
        is_valid = rest in WIN_VALID_FACTORS
    
    status = "KEEP" if is_valid else "DEL "
    print(f"  {status}  {name}")
    
    if is_valid:
        to_keep.append(name)
    else:
        to_delete.append(name)

print(f"\n  Manter: {len(to_keep)} | Deletar: {len(to_delete)}")

if to_delete:
    print(f"\n  Deletando {len(to_delete)} params contaminados...")
    for name in to_delete:
        cursor.execute("DELETE FROM model_params WHERE param_name = ?", (name,))
    conn.commit()
    print("  Feito!")
else:
    print("  Nenhum param contaminado encontrado.")

# Verificar o que sobrou
print("\n=== Params win_ restantes ===")
remaining = conn.execute(
    "SELECT param_name, value, effective_from FROM model_params "
    "WHERE param_name LIKE 'win_%' ORDER BY param_name, effective_from DESC"
).fetchall()
for r in remaining:
    print(f"  {r['param_name']:<35} = {r['value']:>10.6f}  [{r['effective_from']}]")

conn.close()
print("\nPronto. Reinicie o backend para recarregar os parâmetros.")
