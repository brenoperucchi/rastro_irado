"""
IRAI — Calibração do modelo.

Estima pesos dos 7 fatores via regressão linear + logística,
calcula σ diárias, e grava em model_params.

Uso: python scripts/calibrate.py [--days 252] [--out-of-sample 60]
"""

import sqlite3
import os
import sys
import argparse
import json
import numpy as np
import pandas as pd
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.db import get_connection, DB_PATH

os.environ["PYTHONIOENCODING"] = "utf-8"

# ── Fatores ────────────────────────────────────────────────
FACTORS = ["WDO$N", "DI1$N", "VIX", "DXY", "BRENT", "US500", "BTCUSD"]
FACTOR_LABELS = {
    "WDO$N": "wdo", "DI1$N": "di", "VIX": "vix", "DXY": "dxy",
    "BRENT": "brent", "US500": "us500", "BTCUSD": "btcusd",
}
TARGET = "WIN$N"

# Sinais esperados (para sanity check)
EXPECTED_SIGNS = {
    "wdo": -1,     # WDO sobe = IBOV cai
    "di": -1,      # Juros sobe = IBOV cai
    "vix": -1,     # VIX sobe = IBOV cai
    "dxy": -1,     # Dólar global forte = IBOV cai
    "brent": +1,   # Petróleo sobe = IBOV sobe (Petrobras)
    "us500": +1,   # S&P sobe = risco global ↑ = IBOV sobe
    "btcusd": +1,  # BTC sobe = risk-on = IBOV sobe
}


def load_daily_returns(conn: sqlite3.Connection, days: int) -> pd.DataFrame:
    """Carrega closes D1 e calcula retornos diários."""
    all_symbols = [TARGET] + FACTORS

    # Pegar todas as barras D1
    placeholders = ",".join(["?"] * len(all_symbols))
    query = f"""
        SELECT symbol, timestamp_utc, close
        FROM market_bars
        WHERE timeframe = 'D1'
          AND symbol IN ({placeholders})
        ORDER BY timestamp_utc
    """
    df = pd.read_sql_query(query, conn, params=all_symbols)

    if df.empty:
        raise ValueError("Nenhuma barra D1 encontrada no banco!")

    # Pivot: linhas = datas, colunas = símbolos
    df["date"] = pd.to_datetime(df["timestamp_utc"]).dt.date
    pivot = df.pivot_table(index="date", columns="symbol", values="close", aggfunc="last")
    pivot = pivot.sort_index()

    # Retornos percentuais diários
    returns = pivot.pct_change().dropna()

    # Filtrar últimos N dias
    if len(returns) > days:
        returns = returns.tail(days)

    print(f"  Retornos D1: {len(returns)} dias | {returns.index[0]} -> {returns.index[-1]}")
    print(f"  Simbolos com dados: {list(returns.columns)}")

    # Verificar cobertura
    missing = [s for s in all_symbols if s not in returns.columns]
    if missing:
        print(f"  AVISO: Simbolos sem dados D1: {missing}")

    return returns


def load_intraday_sessions(conn: sqlite3.Connection, days: int) -> pd.DataFrame:
    """Carrega barras M5 e computa retorno open-to-close por sessão para calibrar α."""
    # Pegar barras M5 do WIN$N
    query = """
        SELECT timestamp_utc, open, close
        FROM market_bars
        WHERE timeframe = 'M5' AND symbol = ?
        ORDER BY timestamp_utc
    """
    df = pd.read_sql_query(query, conn, params=[TARGET])

    if df.empty:
        return pd.DataFrame()

    df["timestamp"] = pd.to_datetime(df["timestamp_utc"])
    # Sessão B3: barras entre 13:00 UTC (10:00 BRT) e 20:55 UTC (17:55 BRT)
    df["hour_utc"] = df["timestamp"].dt.hour
    df["date"] = df["timestamp"].dt.date

    # Filtrar barras de pregão (13:00 - 20:55 UTC = 10:00 - 17:55 BRT)
    session = df[(df["hour_utc"] >= 13) & (df["hour_utc"] < 21)].copy()

    if session.empty:
        # Tentar com offset BRT direto (se timestamps já estão em BRT)
        session = df[(df["hour_utc"] >= 10) & (df["hour_utc"] < 18)].copy()

    if session.empty:
        print("  AVISO: Nao encontrou barras de pregao. Tentando sem filtro de horario...")
        session = df.copy()

    # Agrupar por dia: open da primeira barra, close da última
    daily = session.groupby("date").agg(
        session_open=("open", "first"),
        session_close=("close", "last"),
        n_bars=("close", "count"),
    )
    daily["return"] = (daily["session_close"] - daily["session_open"]) / daily["session_open"]
    daily["up"] = (daily["return"] > 0).astype(int)

    # Filtrar dias com poucas barras (provavelmente feriados)
    daily = daily[daily["n_bars"] >= 20]

    if len(daily) > days:
        daily = daily.tail(days)

    print(f"  Sessoes intraday: {len(daily)} dias | {daily.index[0]} -> {daily.index[-1]}")
    print(f"  Taxa de alta: {daily['up'].mean():.1%} | Barras/dia media: {daily['n_bars'].mean():.0f}")

    return daily


def calibrate_weights(returns: pd.DataFrame) -> dict:
    """Estima pesos via OLS: ret_WIN = Σ w_i * ret_factor_i."""
    from sklearn.linear_model import LinearRegression

    # Preparar X e y
    available_factors = [f for f in FACTORS if f in returns.columns]
    if TARGET not in returns.columns:
        raise ValueError(f"Alvo {TARGET} nao encontrado nos retornos D1!")

    X = returns[available_factors].values
    y = returns[TARGET].values

    # Remover linhas com NaN
    mask = ~(np.isnan(X).any(axis=1) | np.isnan(y))
    X = X[mask]
    y = y[mask]

    print(f"\n  Regressao OLS: {len(y)} amostras, {len(available_factors)} fatores")

    # Normalizar para pesos comparáveis
    X_std = X.std(axis=0, keepdims=True)
    X_std[X_std == 0] = 1
    y_std = y.std()
    if y_std == 0:
        raise ValueError("Retornos do alvo tem variancia zero!")

    X_norm = X / X_std
    y_norm = y / y_std

    model = LinearRegression(fit_intercept=True)
    model.fit(X_norm, y_norm)

    r_squared = model.score(X_norm, y_norm)
    print(f"  R² = {r_squared:.4f}")

    weights = {}
    print(f"\n  {'Fator':<12} {'Peso':<10} {'Sinal Esperado':<16} {'Check'}")
    print(f"  {'-'*48}")

    for i, factor in enumerate(available_factors):
        label = FACTOR_LABELS[factor]
        w = model.coef_[i]
        expected = EXPECTED_SIGNS.get(label, 0)
        sign_ok = (w * expected > 0) if expected != 0 else True
        check = "OK" if sign_ok else "INVERTIDO!"
        weights[f"w_{label}"] = float(w)
        print(f"  {label:<12} {w:>+8.4f}  {'+' if expected > 0 else '-':<16} {check}")

    return weights, r_squared, available_factors


def calibrate_volatilities(returns: pd.DataFrame) -> dict:
    """Calcula σ diária de cada fator."""
    sigmas = {}
    print(f"\n  Volatilidades diarias:")
    print(f"  {'Fator':<12} {'sigma':<10} {'sigma_anual':<12}")
    print(f"  {'-'*36}")

    for factor in FACTORS:
        if factor not in returns.columns:
            continue
        label = FACTOR_LABELS[factor]
        sigma = returns[factor].std()
        sigma_annual = sigma * np.sqrt(252)
        sigmas[f"sigma_{label}_daily"] = float(sigma)
        print(f"  {label:<12} {sigma:>8.5f}  {sigma_annual:>10.2%}")

    return sigmas


def calibrate_alpha(returns: pd.DataFrame, weights: dict, sessions: pd.DataFrame) -> float:
    """Calibra α via regressão logística: P(up) = sigmoid(α * S)."""
    from sklearn.linear_model import LogisticRegression

    # Usar retornos D1 para montar score diário
    available_factors = [f for f in FACTORS if f in returns.columns]
    dates_common = sorted(set(returns.index) & set(sessions.index))

    if len(dates_common) < 30:
        print(f"\n  AVISO: Apenas {len(dates_common)} dias em comum para calibrar alpha. Usando default.")
        return 1.2

    scores = []
    labels = []

    for date in dates_common:
        if date not in returns.index or date not in sessions.index:
            continue

        score = 0.0
        for factor in available_factors:
            label = FACTOR_LABELS[factor]
            w_key = f"w_{label}"
            if w_key in weights and factor in returns.columns:
                ret = returns.loc[date, factor]
                if not np.isnan(ret):
                    score += weights[w_key] * ret

        scores.append(score)
        labels.append(sessions.loc[date, "up"])

    scores = np.array(scores).reshape(-1, 1)
    labels = np.array(labels)

    # Remover NaN
    mask = ~np.isnan(scores.ravel())
    scores = scores[mask]
    labels = labels[mask]

    if len(scores) < 30:
        print(f"\n  AVISO: Apenas {len(scores)} amostras validas para alpha. Usando default.")
        return 1.2

    print(f"\n  Regressao logistica para alpha: {len(scores)} amostras")

    lr = LogisticRegression(fit_intercept=True, solver="lbfgs", max_iter=1000)
    lr.fit(scores, labels)

    alpha = float(lr.coef_[0][0])
    accuracy = lr.score(scores, labels)

    print(f"  alpha = {alpha:.4f}")
    print(f"  intercept = {lr.intercept_[0]:.4f}")
    print(f"  Acuracia direcional = {accuracy:.1%}")

    # Acurácia por bucket de confiança
    from scipy.special import expit
    probs = expit(alpha * scores.ravel()) * 100
    print(f"\n  Reliability por bucket:")
    for lo, hi in [(0, 30), (30, 40), (40, 60), (60, 70), (70, 100)]:
        mask_bucket = (probs >= lo) & (probs < hi)
        if mask_bucket.sum() > 0:
            obs_rate = labels[mask_bucket].mean()
            print(f"    P_up [{lo:>3}-{hi:>3}%]: {mask_bucket.sum():>4} dias, "
                  f"taxa real de alta: {obs_rate:.1%}")

    return alpha


def save_params(conn: sqlite3.Connection, weights: dict, sigmas: dict, alpha: float,
                r_squared: float, effective_from: str):
    """Grava parâmetros em model_params."""
    all_params = {**weights, **sigmas, "alpha": alpha}

    cursor = conn.cursor()

    # Purge: remover params win_ antigos para evitar modelos híbridos
    deleted = cursor.execute(
        "DELETE FROM model_params WHERE param_name LIKE 'win_%'"
    ).rowcount
    if deleted:
        print(f"  [purge] {deleted} params antigos de 'win_' removidos")

    for name, value in all_params.items():
        cursor.execute(
            "INSERT INTO model_params (param_name, value, effective_from) VALUES (?, ?, ?)",
            (name, value, effective_from),
        )

    # Log da calibração
    cursor.execute(
        """INSERT OR REPLACE INTO calibration_log
           (calibration_date, window_days, r_squared, params_json, notes)
           VALUES (?, ?, ?, ?, ?)""",
        (effective_from, 0, r_squared, json.dumps(all_params, indent=2),
         f"Calibracao automatica {datetime.now().strftime('%Y-%m-%d %H:%M')}"),
    )

    conn.commit()
    print(f"\n  Parametros salvos em model_params (effective_from={effective_from})")


def generate_report(weights: dict, sigmas: dict, alpha: float, r_squared: float,
                    n_days: int, available_factors: list) -> str:
    """Gera relatório markdown da calibração."""
    report = f"""# IRAI — Relatório de Calibração

**Data:** {datetime.now().strftime('%Y-%m-%d %H:%M')}
**Janela:** {n_days} dias úteis
**R²:** {r_squared:.4f}
**Alpha (sigmoid):** {alpha:.4f}

## Pesos Estimados

| Fator | Peso | Sinal Esperado | Status |
|-------|------|----------------|--------|
"""
    for factor in available_factors:
        label = FACTOR_LABELS[factor]
        w = weights.get(f"w_{label}", 0)
        expected = EXPECTED_SIGNS.get(label, 0)
        sign_ok = (w * expected > 0) if expected != 0 else True
        status = "OK" if sign_ok else "INVERTIDO"
        exp_str = "+" if expected > 0 else "-" if expected < 0 else "?"
        report += f"| {label} | {w:+.4f} | {exp_str} | {status} |\n"

    report += f"\n## Volatilidades Diarias\n\n"
    report += "| Fator | sigma_diaria | sigma_anual |\n|-------|-------------|------------|\n"
    for factor in available_factors:
        label = FACTOR_LABELS[factor]
        s = sigmas.get(f"sigma_{label}_daily", 0)
        report += f"| {label} | {s:.5f} | {s * np.sqrt(252):.2%} |\n"

    report += f"""
## Interpretação

- **R² = {r_squared:.4f}**: {'Bom' if r_squared > 0.3 else 'Moderado' if r_squared > 0.15 else 'Fraco'} poder explicativo dos fatores sobre o retorno WIN.
- **Alpha = {alpha:.4f}**: Controla quão agressivamente o score vira probabilidade.

## Parâmetros para model_params

```json
{json.dumps({**weights, **sigmas, "alpha": alpha}, indent=2)}
```
"""
    return report


def main():
    parser = argparse.ArgumentParser(description="IRAI - Calibracao do modelo")
    parser.add_argument("--days", type=int, default=252, help="Dias uteis de janela (default: 252 = 1 ano)")
    parser.add_argument("--db", default=DB_PATH, help="Caminho do banco SQLite")
    args = parser.parse_args()

    print("=" * 70)
    print("  IRAI — Calibracao")
    print(f"  Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Janela: {args.days} dias | DB: {args.db}")
    print("=" * 70)

    conn = get_connection(args.db)

    # 1. Carregar retornos D1
    print("\n[1/5] Carregando retornos D1...")
    returns = load_daily_returns(conn, args.days)

    # 2. Carregar sessões intraday
    print("\n[2/5] Carregando sessoes intraday...")
    sessions = load_intraday_sessions(conn, args.days)

    # 3. Calibrar pesos
    print("\n[3/5] Calibrando pesos (OLS)...")
    weights, r_squared, available_factors = calibrate_weights(returns)

    # 4. Calibrar volatilidades
    print("\n[4/5] Calculando volatilidades...")
    sigmas = calibrate_volatilities(returns)

    # 5. Calibrar alpha
    print("\n[5/5] Calibrando alpha (logistica)...")
    alpha = calibrate_alpha(returns, weights, sessions)

    # Salvar
    effective = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_params(conn, weights, sigmas, alpha, r_squared, effective)

    # Gerar relatório
    report = generate_report(weights, sigmas, alpha, r_squared, len(returns), available_factors)
    report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "reports")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"calibration_{datetime.now().strftime('%Y%m%d')}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n  Relatorio salvo: {report_path}")

    conn.close()

    print(f"\n{'='*70}")
    print(f"  CALIBRACAO CONCLUIDA")
    print(f"  R² = {r_squared:.4f} | alpha = {alpha:.4f}")
    print(f"  Pesos: {', '.join(f'{k}={v:+.3f}' for k, v in weights.items())}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
