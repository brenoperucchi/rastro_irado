import sqlite3, json

MAJORS = {'EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'}

with sqlite3.connect('data/irai.db') as conn:
    rows = conn.execute("SELECT target, factors, factor_labels FROM asset_models WHERE target IN ({})".format(
        ','.join('?' * len(MAJORS))
    ), list(MAJORS)).fetchall()

    for target, factors_json, labels_json in rows:
        factors = json.loads(factors_json) if factors_json else []
        labels = json.loads(labels_json) if labels_json else {}

        old_count = len(factors)
        new_factors = [f for f in factors if f not in ('DXY', 'DXY$N')]
        new_labels = {k: v for k, v in labels.items() if k not in ('DXY', 'DXY$N')}
        removed = old_count - len(new_factors)

        if removed > 0:
            conn.execute(
                "UPDATE asset_models SET factors = ?, factor_labels = ? WHERE target = ?",
                (json.dumps(new_factors), json.dumps(new_labels), target)
            )
            print(f"{target}: removido DXY | fatores {old_count} -> {len(new_factors)}: {new_factors}")
        else:
            print(f"{target}: DXY não encontrado, nenhuma mudança")

    conn.commit()
    print("\nPronto.")
