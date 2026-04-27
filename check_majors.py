import sqlite3, json

MAJORS = {'EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'}

with sqlite3.connect('data/irai.db') as conn:
    rows = conn.execute("SELECT target, factors, factor_labels FROM asset_models").fetchall()
    for target, factors_json, labels_json in rows:
        if target not in MAJORS: continue
        factors = json.loads(factors_json) if factors_json else []
        labels = json.loads(labels_json) if labels_json else {}
        dxy_in = 'DXY' in factors or 'DXY$N' in factors
        print(f"{target}: factors={factors} | DXY={dxy_in}")
