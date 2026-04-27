"""
Recalibração dos pares de moedas major SEM DXY.
DXY é excluído porque os majors compõem o próprio índice.
"""
import sqlite3, json, sys, os
from itertools import combinations
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.special import expit
from sklearn.linear_model import LogisticRegression

os.environ["PYTHONIOENCODING"] = "utf-8"

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "irai.db")

MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"]

# Candidatos sem DXY, sem BR
CANDIDATE_FACTORS = [
    "BRENT", "USDMXN", "VIX",
    "US500", "US30", "USTEC", "XAUUSD", "BTCUSD",
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF",
]

def load_daily_returns(conn, session_start_h, session_end_h):
    df = pd.read_sql_query(
        "SELECT symbol, timestamp_utc, open, close FROM market_bars WHERE timeframe='M5'",
        conn,
    )
    df["timestamp"] = pd.to_datetime(df["timestamp_utc"])
    df["hour"] = df["timestamp"].dt.hour
    df["date"] = df["timestamp"].dt.date
    if session_end_h != 24:
        df = df[(df["hour"] >= session_start_h) & (df["hour"] < session_end_h)]
    daily = {}
    for sym in df["symbol"].unique():
        s = df[df["symbol"] == sym].sort_values("timestamp")
        d = s.groupby("date").agg(o=("open", "first"), c=("close", "last"), n=("close", "count"))
        d = d[d["n"] >= 10]
        d["ret"] = (d["c"] - d["o"]) / d["o"]
        daily[sym] = d["ret"]
    return daily


def calibrate_target(conn, target, session_start_h, session_end_h, min_factors=4, max_factors=7):
    print(f"\n{'='*60}")
    print(f"  Calibrando: {target}  (sem DXY)")
    print(f"  Sessao: {session_start_h:02d}-{session_end_h:02d} UTC")
    print(f"{'='*60}")

    daily = load_daily_returns(conn, session_start_h, session_end_h)

    if target not in daily:
        print(f"  ERRO: sem dados para {target}")
        return None

    target_ret = daily[target].rename("target")
    exclude = {target}
    available_factors = [f for f in CANDIDATE_FACTORS if f in daily and f not in exclude]
    print(f"  Fatores candidatos (sem DXY): {available_factors}")

    merged_all = pd.DataFrame({"target": target_ret})
    label_map = {}
    for f in available_factors:
        label = f.replace("$N", "").lower()
        merged_all[label] = daily[f]
        label_map[f] = label
    merged_all = merged_all.dropna().iloc[-252:]
    print(f"  Sessoes merged: {len(merged_all)}")

    if len(merged_all) < 100:
        print(f"  ERRO: dados insuficientes ({len(merged_all)} < 100)")
        return None

    y_ret = merged_all["target"].values
    y_dir = (y_ret > 0).astype(int)
    tss = np.sum((y_ret - y_ret.mean()) ** 2)

    available_labels = [label_map[f] for f in available_factors]
    best_score = -float("inf")
    best_result = None
    total_combos = 0

    for n_factors in range(min_factors, min(max_factors + 1, len(available_factors) + 1)):
        for combo in combinations(range(len(available_factors)), n_factors):
            total_combos += 1
            labels = [available_labels[i] for i in combo]
            factors = [available_factors[i] for i in combo]
            X = merged_all[labels].values
            Xb = np.column_stack([X, np.ones(len(X))])
            try:
                beta = np.linalg.lstsq(Xb, y_ret, rcond=None)[0]
            except Exception:
                continue
            yp = Xb @ beta
            acc = np.mean((yp > 0) == (y_ret > 0))
            r2 = 1 - np.sum((y_ret - yp) ** 2) / tss
            score = (acc * 0.7) + (max(0, r2) * 0.3)
            if score > best_score:
                best_score = score
                best_result = {"factors": factors, "labels": labels, "beta": beta, "r2": r2, "acc": acc}

    print(f"  Testadas: {total_combos:,} combinacoes")
    if not best_result:
        return None

    print(f"  Melhor: {len(best_result['factors'])} fatores, ACC={best_result['acc']:.1%}, R2={best_result['r2']:.4f}")
    print(f"  Fatores: {', '.join(best_result['factors'])}")

    labels = best_result["labels"]
    beta = best_result["beta"]
    weights = {}
    sigmas = {}
    for i, label in enumerate(labels):
        weights[label] = beta[i]
        sigmas[label] = float(merged_all[label].std())
        print(f"    w_{label:10s} = {beta[i]:+.6f}  s={sigmas[label]:.5f}")

    scores = np.zeros(len(merged_all))
    for i, label in enumerate(labels):
        z = merged_all[label].values / sigmas[label]
        scores += weights[label] * z

    lr = LogisticRegression(fit_intercept=True, max_iter=1000, C=1e6)
    lr.fit(scores.reshape(-1, 1), y_dir)
    alpha = float(lr.coef_[0, 0])
    intercept = float(lr.intercept_[0])
    p_up = expit(alpha * scores + intercept) * 100
    dir_acc = np.mean((p_up > 50).astype(int) == y_dir) * 100
    print(f"  Logistic: alpha={alpha:.4f}, intercept={intercept:.4f}, ACC={dir_acc:.1f}%")

    return {
        "factors": best_result["factors"],
        "labels": labels,
        "factor_labels": {f: l for f, l in zip(best_result["factors"], labels)},
        "weights": weights,
        "sigmas": sigmas,
        "alpha": alpha,
        "intercept": intercept,
        "r2": best_result["r2"],
        "accuracy": best_result["acc"] * 100,
        "logistic_acc": dir_acc,
        "n_sessions": len(merged_all),
    }


def save_to_db(conn, target, slug, result):
    effective = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prefix = f"{slug}_"
    deleted = conn.execute("DELETE FROM model_params WHERE param_name LIKE ?", (f"{prefix}%",)).rowcount
    print(f"  [purge] {deleted} params antigos removidos")
    params = []
    for label, w in result["weights"].items():
        params.append((f"{prefix}w_{label}", w, effective))
    for label, s in result["sigmas"].items():
        params.append((f"{prefix}sigma_{label}", s, effective))
    params.append((f"{prefix}alpha", result["alpha"], effective))
    params.append((f"{prefix}intercept", result["intercept"], effective))
    conn.executemany("INSERT INTO model_params (param_name, value, effective_from) VALUES (?, ?, ?)", params)
    conn.execute("""
        UPDATE asset_models SET
            factors = ?, factor_labels = ?,
            accuracy = ?, r_squared = ?, n_sessions = ?,
            calibrated_at = ?
        WHERE target = ?
    """, (
        json.dumps(result["factors"]),
        json.dumps(result["factor_labels"]),
        result["accuracy"],
        result["r2"],
        result["n_sessions"],
        effective,
        target,
    ))
    conn.commit()
    print(f"  OK: {len(params)} params salvos")


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    summary = []
    for target in MAJORS:
        row = conn.execute("SELECT * FROM asset_models WHERE target=?", (target,)).fetchone()
        if not row:
            print(f"SKIP {target}: nao encontrado no banco")
            continue
        result = calibrate_target(conn, target, row["session_start_h"], row["session_end_h"])
        if result:
            save_to_db(conn, target, row["slug"], result)
            summary.append((target, result["accuracy"], result["r2"], result["logistic_acc"], len(result["factors"])))
        else:
            summary.append((target, None, None, None, 0))
    conn.close()

    print(f"\n{'='*60}")
    print(f"  RESUMO FINAL (sem DXY)")
    print(f"{'='*60}")
    print(f"  {'Target':10s} {'ACC':>8s} {'R2':>8s} {'LogACC':>8s} {'#F':>4s}")
    for t, acc, r2, lacc, nf in summary:
        if acc:
            print(f"  {t:10s} {acc:7.1f}% {r2:7.4f} {lacc:7.1f}% {nf:3d}")
        else:
            print(f"  {t:10s}  FALHOU")


if __name__ == "__main__":
    main()
