import re
import json

with open('temp_models.txt', 'r', encoding='utf-16') as f:
    text = f.read()

blocks = text.split('============================================================')[1:]
models = []
for block in blocks:
    if not block.strip(): continue
    lines = [l for l in block.split('\n') if l.strip()]
    if len(lines) < 3: continue
    
    header = lines[0].strip()
    match = re.match(r'(.*)\s+\((.*?)\)', header)
    if not match: continue
    display = match.group(1).strip()
    target = match.group(2).strip()
    
    stats_line = lines[1].strip()
    acc_m = re.search(r'ACC=([\d.]+)%', stats_line)
    r2_m = re.search(r'R²=([\d.]+)', stats_line)
    acc = acc_m.group(1) + '%' if acc_m else '0%'
    r2 = r2_m.group(1) if r2_m else '0.0'
    
    factors_line = lines[2].strip()
    n_factors_m = re.search(r'(\d+) fatores', factors_line)
    n_factors = n_factors_m.group(1) if n_factors_m else '0'
    
    alpha_line = lines[3].strip()
    alpha_m = re.search(r'α=([\d.-]+)', alpha_line)
    alpha = alpha_m.group(1) if alpha_m else '0'
    
    weights = []
    for line in lines[4:]:
        w_m = re.search(r'\s+(\w+)\s+w=([+\-.\d]+)\s+σ=([.\d]+)\s+\((.*?)\)', line)
        if w_m:
            weights.append(w_m.groups())
            
    models.append({
        'display': display,
        'target': target,
        'acc': acc,
        'r2': r2,
        'n_factors': n_factors,
        'alpha': alpha,
        'weights': weights,
        'acc_val': float(acc.replace('%', ''))
    })

models.sort(key=lambda x: x['acc_val'], reverse=True)

md = '''# IRAI Multi-Asset — Mapa de Fatores por Ativo

> [!NOTE]
> 13 modelos recalibrados. Regras aplicadas:
> 1. Ativos internacionais **não** utilizam ativos BR (WIN, DOL, DI1).
> 2. Índices americanos (US500, US30, USTEC) **não** utilizam outros índices americanos.
> 3. Horários das Sessões respeitados (BR: 09h às 18h | Internacional: 03h às 22h).
> 4. **Otimização (Score Misto):** Modelos classificados por 70% Acurácia + 30% R² para garantir robustez estrutural (ex: DI no Dólar).
> Última calibração: 2026-04-25

---

## Ranking por Acurácia (Pós-Isolamento e Score Misto)

| # | Ativo | ACC | R² | Fatores | Fator Principal |
|---|---|---|---|---|---|
'''

flags = {
    'EURUSD': '🇪🇺', 'GBPUSD': '🇬🇧', 'USDJPY': '🇯🇵', 'USDCHF': '🇨🇭',
    'AUDUSD': '🇦🇺', 'USTEC': '💻', 'USDCAD': '🇨🇦', 'US500': '🇺🇸',
    'US30': '🏛️', 'WDO$N': '💵', 'WIN$N': '🇧🇷', 'XAUUSD': '🥇', 'BTCUSD': '₿'
}

for i, m in enumerate(models):
    flag = flags.get(m['target'], '')
    main_f = f"{m['weights'][0][0].upper()} ({m['weights'][0][1]})" if m['weights'] else '-'
    md += f"| {i+1} | {flag} **{m['display']}** | **{m['acc']}** | **{m['r2']}** | {m['n_factors']} | {main_f} |\n"

md += '''
---

## Detalhamento Completo por Ativo

'''

for i, m in enumerate(models):
    flag = flags.get(m['target'], '')
    session = '09h - 18h' if m['target'] in ('WIN$N', 'WDO$N') else '03h - 22h'
    md += f"### {i+1}. {flag} {m['display']} ({m['target']}) — ACC {m['acc']} (Sessão: {session})\n"
    md += "```\n"
    md += f"α={m['alpha']}\n\n"
    md += "  Fator       Peso        σ         Direção\n"
    md += "  ──────────  ──────────  ────────  ─────────\n"
    for w in m['weights']:
        md += f"  {w[0].upper():<10}  {w[1]:<10}  {w[2]:<8}  {w[3]}\n"
    md += "```\n\n"

with open('C:/Users/ryzen/.gemini/antigravity/brain/bd0c3dce-4b30-4f1b-8562-69d8e770e05f/factor_map.md', 'w', encoding='utf-8') as f:
    f.write(md)

print("Factor map updated successfully.")
