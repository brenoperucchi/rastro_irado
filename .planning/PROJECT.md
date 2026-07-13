# IRAI — Intraday Risk Appetite Index

## What This Is

Dashboard cross-asset em tempo real que estima a probabilidade direcional (alta/baixa) para **20 ativos globais** (Índices, Moedas, Commodities e Crypto), inferida a partir do comportamento estrutural do mercado usando Regressão Ridge. Ferramenta pessoal de suporte à decisão para trading algorítmico e discricionário.

## Core Value

Responder a cada 5 minutos, de forma visual e quantitativa, à pergunta: *"Neste momento do pregão, o resto do mundo está dizendo que este ativo deveria estar subindo ou caindo?"* — sintetizando até 8 fatores de forte correlação estrutural em um único número (P_up ∈ 0–100%) para cada alvo operado.

## Requirements

### Validated

- [x] Otimização Motor Z-Score Dinâmico (Kalman Filter + Johansen Cointegration) validado via Backtest de Spread PnL Real M5 (90 dias).

### Active

- [x] Calibrar pesos dos fatores cross-asset sobre 1 ano de dados históricos (Motor V2 Multi-Asset)
- [x] Coletar barras M5 de 3 terminais MT5 (XP, Tickmill, Axi) em tempo real
- [x] Calcular P_up(t) a cada barra de 5 min durante a sessão
- [x] Dashboard React exibindo P_up, Z-Score de Divergência de Preço e NWE
- [x] Validação visual e sinais de oportunidade (Verde/Vermelho)
- [x] Interatividade avançada: componente Brush para navegação Zoom/Pan no histórico diário e iconografia de mercado (2-letter codes)
- [x] Hierarquia Visual de Alertas (Global vs Local) com painel D-P-Z-E (Divergência, Pullback, Z-Score, Exaustão).
- [x] UI Simplificada Mobile-first: Remoção do Sparkline e textos neutros enxutos para reduzir carga cognitiva.
- [x] Tolerância a falha: se um terminal cai, sistema continua com stale flags
- [x] Calibração recalibrável via brute-force automático com Regressão Ridge (`calibrate_v2.py`)
- [x] Hospedagem Zero-Custo na nuvem via Firebase Hosting com sincronização periódica (Acesso Mobile)
- [x] Orquestração em background via Windows Services (NSSM)
- [x] Monitoramento 24h para ativos globais (00:00–24:00 UTC); B3 mantém 09:00–18:00
- [x] NWE (Nadaraya-Watson Envelope) com cor dinâmica por inclinação e bandas tracejadas (bw=8, mult=3)
- [x] Cobertura expandida: CADCHF, AUDNZD, EURGBP, EURCHF, EURJPY, GBPJPY e EURAUD adicionados ao modelo (20 alvos totais)
- [x] Cache server-side para respostas da API (invalidado por notify_update)
- [x] Convicção badge (forte/moderada/fraca) na overview
- [x] iShares Axi: 6 ETFs (EWZ, TLT, TLH, SHY, EMB, LEMB) como fatores de calibracao via 3o terminal. Filtros anti-multicolinearidade. 12/20 modelos melhorados.

### Out of Scope

- Execução automatizada de ordens — IRAI é suporte à decisão, não sistema de trading
- Multi-usuário / SaaS — ferramenta pessoal localhost
- Integração com supervisor SQX dos robôs — possível V2
- Backtest de estratégia baseada no IRAI — pode virar projeto separado
- App mobile — web-first

## Context

### Infraestrutura existente

- **MT5 BR (XP):** `C:\Program Files\MetaTrader 5 Terminal\terminal64.exe` — WIN$N, DOL$N, DI1$N, WDO$N
- **Tickmill:** `C:\Program Files\Tickmill MT5 Terminal\terminal64.exe` — VIX, DXY, BRENT, US500, US30, USTEC, XAUUSD, BTCUSD, EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, USDMXN, CHINA50
- **Axi:** `C:\Program Files\Axi MetaTrader 5 Terminal\terminal64.exe` — iSharesBrazil+, iSharesTreasury20+, iSharesTreasury10-20+, iSharesTreasury1-3+, iSharesUSEmerging+, iSharesCurrencyBond+ (fatores de calibracao, nao alvos)
- **IC Trading:** `C:\Program Files\IC Trading (MU) MT5 Terminal\terminal64.exe` — NAO conecta via Python MT5 lib (IPC timeout); descartado no MVP

### Alvos e Fatores do Modelo (V2)

O sistema analisa **20 alvos globais** simultâneos, cada um calibrado contra uma cesta dinâmica de fatores.

**Ativos Globais (Tickmill — sessão 24h):** US500, USTEC, US30, EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, XAUUSD, BTCUSD, AUDNZD, CADCHF, EURGBP, EURCHF, EURJPY, GBPJPY, EURAUD
**Ativos Locais (BR/XP — sessão 09h–18h):** WIN$N (Mini Índice), WDO$N (Mini Dólar)

**Calibracao V2 (Ridge Regularization + DE40 + iShares):**
O `calibrate_universal.py` padroniza os fatores e usa Ridge de fato na triagem e no fit final. O α é escolhido entre `0.01, 0.1, 1, 10, 100` no trecho final de 20% do treino e revalidado somente na cesta vencedora, sem consultar o holdout externo. A força bruta mantém 6–8 fatores, as regras estruturais e rejeita cestas com número de condição de `X` acima de 10. A última data do snapshot é descartada como potencialmente parcial, e ACC/R² in-sample e OOS são persistidos separadamente.
Ver `.planning/docs/FACTOR_MAP.md` para a tabela completa de pesos, R2 e Acuracia por ativo.

### Profundidade de dados disponível (M5)

| Símbolo | Barras M5 | Desde | Até | Dias |
|---------|-----------|-------|-----|------|
| WIN$N | 100.171 | 2022-09-16 | 2026-04-27 | 1319 |
| WDO$N | 100.171 | 2022-09-28 | 2026-04-27 | 1307 |
| DI1$N | 100.159 | 2022-07-19 | 2026-04-27 | 1378 |
| VIX | 100.199 | 2023-11-01 | 2026-04-28 | 908 |
| DXY | 100.315 | 2024-10-07 | 2026-04-28 | 567 |
| BRENT | 100.314 | 2024-10-04 | 2026-04-28 | 570 |
| US500 | 100.355 | 2024-11-21 | 2026-04-28 | 522 |
| US30 | 100.304 | 2024-11-21 | 2026-04-28 | 522 |
| USTEC | 100.304 | 2024-11-21 | 2026-04-28 | 522 |
| XAUUSD | 100.304 | 2024-11-22 | 2026-04-28 | 521 |
| BTCUSD | 100.921 | 2025-05-08 | 2026-04-28 | 354 |
| EURUSD | 100.327 | 2024-12-19 | 2026-04-28 | 494 |
| GBPUSD | 100.327 | 2024-12-19 | 2026-04-28 | 494 |
| USDJPY | 100.327 | 2024-12-19 | 2026-04-28 | 495 |
| AUDUSD | 100.327 | 2024-12-19 | 2026-04-28 | 494 |
| USDCAD | 100.327 | 2024-12-18 | 2026-04-28 | 495 |
| USDCHF | 100.327 | 2024-12-19 | 2026-04-28 | 495 |
| USDMXN | 100.319 | 2024-12-16 | 2026-04-28 | 498 |
| CHINA50 | 100.304 | 2024-11-26 | 2026-04-28 | 517 |
| AUDNZD | 80.057 | 2025-03-31 | 2026-04-28 | 392 |
| CADCHF | 80.057 | 2025-03-31 | 2026-04-28 | 392 |

> **Nota (Motor V2):** A profundidade média foi ampliada para ~100.000 barras na maioria dos ativos globais, garantindo histórico suficiente para extração de retornos de sessão.

**Janela útil M5 (interseção):** ~354 dias (limitada pelo BTCUSD).
**Janela útil D1:** 500+ barras (~2 anos) para todos.

### Stack escolhida

- Python 3.11+ (MetaTrader5 lib 5.0.5640)
- FastAPI + Uvicorn (API REST + cache server-side)
- SQLite WAL mode (zero infra)
- React + Vite + Recharts (dashboard com NWE overlay)
- APScheduler (cron de coleta a cada 60s)
- statsmodels / scikit-learn (calibração)
- NSSM (Windows Services para API, Collector, Firebase Sync)

### Experiência do operador

- Opera algoritmicamente via MT5 + SQX com 55+ robôs em produção
- Regime Supervisor já em produção com NSSM Windows Services
- Pair trading dashboard (WDO×WIN) com React + FastAPI em produção
- Familiaridade total com a stack

## Constraints

- **Plataforma:** Windows (MT5 nao roda nativo Linux)
- **Custo:** Zero — apenas contas demo/existentes dos brokers
- **MT5 Python lib:** Uma conexao por processo — collector com shutdown/reinit sequencial
- **IC Trading:** Terminal nao conecta via Python lib (IPC timeout) — descartado, EWZ substituido pelo iSharesBrazil+ da Axi
- **Horario:** Pregao B3 09:00–18:00 BRT; ativos globais rodam 24h; iShares Axi 16:00–23:00 UTC

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| 3 terminais → 2 terminais (XP + Tickmill) | IC Trading não conecta via Python MT5 lib (IPC timeout persistente) | Resolved |
| Transição para Multi-Asset (20 alvos) | O modelo provou ter alta eficácia para B3, então foi expandido globalmente com novos cross-pairs | Validated |
| Visualização de Divergência | Adicionado gráfico de barras (Z-Score) mostrando divergência de preço vs IRAI para gatilhos operacionais | Validated |
| Calibração V2 (Ridge + Filtro de 30%) | OLS puro gerava overfitting; o Ridge e os filtros limparam os modelos cortando fatores não-causais pela metade | Validated |
| Foco Intraday de Fechamento de Sessão | O treinamento é feito com as barras M5 exatas que compõem o horário operacional | Validated |
| Hospedagem Híbrida via Firebase | Solução engenhosa para expor a UI em dispositivos mobile (custo $0) sem precisar abrir porta do roteador. Um script (firebase_sync.py) escreve no Firebase RTDB a cada 30s. | Validated |
| Windows Services (NSSM) | Garantir que a API e o sync reiniciem junto com o PC sem janelas cmd poluindo o ambiente de trading. | Validated |
| Sessão 24h para ativos globais | Ativos Tickmill (Forex, Índices US, Crypto) operam fora do horário B3; restringir a 9-18h descartava dados valiosos | Validated |
| NWE no lugar de Cumulative Delta | Cumulative Delta era ruidoso e sem valor visual; NWE prove leitura direcional limpa com envelope de volatilidade | Validated |
| Polling HTTP ao inves de WebSocket | WebSocket causava flickering, race conditions de data, e overhead de recomputacao no broadcast; polling 60s com cache e mais estavel | Validated |
| Cache server-side por (target, date) | Evita recomputar compute_from_db() a cada request; invalidado apenas no notify_update do collector | Validated |
| iShares Axi como fatores macro | 6 ETFs (EWZ, TLT, TLH, SHY, EMB, LEMB) via 3o terminal (Axi). Filtros anti-multicolinearidade. 12/20 modelos melhorados. | Validated |
| 3 terminais MT5 sequenciais | Axi adicionado para iShares; collector faz shutdown/reinit sequencial (XP->Tickmill->Axi) | Validated |
| Calibração Universal (Mínimo 6 fatores + DE40) | R² fracos em ativos cruzados e sobreajuste (overfitting) em alguns alvos exigiram cestas maiores. DE40 supriu a macroeconomia europeia faltante. | Validated |
| Z-Score: PnL Real e Filtro in_trade | A V2 usa Spread `Y - X*beta` real, trava múltiplas reentradas e avalia métricas Forward 5B. Revelou assimetria de scalp no WIN$N e necessidade de Hold (z-exit) no WDO$N. | Validated |
| Arquitetura Híbrida (Ablation Johansen) | O filtro rígido de Cointegração destruía o PnL de ativos de Momentum (WIN$N, BTCUSD), mas salvava ativos Mean Reversion. Implementamos flag `use_johansen` por ativo no banco. | Validated |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

## Next Steps / Roadmap

Para propostas futuras, novos ativos, integrações avançadas (como o NWE Filter) e o projeto de acoplamento direto com o Regime Supervisor, consulte o backlog de evolução:
📄 **[ROADMAP.md](file:///c:/Users/ryzen/Downloads/Antigravity/rastro_irado/.planning/ROADMAP.md)**

---
*Last updated: 2026-05-02 after Z-Score V2 Optimization (Kalman Filter + Johansen Cointegration com avaliação de PnL Real).*
