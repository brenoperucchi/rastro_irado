"""
Migração DOL$N → WDO$N para o modelo WIN.
1. Copia barras M5 e D1 de DOL$N para WDO$N (sobrescrevendo)
2. Atualiza asset_models: factor DOL$N → WDO$N, label dol → wdo
3. Renomeia params win_w_dol / win_sigma_dol → win_w_wdo / win_sigma_wdo
"""
import sys; sys.path.insert(0, '.')
from backend.db import get_connection
from datetime import datetime, timezone

conn = get_connection()
cursor = conn.cursor()

# 1) Copiar barras DOL$N → WDO$N
print("=== 1) Copiando barras DOL$N -> WDO$N ===")
for tf in ['M5', 'D1']:
    count_dol = conn.execute(
        "SELECT COUNT(*) as c FROM market_bars WHERE symbol='DOL$N' AND timeframe=?", (tf,)
    ).fetchone()['c']
    
    # Apagar WDO$N existente para este TF e reinserir a partir do DOL$N
    del_count = cursor.execute(
        "DELETE FROM market_bars WHERE symbol='WDO$N' AND timeframe=?", (tf,)
    ).rowcount
    
    ins_count = cursor.execute("""
        INSERT INTO market_bars (symbol, source, timeframe, timestamp_utc, open, high, low, close, volume, real_volume)
        SELECT 'WDO$N', source, timeframe, timestamp_utc, open, high, low, close, volume, real_volume
        FROM market_bars
        WHERE symbol='DOL$N' AND timeframe=?
    """, (tf,)).rowcount
    
    print(f"  {tf}: DOL$N tinha {count_dol} barras | deletadas WDO$N {del_count} | inseridas WDO$N {ins_count}")

conn.commit()

# 2) Atualizar asset_models para WIN: DOL$N → WDO$N, label dol → wdo
import json
print()
print("=== 2) Atualizando asset_models WIN ===")
row = conn.execute("SELECT factors, factor_labels FROM asset_models WHERE slug='win'").fetchone()
if row:
    factors = json.loads(row['factors'])
    labels = json.loads(row['factor_labels'])
    print(f"  Antes: factors={factors}")
    print(f"         labels={labels}")
    
    # Substituir DOL$N por WDO$N
    new_factors = ['WDO$N' if f == 'DOL$N' else f for f in factors]
    new_labels = {('WDO$N' if k == 'DOL$N' else k): ('wdo' if v == 'dol' else v) for k, v in labels.items()}
    
    print(f"  Depois: factors={new_factors}")
    print(f"          labels={new_labels}")
    
    cursor.execute(
        "UPDATE asset_models SET factors=?, factor_labels=? WHERE slug='win'",
        (json.dumps(new_factors), json.dumps(new_labels))
    )
    conn.commit()
    print("  OK")

# 3) Renomear params win_w_dol → win_w_wdo e win_sigma_dol → win_sigma_wdo
print()
print("=== 3) Renomeando params DB ===")
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

renames = [
    ('win_w_dol', 'win_w_wdo'),
    ('win_sigma_dol', 'win_sigma_wdo'),
]
for old, new in renames:
    rows = conn.execute(
        "SELECT value, effective_from FROM model_params WHERE param_name=? ORDER BY effective_from DESC LIMIT 1",
        (old,)
    ).fetchone()
    if rows:
        # Inserir com novo nome
        cursor.execute(
            "INSERT INTO model_params (param_name, value, effective_from) VALUES (?, ?, ?)",
            (new, rows['value'], now)
        )
        # Deletar todos os antigos
        del_n = cursor.execute("DELETE FROM model_params WHERE param_name=?", (old,)).rowcount
        print(f"  {old} -> {new}  (value={rows['value']:.6f}, deletados {del_n} antigos)")
    else:
        print(f"  {old} NAO ENCONTRADO")

conn.commit()
conn.close()
print()
print("Migracao concluida. Reinicie o backend.")
