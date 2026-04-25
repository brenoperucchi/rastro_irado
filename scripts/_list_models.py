import sqlite3, json

c = sqlite3.connect('data/irai.db')
c.row_factory = sqlite3.Row

rows = c.execute("""
    SELECT target, display_name, slug, factors, factor_labels, accuracy, r_squared, n_sessions
    FROM asset_models WHERE active=1 ORDER BY accuracy DESC
""").fetchall()

for r in rows:
    factors = json.loads(r["factors"]) if r["factors"] else []
    labels = json.loads(r["factor_labels"]) if r["factor_labels"] else {}
    acc = r["accuracy"] or 0
    r2 = r["r_squared"] or 0
    print(f"\n{'='*60}")
    print(f"  {r['display_name']} ({r['target']})")
    print(f"  ACC={acc:.1f}%  R²={r2:.4f}  Sessões={r['n_sessions'] or '?'}")
    print(f"  {len(factors)} fatores: {', '.join(factors)}")
    
    # Get weights
    prefix = f"{r['slug']}_"
    params = c.execute("""
        SELECT param_name, value FROM model_params
        WHERE param_name LIKE ? AND effective_from = (
            SELECT MAX(effective_from) FROM model_params WHERE param_name LIKE ?
        ) ORDER BY param_name
    """, (f"{prefix}%", f"{prefix}%")).fetchall()
    
    weights = {}
    sigmas = {}
    alpha = intercept = None
    for p in params:
        name = p["param_name"][len(prefix):]
        if name.startswith("w_"):
            weights[name[2:]] = p["value"]
        elif name.startswith("sigma_"):
            sigmas[name[6:]] = p["value"]
        elif name == "alpha":
            alpha = p["value"]
        elif name == "intercept":
            intercept = p["value"]
    
    if weights:
        print(f"  α={alpha:.4f}  intercept={intercept:.4f}")
        for k in sorted(weights.keys(), key=lambda x: abs(weights[x]), reverse=True):
            w = weights[k]
            s = sigmas.get(k, 0)
            direction = "↑ COMPRA" if w > 0 else "↓ VENDA"
            print(f"    {k:10s}  w={w:+.6f}  σ={s:.5f}  ({direction})")

c.close()
