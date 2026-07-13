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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.irai.market_geometry import serving_daily_returns

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


def load_daily_returns(conn, session_start_h, session_end_h, target_symbol=None):
    """Carrega exatamente os retornos finais observáveis pelo engine.

    ``session_start_h``/``session_end_h`` ficam na assinatura por compatibilidade,
    mas não recortam barras: no serving eles dimensionam ``t_frac`` e não a
    abertura. A sessão consultada pelo engine é sempre o dia cru do banco.
    """
    if target_symbol is None:
        raise ValueError("target_symbol é obrigatório para reproduzir o cutoff do serving")
    symbols = sorted(set(ALL_FACTORS) | {target_symbol})
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(
        f"""SELECT symbol, source, timestamp_utc, open, close
            FROM market_bars
            WHERE timeframe='M5' AND symbol IN ({placeholders})
            ORDER BY timestamp_utc""",
        symbols,
    )
    returns = serving_daily_returns(rows, target_symbol)
    return {symbol: pd.Series(values, dtype=float) for symbol, values in returns.items()}


def calibrate_target(conn, target, session_start_h=0, session_end_h=24,
                     data_proxy=None, min_factors=4, max_factors=8, forced_factors=None,
                     holdout_sessions=50):
    """
    Brute-force: testa todas combinações de fatores para o target.
    Retorna: best_factors, best_labels, weights, sigmas, alpha, intercept, r2, accuracy
    """
    data_sym = data_proxy or ALIASES.get(target, target)
    
    print(f"\n{'='*60}")
    print(f"  Calibrando: {target} (dados: {data_sym})")
    print(f"  Geometria: serving 00-24 cru, alinhada por source; cutoff no close do target")
    print(f"{'='*60}")

    daily = load_daily_returns(conn, session_start_h, session_end_h, data_sym)

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

    if holdout_sessions <= 0:
        print("  [FAIL] --holdout-sessions deve ser positivo")
        return None
    if len(merged_all) - holdout_sessions < 100:
        print(f"  [FAIL] Treino insuficiente após holdout ({len(merged_all) - holdout_sessions} < 100)")
        return None

    train = merged_all.iloc[:-holdout_sessions]
    holdout = merged_all.iloc[-holdout_sessions:]
    y_train = train["target"].values
    y_train_dir = (y_train > 0).astype(int)
    y_all = merged_all["target"].values
    y_all_dir = (y_all > 0).astype(int)
    print(
        f"  Split temporal: treino={len(train)} até {train.index[-1]}, "
        f"holdout={len(holdout)} de {holdout.index[0]} a {holdout.index[-1]}"
    )

    # Brute force
    best_score = -float("inf")
    best_result = None
    total_combos = 0

    factor_labels_map = {f: f.replace("$N", "").lower() for f in available_factors}
    available_labels = [factor_labels_map[f] for f in available_factors]

    # Precompute TSS for R2
    tss = np.sum((y_train - y_train.mean()) ** 2)

    # Precompute index sets for multicollinearity groups
    treasury_indices = {i for i, f in enumerate(available_factors) 
                        if f in treasuries}
    em_bond_indices = {i for i, f in enumerate(available_factors)
                       if f in em_bonds}

    skipped_multicol = 0

    candidate_matrix = train[available_labels].values
    gram = candidate_matrix.T @ candidate_matrix
    sums = candidate_matrix.sum(axis=0)
    rhs_factors = candidate_matrix.T @ y_train
    rhs_intercept = y_train.sum()
    batch_size = 2_000

    def evaluate_batch(combo_batch, n_factors):
        nonlocal best_score, best_result
        combo_array = np.asarray(combo_batch, dtype=int)
        n_batch = len(combo_array)
        normal = np.empty((n_batch, n_factors + 1, n_factors + 1))
        normal[:, :n_factors, :n_factors] = gram[
            combo_array[:, :, None], combo_array[:, None, :]
        ]
        normal[:, :n_factors, n_factors] = sums[combo_array]
        normal[:, n_factors, :n_factors] = sums[combo_array]
        normal[:, n_factors, n_factors] = len(train)
        rhs = np.empty((n_batch, n_factors + 1))
        rhs[:, :n_factors] = rhs_factors[combo_array]
        rhs[:, n_factors] = rhs_intercept

        try:
            betas = np.linalg.solve(normal, rhs[..., None])[..., 0]
        except np.linalg.LinAlgError:
            betas = np.stack([
                np.linalg.lstsq(normal[i], rhs[i], rcond=None)[0]
                for i in range(n_batch)
            ])

        predictions = np.einsum(
            "sbn,bn->sb", candidate_matrix[:, combo_array], betas[:, :n_factors]
        ) + betas[:, n_factors]
        accuracies = np.mean(
            (predictions > 0) == (y_train[:, None] > 0), axis=0
        )
        r2s = 1 - np.sum((y_train[:, None] - predictions) ** 2, axis=0) / tss
        scores = accuracies * 0.7 + np.maximum(0, r2s) * 0.3
        winner = int(np.argmax(scores))
        if scores[winner] > best_score:
            combo = tuple(combo_array[winner])
            labels = [available_labels[i] for i in combo]
            # Refit direto para manter a mesma solução numérica histórica.
            Xb = np.column_stack([train[labels].values, np.ones(len(train))])
            beta = np.linalg.lstsq(Xb, y_train, rcond=None)[0]
            yp = Xb @ beta
            acc = np.mean((yp > 0) == (y_train > 0))
            r2 = 1 - np.sum((y_train - yp) ** 2) / tss
            best_score = acc * 0.7 + max(0, r2) * 0.3
            best_result = {
                "factors": [available_factors[i] for i in combo],
                "labels": labels,
                "beta": beta,
                "r2": r2,
                "acc": acc,
                "n_factors": n_factors,
            }

    for n_factors in range(min_factors, min(max_factors + 1, len(available_factors) + 1)):
        batch = []
        for combo in combinations(range(len(available_factors)), n_factors):
            combo_set = set(combo)
            if len(combo_set & treasury_indices) > 1 or len(combo_set & em_bond_indices) > 1:
                skipped_multicol += 1
                continue
            total_combos += 1
            batch.append(combo)
            if len(batch) == batch_size:
                evaluate_batch(batch, n_factors)
                batch = []
        if batch:
            evaluate_batch(batch, n_factors)

    if best_result is None:
        print(f"  Testadas: {total_combos:,} combos ({skipped_multicol:,} descartadas por multicolinearidade)")
        print(f"  [FAIL] Nenhum resultado valido")
        return None

    print(f"  Testadas: {total_combos:,} combos ({skipped_multicol:,} descartadas por multicolinearidade)")
    print(f"  >> Seleção no treino: {best_result['n_factors']} fatores, ACC={best_result['acc']:.1%}, R2={best_result['r2']:.4f}")
    print(f"  Fatores: {', '.join(best_result['factors'])}")

    # O holdout fica intocado durante a escolha da cesta. Métricas OOS usam os
    # coeficientes provisórios do treino; só depois refazemos o fit em todas as
    # sessões para produzir os parâmetros finais de serving.
    labels = best_result["labels"]
    beta_train = best_result["beta"]
    X_holdout_b = np.column_stack(
        [holdout[labels].values, np.ones(len(holdout))]
    )
    y_holdout = holdout["target"].values
    yp_holdout = X_holdout_b @ beta_train
    oos_acc = np.mean((yp_holdout > 0) == (y_holdout > 0))
    oos_tss = np.sum((y_holdout - y_holdout.mean()) ** 2)
    oos_r2 = 1 - np.sum((y_holdout - yp_holdout) ** 2) / oos_tss

    X_all_b = np.column_stack(
        [merged_all[labels].values, np.ones(len(merged_all))]
    )
    beta = np.linalg.lstsq(X_all_b, y_all, rcond=None)[0]
    yp_all = X_all_b @ beta
    in_sample_acc = np.mean((yp_all > 0) == (y_all > 0))
    all_tss = np.sum((y_all - y_all.mean()) ** 2)
    in_sample_r2 = 1 - np.sum((y_all - yp_all) ** 2) / all_tss
    print(
        f"  Avaliação: in-sample ACC={in_sample_acc:.1%} R2={in_sample_r2:.4f}; "
        f"OOS ACC={oos_acc:.1%} R2={oos_r2:.4f}"
    )

    # Calibrar sigmas e logistic finais em todas as sessões.
    
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
    lr.fit(scores.reshape(-1, 1), y_all_dir)
    alpha = float(lr.coef_[0, 0])
    intercept = float(lr.intercept_[0])

    p_up = expit(alpha * scores + intercept) * 100
    dir_acc = np.mean((p_up > 50).astype(int) == y_all_dir) * 100

    train_sigmas = train[labels].std()
    train_scores = np.zeros(len(train))
    holdout_scores = np.zeros(len(holdout))
    for i, label in enumerate(labels):
        train_scores += beta_train[i] * train[label].values / train_sigmas[label]
        holdout_scores += beta_train[i] * holdout[label].values / train_sigmas[label]
    lr_oos = LogisticRegression(fit_intercept=True, max_iter=1000, C=1e6)
    lr_oos.fit(train_scores.reshape(-1, 1), y_train_dir)
    oos_p_up = lr_oos.predict_proba(holdout_scores.reshape(-1, 1))[:, 1]
    oos_logistic_acc = np.mean((oos_p_up > 0.5) == (y_holdout > 0)) * 100

    print(
        f"  Logistic final: a={alpha:.4f}, intercept={intercept:.4f}, "
        f"ACC in-sample={dir_acc:.1f}%, ACC OOS={oos_logistic_acc:.1f}%"
    )

    return {
        "factors": best_result["factors"],
        "labels": labels,
        "factor_labels": {f: l for f, l in zip(best_result["factors"], labels)},
        "weights": weights,
        "sigmas": sigmas,
        "alpha": alpha,
        "intercept": intercept,
        "r2": in_sample_r2,
        "accuracy": in_sample_acc * 100,
        "oos_accuracy": oos_acc * 100,
        "oos_r2": oos_r2,
        "logistic_acc": dir_acc,
        "oos_logistic_acc": oos_logistic_acc,
        "n_sessions": len(merged_all),
        "holdout_sessions": len(holdout),
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
    parser.add_argument("--db", type=str, default=DB_PATH,
                        help="Caminho do SQLite (default: data/irai.db)")
    parser.add_argument("--holdout-sessions", type=int, default=50,
                        help="Últimas N sessões reservadas, sem embaralhar (default: 50)")
    args = parser.parse_args()

    forced = [f.strip() for f in args.factors.split(",")] if args.factors else None

    if args.dry_run:
        db_uri = f"file:{os.path.abspath(args.db)}?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True)
    else:
        conn = sqlite3.connect(args.db)
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
            holdout_sessions=args.holdout_sessions,
        )
        if result:
            if args.dry_run:
                print(
                    f"  [DRY-RUN] cesta={result['factors']} "
                    f"acc_in={result['accuracy']:.1f}% r2_in={result['r2']:.4f} "
                    f"acc_oos={result['oos_accuracy']:.1f}% r2_oos={result['oos_r2']:.4f} "
                    "— NÃO gravado"
                )
            else:
                save_to_db(conn, target, slug, result)
            results_summary.append((
                target, result["accuracy"], result["oos_accuracy"], result["r2"],
                result["oos_r2"], result["logistic_acc"], len(result["factors"])
            ))
        else:
            results_summary.append((target, None, None, None, None, None, 0))

    # Summary
    print(f"\n{'='*60}")
    print(f"  RESUMO CALIBRAÇÃO")
    print(f"{'='*60}")
    print(f"  {'Target':12s} {'ACC in':>8s} {'ACC OOS':>8s} {'R2 in':>8s} {'R2 OOS':>8s} {'LogACC':>8s} {'#Fats':>6s}")
    print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*6}")
    for target, acc, oos_acc, r2, oos_r2, lacc, nf in results_summary:
        if acc:
            print(
                f"  {target:12s} {acc:7.1f}% {oos_acc:7.1f}% {r2:7.4f} "
                f"{oos_r2:7.4f} {lacc:7.1f}% {nf:5d}"
            )
        else:
            print(f"  {target:12s}  FAILED")
    
    conn.close()


if __name__ == "__main__":
    main()
