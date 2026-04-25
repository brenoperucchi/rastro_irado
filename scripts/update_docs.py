import os

workspace = "c:/Users/ryzen/Downloads/Antigravity/rastro_irado"
artifact_map = "C:/Users/ryzen/.gemini/antigravity/brain/bd0c3dce-4b30-4f1b-8562-69d8e770e05f/factor_map.md"

# 1. Copy factor map to project root
with open(artifact_map, 'r', encoding='utf-8') as f:
    factor_map_content = f.read()

with open(os.path.join(workspace, 'FACTOR_MAP.md'), 'w', encoding='utf-8') as f:
    f.write(factor_map_content)

# 2. Update PRD.md
prd_path = os.path.join(workspace, 'PRD.md')
with open(prd_path, 'r', encoding='utf-8') as f:
    prd = f.read()

new_decisions = """| 2026-04-25 | Suporte a 13 Ativos | Ampliação do escopo: 3 índices US, BTC, Ouro, e pares FX Majors |
| 2026-04-25 | Isolamento Geográfico & Sessões | Ativos INTL bloqueados de usar fatores BR; sessões normalizadas (BR 09-18h, INTL 03-22h) |
| 2026-04-25 | Score Misto (70% ACC + 30% R²) | Força o modelo a manter coerência estrutural causal em vez de overfitting direcional |
| 2026-04-25 | UI: Divergência & Fluxo | Fim do 'Retorno' acumulado; painel agora mostra 'Preço Diverge' e 'Fluxo Confirma' |
"""
if "Suporte a 13 Ativos" not in prd:
    prd = prd.replace("249\n", "249\n" + new_decisions)
    
    # Also update the scope checklist in PRD
    prd = prd.replace("- [ ] Multi-target: WDO, small caps, BRL.", "- ✅ Multi-target: expansão para 13 ativos globais e BR.")
    prd = prd.replace("- [ ] Integração IRAI × Regime Supervisor", "- ✅ Painel Central Multi-Ativo com Divergência de Preço e Confirmação de Fluxo.\n- [ ] Integração IRAI × Regime Supervisor")

with open(prd_path, 'w', encoding='utf-8') as f:
    f.write(prd)

# 3. Update SPEC.md
spec_path = os.path.join(workspace, 'SPEC.md')
with open(spec_path, 'r', encoding='utf-8') as f:
    spec = f.read()

spec_addition = """
### 1.3 Princípios de Calibração (V2 - Multi-Asset)

1. **Isolamento Geográfico:** Ativos internacionais (ex: EURUSD, US500) jamais podem usar fatores do Brasil (WIN, WDO, DI1) para evitar ruído.
2. **Anti-Contaminação de Índices:** Índices americanos (US500, US30, USTEC) não podem usar uns aos outros como fatores, forçando o modelo a ancorar na macroeconomia base (VIX, DXY, Ouro).
3. **Score Misto (70% ACC / 30% R²):** A calibração (brute-force) prioriza modelos que tenham alta acurácia direcional (ACC) mas que mantenham forte aderência estrutural (R²). Isso garante que o WDO, por exemplo, não perca o DI1 de sua fórmula apenas por "azar" estatístico de uma pequena % direcional.
4. **Alinhamento de Sessões:** Sinais só são gerados dentro do horário líquido válido de cada ativo (BR: 09h às 18h | INTL: 03h às 22h).

"""

if "Princípios de Calibração (V2 - Multi-Asset)" not in spec:
    # Insert after 1.2 Princípios de design
    spec = spec.replace("## 2. Componentes", spec_addition + "\n## 2. Componentes")

with open(spec_path, 'w', encoding='utf-8') as f:
    f.write(spec)

# 4. Update README.md
readme_path = os.path.join(workspace, 'README.md')
if os.path.exists(readme_path):
    with open(readme_path, 'r', encoding='utf-8') as f:
        readme = f.read()
    
    readme_update = """
## Arquitetura Multi-Ativo (V2)
O sistema evoluiu para cobrir 13 ativos globais. Para entender a relação de fatores e pesos de cada modelo (WIN, WDO, S&P500, Forex, Cripto), consulte o [FACTOR_MAP.md](FACTOR_MAP.md).
"""
    if "Arquitetura Multi-Ativo (V2)" not in readme:
        readme = readme + "\n" + readme_update
        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(readme)

print("Docs updated successfully.")
