import sqlite3
import pandas as pd

c = sqlite3.connect('data/irai.db')

print("--- TIMESTAMPS ---")
df = pd.read_sql("SELECT symbol, timestamp_utc, open FROM market_bars WHERE symbol='WIN$N' ORDER BY timestamp_utc DESC LIMIT 5", c)
print('WIN:\n', df)

df2 = pd.read_sql("SELECT symbol, timestamp_utc, open FROM market_bars WHERE symbol='US500' ORDER BY timestamp_utc DESC LIMIT 5", c)
print('\nUS500:\n', df2)

c.close()
