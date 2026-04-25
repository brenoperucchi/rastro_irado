"""
Calibra modelo WDO: DI1$N + WIN$N + BTCUSD + CHINA50 + VIX + DXY
Salva pesos no model_params com prefixo wdo_ (separado do modelo WIN).
Usa WDO$N (agora com historico proprio copiado do DOL$N).
"""
import sqlite3, os, sys
import pandas as pd
import numpy as np
from datetime import datetime
from scipy.special import expit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.db import get_connection, DB_PATH

os.environ["PYTHONIOENCODING"] = "utf-8"

# Config WDO model
TARGET = "WDO$N"  # agora com historico proprio no banco
FACTORS = ["DI1$N", "WIN$N", "BTCUSD", "CHINA50", "VIX", "DXY"]
FACTOR_LABELS = {
    "DI1$N": "di", "WIN$N": "win", "BTCUSD": "btc",
    "CHINA50": "china", "VIX": "vix", "DXY": "dxy",
}

EXPECTED_SIGNS = {
    "di": +1,     # juros sobe = dolar sobe
    "win": -1,    # ibov sobe = dolar cai
    "btc": -1,    # risk-on = dolar cai
    "china": -1,  # china sobe = risk-on = dolar cai
    "vix": +1,    # medo = dolar sobe
    "dxy": +1,    # dolar global sobe = dolar BR sobe
}

SESSION_START_UTC_HOUR = 13
SESSION_END_UTC_HOUR = 21

# Load data
conn = get_connection()
df = pd.read_sql_query(
    "SELECT symbol, timestamp_utc, open, high, low, close, real_volume FROM market_bars WHERE timeframe='M5'", conn)

df["timestamp"] = pd.to_datetime(df["timestamp_utc"])
df["hour"] = df["timestamp"].dt.hour
df["date"] = df["timestamp"].dt.date

# Session filter
mode_h = df[df["symbol"] == TARGET]["hour"].mode()
h0, h1 = (10, 18) if mode_h.iloc[0] < 13 else (13, 21)
df = df[(df["hour"] >= h0) & (df["hour"] < h1)]

# Daily returns
daily = {}
for sym in [TARGET] + FACTORS:
    s = df[df["symbol"] == sym].sort_values("timestamp")
    d = s.groupby("date").agg(o=("open", "first"), c=("close", "last"), n=("close", "count"))
    d = d[d["n"] >= 20]
    d["ret"] = (d["c"] - d["o"]) / d["o"]
    daily[sym] = d[["ret", "o", "c"]]

# Merge
target_daily = daily[TARGET]["ret"].rename("target_ret")
factor_rets = pd.DataFrame({FACTOR_LABELS[f]: daily[f]["ret"] for f in FACTORS if f in daily})
merged = pd.concat([target_daily, factor_rets], axis=1).dropna()
merged = merged.iloc[-252:]

print(f"Sessoes: {len(merged)}")
print(f"Periodo: {merged.index[0]} a {merged.index[-1]}")

# OLS
labels = list(FACTOR_LABELS.values())
X = merged[labels].values
y_ret = merged["target_ret"].values
y_dir = (y_ret > 0).astype(int)

Xb = np.column_stack([X, np.ones(len(X))])
beta = np.linalg.lstsq(Xb, y_ret, rcond=None)[0]
yp = Xb @ beta

r2 = 1 - np.sum((y_ret - yp) ** 2) / np.sum((y_ret - y_ret.mean()) ** 2)
correct = np.sum((yp > 0) == (y_ret > 0))
acc = correct / len(y_ret)

print(f"\nOLS Results:")
print(f"  R2 = {r2:.4f}")
print(f"  Directional ACC = {acc:.1%}")

weights = {}
for i, label in enumerate(labels):
    w = beta[i]
    sign_ok = "OK" if (w > 0 and EXPECTED_SIGNS.get(label, 0) > 0) or (w < 0 and EXPECTED_SIGNS.get(label, 0) < 0) else "FLIP"
    print(f"  w_{label:8s} = {w:+.6f}  ({sign_ok})")
    weights[label] = w

intercept_ols = beta[-1]
print(f"  intercept = {intercept_ols:+.6f}")

# Logistic calibration for P_up
from sklearn.linear_model import LogisticRegression

# Compute z-scores
sigmas = {}
for label in labels:
    sigmas[label] = merged[label].std()
    print(f"  sigma_{label} = {sigmas[label]:.5f}")

# Compute scores per session
scores = np.zeros(len(merged))
for i, label in enumerate(labels):
    z = merged[label].values / sigmas[label]
    scores += weights[label] * z

lr = LogisticRegression(fit_intercept=True, max_iter=1000, C=1e6)
lr.fit(scores.reshape(-1, 1), y_dir)
alpha = float(lr.coef_[0, 0])
intercept = float(lr.intercept_[0])

p_up = expit(alpha * scores + intercept) * 100
dir_pred = (p_up > 50).astype(int)
dir_acc = np.mean(dir_pred == y_dir) * 100

print(f"\nLogistic Calibration:")
print(f"  alpha = {alpha:.4f}")
print(f"  intercept = {intercept:.4f}")
print(f"  Directional ACC (logistic) = {dir_acc:.1f}%")

# Save to DB — purge old wdo_ params first to avoid hybrid model contamination
effective = datetime.utcnow().strftime("%Y-%m-%dT00:00:00Z")
params = []
for label, w in weights.items():
    params.append((f"wdo_w_{label}", w, effective))
for label, s in sigmas.items():
    params.append((f"wdo_sigma_{label}", s, effective))
params.append(("wdo_alpha", alpha, effective))
params.append(("wdo_intercept", intercept, effective))

deleted = conn.execute(
    "DELETE FROM model_params WHERE param_name LIKE 'wdo_%'"
).rowcount
if deleted:
    print(f"[purge] {deleted} params antigos de 'wdo_' removidos")

conn.executemany(
    "INSERT INTO model_params (param_name, value, effective_from) VALUES (?, ?, ?)",
    params)
conn.commit()
conn.close()

print(f"\nSaved {len(params)} WDO model params to DB")
print("Done!")
