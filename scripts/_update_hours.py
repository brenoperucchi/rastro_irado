import sqlite3

c = sqlite3.connect('data/irai.db')

# Brasil: 09h a 18h
br_targets = ['WIN$N', 'WDO$N']
c.execute(f"UPDATE asset_models SET session_start_h=9, session_end_h=18 WHERE target IN ({','.join(['?']*len(br_targets))})", br_targets)

# Internacional: 03h a 22h
c.execute("UPDATE asset_models SET session_start_h=3, session_end_h=22 WHERE target NOT IN (?, ?)", br_targets)

c.commit()

# Check
rows = c.execute("SELECT target, session_start_h, session_end_h FROM asset_models").fetchall()
for r in rows:
    print(f"{r[0]}: {r[1]}h - {r[2]}h")

c.close()
