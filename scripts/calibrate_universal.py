"""
Calibração Universal IRAI — Brute-force automático para qualquer ativo.

Uso:
    python scripts/calibrate_universal.py --target US500
    python scripts/calibrate_universal.py --target XAUUSD
    python scripts/calibrate_universal.py --all          # calibra todos pendentes
    python scripts/calibrate_universal.py --all --force   # recalibra todos
"""
import sqlite3, json, sys, os, argparse
from itertools import combinations
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.special import expit

os.environ["PYTHONIOENCODING"] = "utf-8"

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "irai.db")

# Todos os possíveis fatores
ALL_FACTORS = [
    "WIN$N", "DOL$N", "DI1$N",
    "DXY", "BRENT", "CHINA50", "USDMXN", "VIX", "BTCUSD",
    "US500", "US30", "USTEC", "XAUUSD",
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF",
]

# Alias: target lógico → símbolo nos dados
ALIASES = {"WDO$N": "DOL$N"}


def load_daily_returns(conn, session_start_h, session_end_h):
    """Carrega retornos diários de todos os símbolos disponíveis."""
    df = pd.read_sql_query(
        "SELECT symbol, timestamp_utc, open, close FROM market_bars WHERE timeframe='M5'",
        conn,
    )
    df["timestamp"] = pd.to_datetime(df["timestamp_utc"])
    df["hour"] = df["timestamp"].dt.hour
    df["date"] = df["timestamp"].dt.date

    # Filtrar por sessão
    if session_end_h == 24:
        # 24h — sem filtro de hora
        pass
    else:
        df = df[(df["hour"] >= session_start_h) & (df["hour"] < session_end_h)]

    daily = {}
    for sym in df["symbol"].unique():
        s = df[df["symbol"] == sym].sort_values("timestamp")
        d = s.groupby("date").agg(o=("open", "first"), c=("close", "last"), n=("close", "count"))
        d = d[d["n"] >= 10]  # mínimo de barras
        d["ret"] = (d["c"] - d["o"]) / d["o"]
        daily[sym] = d["ret"]

    return daily


def calibrate_target(conn, target, session_start_h=0, session_end_h=24, 
                     data_proxy=None, min_factors=4, max_factors=8):
    """
    Brute-force: testa todas combinações de fatores para o target.
    Retorna: best_factors, best_labels, weights, sigmas, alpha, intercept, r2, accuracy
    """
    data_sym = data_proxy or ALIASES.get(target, target)
    
    print(f"\n{'='*60}")
    print(f"  Calibrando: {target} (dados: {data_sym})")
    print(f"  Sessão: {session_start_h:02d}-{session_end_h:02d} UTC")
    print(f"{'='*60}")

    daily = load_daily_returns(conn, session_start_h, session_end_h)

    if data_sym not in daily:
        print(f"  ❌ Sem dados para {data_sym}")
        return None

    target_ret = daily[data_sym].rename("target")

    # Fatores candidatos = todos menos o target e seu proxy
    exclude = {target, data_sym}
    available_factors = [f for f in ALL_FACTORS if f in daily and f not in exclude]
    
    print(f"  Fatores disponíveis: {len(available_factors)}")
    print(f"  Sessões target: {len(target_ret)}")

    if len(available_factors) < min_factors:
        print(f"  ❌ Poucos fatores ({len(available_factors)} < {min_factors})")
        return None

    # Últimos 252 dias úteis
    merged_all = pd.DataFrame({"target": target_ret})
    for f in available_factors:
        label = f.replace("$N", "").lower()
        merged_all[label] = daily[f]
    merged_all = merged_all.dropna().iloc[-252:]
    
    print(f"  Sessões merged: {len(merged_all)}")

    if len(merged_all) < 100:
        print(f"  ❌ Poucos dados ({len(merged_all)} < 100)")
        return None

    y_ret = merged_all["target"].values
    y_dir = (y_ret > 0).astype(int)

    # Brute force
    best_acc = 0.0
    best_result = None
    total_combos = 0

    factor_labels_map = {f: f.replace("$N", "").lower() for f in available_factors}
    available_labels = [factor_labels_map[f] for f in available_factors]

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
            correct = np.sum((yp > 0) == (y_ret > 0))
            acc = correct / len(y_ret)

            if acc > best_acc:
                r2 = 1 - np.sum((y_ret - yp) ** 2) / np.sum((y_ret - y_ret.mean()) ** 2)
                best_acc = acc
                best_result = {
                    "factors": factors,
                    "labels": labels,
                    "beta": beta,
                    "r2": r2,
                    "acc": acc,
                    "n_factors": n_factors,
                }

    if best_result is None:
        print(f"  ❌ Nenhum resultado válido")
        return None

    print(f"  Testadas: {total_combos:,} combinações")
    print(f"  🏆 Melhor: {best_result['n_factors']} fatores, ACC={best_result['acc']:.1%}, R²={best_result['r2']:.4f}")
    print(f"  Fatores: {', '.join(best_result['factors'])}")

    # Calibrar sigmas e logistic
    labels = best_result["labels"]
    beta = best_result["beta"]
    
    weights = {}
    sigmas = {}
    for i, label in enumerate(labels):
        weights[label] = beta[i]
        sigmas[label] = float(merged_all[label].std())
        print(f"    w_{label:8s} = {beta[i]:+.6f}  σ={sigmas[label]:.5f}")

    # Logistic calibration
    scores = np.zeros(len(merged_all))
    for i, label in enumerate(labels):
        z = merged_all[label].values / sigmas[label]
        scores += weights[label] * z

    from sklearn.linear_model import LogisticRegression
    lr = LogisticRegression(fit_intercept=True, max_iter=1000, C=1e6)
    lr.fit(scores.reshape(-1, 1), y_dir)
    alpha = float(lr.coef_[0, 0])
    intercept = float(lr.intercept_[0])

    p_up = expit(alpha * scores + intercept) * 100
    dir_acc = np.mean((p_up > 50).astype(int) == y_dir) * 100

    print(f"  Logistic: α={alpha:.4f}, intercept={intercept:.4f}, ACC={dir_acc:.1f}%")

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
    """Salva pesos no model_params e atualiza asset_models."""
    effective = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prefix = f"{slug}_"

    params = []
    for label, w in result["weights"].items():
        params.append((f"{prefix}w_{label}", w, effective))
    for label, s in result["sigmas"].items():
        params.append((f"{prefix}sigma_{label}", s, effective))
    params.append((f"{prefix}alpha", result["alpha"], effective))
    params.append((f"{prefix}intercept", result["intercept"], effective))

    conn.executemany(
        "INSERT OR REPLACE INTO model_params (param_name, value, effective_from) VALUES (?, ?, ?)",
        params,
    )

    # Atualizar asset_models
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
    print(f"  ✅ Salvos {len(params)} params (prefix='{prefix}') + asset_models atualizado")


def main():
    parser = argparse.ArgumentParser(description="Calibração Universal IRAI")
    parser.add_argument("--target", type=str, help="Símbolo alvo (ex: US500)")
    parser.add_argument("--all", action="store_true", help="Calibrar todos os targets")
    parser.add_argument("--force", action="store_true", help="Recalibrar mesmo já calibrados")
    parser.add_argument("--min-factors", type=int, default=4)
    parser.add_argument("--max-factors", type=int, default=8)
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if args.all:
        if args.force:
            rows = conn.execute("SELECT * FROM asset_models WHERE active=1").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM asset_models WHERE active=1 AND (calibrated_at IS NULL OR accuracy IS NULL)"
            ).fetchall()
        targets = [(r["target"], r["slug"], r["session_start_h"], r["session_end_h"], r["data_proxy"]) for r in rows]
    elif args.target:
        row = conn.execute("SELECT * FROM asset_models WHERE target=?", (args.target,)).fetchone()
        if row:
            targets = [(row["target"], row["slug"], row["session_start_h"], row["session_end_h"], row["data_proxy"])]
        else:
            print(f"Target {args.target} not found in asset_models")
            return
    else:
        parser.print_help()
        return

    print(f"\n🎯 Calibrando {len(targets)} targets...")

    results_summary = []
    for target, slug, s_start, s_end, proxy in targets:
        result = calibrate_target(
            conn, target, s_start, s_end, proxy,
            args.min_factors, args.max_factors,
        )
        if result:
            save_to_db(conn, target, slug, result)
            results_summary.append((target, result["accuracy"], result["r2"], result["logistic_acc"], len(result["factors"])))
        else:
            results_summary.append((target, None, None, None, 0))

    # Summary
    print(f"\n{'='*60}")
    print(f"  RESUMO CALIBRAÇÃO")
    print(f"{'='*60}")
    print(f"  {'Target':12s} {'ACC':>8s} {'R²':>8s} {'LogACC':>8s} {'#Fats':>6s}")
    print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*6}")
    for target, acc, r2, lacc, nf in results_summary:
        if acc:
            print(f"  {target:12s} {acc:7.1f}% {r2:7.4f} {lacc:7.1f}% {nf:5d}")
        else:
            print(f"  {target:12s}  FAILED")
    
    conn.close()


if __name__ == "__main__":
    main()
