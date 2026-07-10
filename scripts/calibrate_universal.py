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
    "WIN$N", "WDO$N", "DI1$N",
    "DXY", "BRENT", "CHINA50", "USDMXN", "VIX", "BTCUSD",
    "US500", "US30", "USTEC", "DE40", "XAUUSD",
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF",
    "CADCHF", "AUDNZD", "EURGBP", "EURCHF", "EURJPY", "GBPJPY", "EURAUD",
    # --- iShares Axi (fatores candidatos, não estão no painel) ---
    "iSharesBrazil+",       # EWZ - proxy bolsa BR
    "iSharesTreasury20+",   # TLT - Treasury longo (20+y)
    "iSharesTreasury10-20+",# TLH - Treasury médio (10-20y)
    "iSharesTreasury1-3+",  # SHY - Treasury curto (1-3y)
    "iSharesUSEmerging+",   # EMB - EM USD Bond
    "iSharesCurrencyBond+", # LEMB - EM Local Currency Bond
]

# Pares que compõem o DXY — não podem usar DXY como fator (multicolinearidade)
DXY_COMPONENTS = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"}


# Alias: target lógico → símbolo nos dados
# WDO$N e DOL$N rastreiam o mesmo preço — WDO é o contrato mini oficial
ALIASES = {}  # sem aliases: usar WDO$N diretamente


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
                     data_proxy=None, min_factors=4, max_factors=8, forced_factors=None):
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
        print(f"  [FAIL] Sem dados para {data_sym}")
        return None

    target_ret = daily[data_sym].rename("target")

    # Fatores candidatos
    exclude = {target, data_sym}
    
    # Regras de negócio
    br_assets = {"WIN$N", "WDO$N", "DI1$N"}
    us_indices = {"US500", "US30", "USTEC"}
    
    if target in us_indices:
        # US indices não seguem outros US indices
        exclude.update(us_indices)

    if target not in br_assets:
        # Internacional não usa BR
        exclude.update(br_assets)

    if target in DXY_COMPONENTS:
        # Majors não usam DXY — são componentes do índice (multicolinearidade)
        exclude.add("DXY")
        print(f"  [regra] {target} é componente do DXY — DXY excluído dos fatores")

    # ── Exclusão de tautologias aritméticas entre crosses ─────────────────
    cross_trios = [
        {"CADCHF", "USDCAD", "USDCHF"},
        {"EURGBP", "EURUSD", "GBPUSD"},
        {"EURCHF", "EURUSD", "USDCHF"},
        {"EURJPY", "EURUSD", "USDJPY"},
        {"GBPJPY", "GBPUSD", "USDJPY"},
        {"EURAUD", "EURUSD", "AUDUSD"},
        # {"EURJPY", "GBPJPY", "EURGBP"}, # Removido para controle manual
    ]
    for trio in cross_trios:
        if target in trio:
            exclude.update(trio - {target})
            print(f"  [regra] {target} pertence a um trio de cross — excluindo {trio - {target}}")

    # AUDNZD = AUDUSD / NZDUSD  →  excluir AUDUSD para AUDNZD e vice-versa
    # (NZDUSD não está no modelo, então AUDNZD ≈ f(AUDUSD) apenas parcialmente)
    # Mas AUDNZD não pode usar AUDUSD como único fator dominante
    if target == "AUDNZD":
        exclude.add("AUDUSD")
        print(f"  [regra] AUDNZD exclui AUDUSD (correlação estrutural alta)")
    if target == "AUDUSD":
        exclude.add("AUDNZD")
        print(f"  [regra] AUDUSD exclui AUDNZD (correlação estrutural alta)")

    if target == "EURJPY":
        exclude.add("GBPJPY")
        print(f"  [regra] EURJPY exclui GBPJPY (pedido manual)")
    if target == "GBPJPY":
        exclude.add("EURJPY")
        print(f"  [regra] GBPJPY exclui EURJPY (pedido manual)")

    # ── Exclusão de iShares redundantes entre si ───────────────────────────────────
    # Treasuries são altamente correlacionados entre si — no máximo 1 na cesta
    treasuries = {"iSharesTreasury20+", "iSharesTreasury10-20+", "iSharesTreasury1-3+"}
    # EM bonds também correlacionam forte entre si
    em_bonds = {"iSharesUSEmerging+", "iSharesCurrencyBond+"}
    # iSharesBrazil (EWZ) tem correlação estrutural com WIN$N/WDO$N
    if target in {"WIN$N", "WDO$N"}:
        # Para BR assets, EWZ é quase tautologia — excluir
        exclude.add("iSharesBrazil+")
        print(f"  [regra] {target} exclui iSharesBrazil+ (proxy da mesma bolsa)")

    available_factors = [f for f in ALL_FACTORS if f in daily and f not in exclude]

    # --factors: força a cesta exata (bypassa brute-force e regras de exclusão,
    # avalia só esse combo). Usado p/ convergir com a produção em vez de
    # re-otimizar na nossa janela. Exige que todos os fatores tenham dados.
    if forced_factors:
        missing = [f for f in forced_factors if f not in daily]
        if missing:
            print(f"  [FAIL] Fatores forçados sem dados no DB: {missing}")
            return None
        available_factors = list(forced_factors)
        min_factors = max_factors = len(available_factors)
        print(f"  [FORÇADO] cesta fixa ({len(available_factors)}): {available_factors}")

    print(f"  Fatores disponíveis: {len(available_factors)}")
    print(f"  Sessões target: {len(target_ret)}")


    if len(available_factors) < min_factors:
        print(f"  [FAIL] Poucos fatores ({len(available_factors)} < {min_factors})")
        return None

    # Últimos 252 dias úteis
    merged_all = pd.DataFrame({"target": target_ret})
    for f in available_factors:
        label = f.replace("$N", "").lower()
        merged_all[label] = daily[f]
    merged_all = merged_all.dropna().iloc[-252:]
    
    print(f"  Sessões merged: {len(merged_all)}")

    if len(merged_all) < 100:
        print(f"  [FAIL] Poucos dados ({len(merged_all)} < 100)")
        return None

    y_ret = merged_all["target"].values
    y_dir = (y_ret > 0).astype(int)

    # Brute force
    best_score = -float("inf")
    best_result = None
    total_combos = 0

    factor_labels_map = {f: f.replace("$N", "").lower() for f in available_factors}
    available_labels = [factor_labels_map[f] for f in available_factors]

    # Precompute TSS for R2
    tss = np.sum((y_ret - y_ret.mean()) ** 2)

    # Precompute index sets for multicollinearity groups
    treasury_indices = {i for i, f in enumerate(available_factors) 
                        if f in treasuries}
    em_bond_indices = {i for i, f in enumerate(available_factors)
                       if f in em_bonds}

    skipped_multicol = 0

    for n_factors in range(min_factors, min(max_factors + 1, len(available_factors) + 1)):
        for combo in combinations(range(len(available_factors)), n_factors):
            # Filtro: no máximo 1 Treasury e 1 EM Bond por cesta
            combo_set = set(combo)
            n_treas = len(combo_set & treasury_indices)
            n_em = len(combo_set & em_bond_indices)
            if n_treas > 1 or n_em > 1:
                skipped_multicol += 1
                continue

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
            
            # Score Misto: 70% Direcional, 30% Correlação Estrutural (R²)
            r2 = 1 - np.sum((y_ret - yp) ** 2) / tss
            score = (acc * 0.7) + (max(0, r2) * 0.3)

            if score > best_score:
                best_score = score
                best_result = {
                    "factors": factors,
                    "labels": labels,
                    "beta": beta,
                    "r2": r2,
                    "acc": acc,
                    "n_factors": n_factors,
                }

    if best_result is None:
        print(f"  Testadas: {total_combos:,} combos ({skipped_multicol:,} descartadas por multicolinearidade)")
        print(f"  [FAIL] Nenhum resultado valido")
        return None

    print(f"  Testadas: {total_combos:,} combos ({skipped_multicol:,} descartadas por multicolinearidade)")
    print(f"  >> Melhor: {best_result['n_factors']} fatores, ACC={best_result['acc']:.1%}, R2={best_result['r2']:.4f}")
    print(f"  Fatores: {', '.join(best_result['factors'])}")

    # Calibrar sigmas e logistic
    labels = best_result["labels"]
    beta = best_result["beta"]
    
    weights = {}
    sigmas = {}
    for i, label in enumerate(labels):
        weights[label] = beta[i]
        sigmas[label] = float(merged_all[label].std())
        print(f"    w_{label:8s} = {beta[i]:+.6f}  s={sigmas[label]:.5f}")

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

    print(f"  Logistic: a={alpha:.4f}, intercept={intercept:.4f}, ACC={dir_acc:.1f}%")

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

    # Limpar TODOS os params antigos deste slug antes de inserir os novos.
    # Isso evita params de calibrações anteriores (com fatores diferentes)
    # ficarem no banco e causando modelos híbridos incorretos.
    deleted = conn.execute(
        "DELETE FROM model_params WHERE param_name LIKE ?", (f"{prefix}%",)
    ).rowcount
    if deleted:
        print(f"  [purge] {deleted} params antigos de '{prefix}' removidos")

    params = []
    for label, w in result["weights"].items():
        params.append((f"{prefix}w_{label}", w, effective))
    for label, s in result["sigmas"].items():
        params.append((f"{prefix}sigma_{label}", s, effective))
    params.append((f"{prefix}alpha", result["alpha"], effective))
    params.append((f"{prefix}intercept", result["intercept"], effective))

    conn.executemany(
        "INSERT INTO model_params (param_name, value, effective_from) VALUES (?, ?, ?)",
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
    print(f"  [OK] Salvos {len(params)} params (prefix='{prefix}') + asset_models atualizado")


def main():
    parser = argparse.ArgumentParser(description="Calibração Universal IRAI")
    parser.add_argument("--target", type=str, help="Símbolo alvo (ex: US500)")
    parser.add_argument("--all", action="store_true", help="Calibrar todos os targets")
    parser.add_argument("--force", action="store_true", help="Recalibrar mesmo já calibrados")
    parser.add_argument("--min-factors", type=int, default=6)
    parser.add_argument("--max-factors", type=int, default=8)
    parser.add_argument("--factors", type=str, default=None,
                        help="Força cesta exata (CSV de símbolos, ex: WDO$N,DI1$N,BRENT). Pula o brute-force.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Não grava no DB (só imprime cesta/métricas) — diagnóstico.")
    args = parser.parse_args()

    forced = [f.strip() for f in args.factors.split(",")] if args.factors else None

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

    print(f"\nCalibrando {len(targets)} targets...")

    results_summary = []
    for target, slug, s_start, s_end, proxy in targets:
        result = calibrate_target(
            conn, target, s_start, s_end, proxy,
            args.min_factors, args.max_factors,
            forced_factors=forced,
        )
        if result:
            if args.dry_run:
                print(f"  [DRY-RUN] cesta={result['factors']} acc={result['accuracy']:.1f}% r2={result['r2']:.4f} — NÃO gravado")
            else:
                save_to_db(conn, target, slug, result)
            results_summary.append((target, result["accuracy"], result["r2"], result["logistic_acc"], len(result["factors"])))
        else:
            results_summary.append((target, None, None, None, 0))

    # Summary
    print(f"\n{'='*60}")
    print(f"  RESUMO CALIBRAÇÃO")
    print(f"{'='*60}")
    print(f"  {'Target':12s} {'ACC':>8s} {'R2':>8s} {'LogACC':>8s} {'#Fats':>6s}")
    print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*6}")
    for target, acc, r2, lacc, nf in results_summary:
        if acc:
            print(f"  {target:12s} {acc:7.1f}% {r2:7.4f} {lacc:7.1f}% {nf:5d}")
        else:
            print(f"  {target:12s}  FAILED")
    
    conn.close()


if __name__ == "__main__":
    main()
