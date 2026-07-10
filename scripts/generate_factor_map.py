import sqlite3
import json
import os

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
db_path = os.path.join(project_root, 'data', 'irai.db')
out_md_path = os.path.join(project_root, '.planning', 'docs', 'FACTOR_MAP.md')

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Get models
models = []
for row in conn.execute("SELECT * FROM asset_models WHERE active = 1"):
    slug = row["slug"]
    factors = json.loads(row["factors"])
    factor_labels = json.loads(row["factor_labels"])
    
    # Get params
    params = {}
    for p_row in conn.execute("SELECT param_name, value FROM model_params WHERE param_name LIKE ?", (f"{slug}_%",)):
        params[p_row["param_name"]] = p_row["value"]
        
    for p_row in conn.execute("SELECT param_name, value FROM model_params WHERE param_name LIKE 'sigma_%'"):
        params[p_row["param_name"]] = p_row["value"]
        
    weights = []
    for f in factors:
        label = factor_labels.get(f, f)
        w = params.get(f"{slug}_w_{label}", 0.0)
        s = params.get(f"{slug}_sigma_{label}", 0.0)
        direction = "↑ COMPRA" if w > 0 else "↓ VENDA"
        weights.append((f, w, s, direction))
        
    weights.sort(key=lambda x: abs(x[1]), reverse=True)
    
    models.append({
        'display': row["display_name"],
        'target': row["target"],
        'acc': f"{row['accuracy']:.1f}%" if row['accuracy'] else "0%",
        'r2': f"{row['r_squared']:.4f}" if row['r_squared'] else "0.0",
        'n_factors': len(factors),
        'alpha': f"{params.get(f'{slug}_alpha', 0.0):.4f}",
        'weights': weights,
        'acc_val': float(row['accuracy'] or 0)
    })

models.sort(key=lambda x: x['acc_val'], reverse=True)

md = '''# IRAI Multi-Asset — Mapa de Fatores por Ativo

> [!NOTE]
> 20 modelos ativos extraídos diretamente do banco de dados (irai.db).
> Regras aplicadas:
> 1. Ativos internacionais **não** utilizam ativos BR (WIN, DOL, DI1).
> 2. Índices americanos (US500, US30, USTEC) **não** utilizam outros índices americanos.
> 3. Horários das Sessões respeitados.
> 4. **Otimização (Score Misto):** Modelos classificados por 70% Acurácia + 30% R² para garantir robustez estrutural.

---

## Ranking por Acurácia (Pós-Isolamento e Score Misto)

| # | Ativo | ACC | R² | Fatores | Fator Principal |
|---|---|---|---|---|---|
'''

flags = {
    'EURUSD': '🇪🇺', 'GBPUSD': '🇬🇧', 'USDJPY': '🇯🇵', 'USDCHF': '🇨🇭',
    'AUDUSD': '🇦🇺', 'USTEC': '💻', 'USDCAD': '🇨🇦', 'US500': '🇺🇸',
    'US30': '🏛️', 'WDO$N': '💵', 'WIN$N': '🇧🇷', 'XAUUSD': '🥇', 'BTCUSD': '₿',
    'CADCHF': '🇨🇦🇨🇭', 'AUDNZD': '🇦🇺🇳🇿', 'EURGBP': '🇪🇺🇬🇧', 'EURCHF': '🇪🇺🇨🇭',
    'EURJPY': '🇪🇺🇯🇵', 'GBPJPY': '🇬🇧🇯🇵', 'EURAUD': '🇪🇺🇦🇺'
}

for i, m in enumerate(models):
    flag = flags.get(m['target'], '')
    main_f = f"{m['weights'][0][0]} ({m['weights'][0][1]:.4f})" if m['weights'] else '-'
    md += f"| {i+1} | {flag} **{m['display']}** | **{m['acc']}** | **{m['r2']}** | {m['n_factors']} | {main_f} |\n"

md += '''
---

## Detalhamento Completo por Ativo

'''

for i, m in enumerate(models):
    flag = flags.get(m['target'], '')
    session = '09h - 18h' if m['target'] in ('WIN$N', 'WDO$N') else '00h - 24h'
    md += f"### {i+1}. {flag} {m['display']} ({m['target']}) — ACC {m['acc']} (Sessão: {session})\n"
    md += "```\n"
    md += f"α={m['alpha']}\n\n"
    md += "  Fator       Peso        σ         Direção\n"
    md += "  ──────────  ──────────  ────────  ─────────\n"
    for w in m['weights']:
        md += f"  {w[0]:<10}  {w[1]:<10.6f}  {w[2]:<8.5f}  {w[3]}\n"
    md += "```\n\n"

with open(out_md_path, 'w', encoding='utf-8') as f:
    f.write(md)

print("Factor map updated successfully.")
