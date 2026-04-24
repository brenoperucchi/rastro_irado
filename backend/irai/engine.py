"""
IRAI Engine — Cálculo do P_up(t) em tempo real.

Carrega parâmetros calibrados do banco e computa z-scores,
contribuições e probabilidade a cada barra M5.
"""

import sqlite3
import numpy as np
from datetime import datetime, date, timezone, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.db import get_connection, DB_PATH


# ── Configuração ──────────────────────────────────────────
FACTORS = ["DOL$N", "DI1$N", "VIX", "DXY", "BRENT"]
FACTOR_LABELS = {"DOL$N": "dol", "DI1$N": "di", "VIX": "vix", "DXY": "dxy", "BRENT": "brent"}
TARGET = "WIN$N"

# Sessão B3: 10:00 - 17:55 BRT
SESSION_START_H = 10
SESSION_END_H = 18
BARS_PER_SESSION = 96  # 8h * 12 barras/h


@dataclass
class FactorState:
    symbol: str
    label: str
    open_price: float = 0.0
    current_price: float = 0.0
    ret: float = 0.0
    z_score: float = 0.0
    contribution: float = 0.0
    weight: float = 0.0
    sigma: float = 0.0
    last_update: Optional[str] = None
    stale: bool = False


@dataclass
class IRAISnapshot:
    timestamp: str
    session_date: str
    bar_idx: int
    t_frac: float
    p_up: float
    score: float
    verdict: str
    verdict_color: str
    factors: dict = field(default_factory=dict)
    win_return: float = 0.0
    win_open: float = 0.0
    win_current: float = 0.0
    stale_factors: list = field(default_factory=list)
    # Cumulative Delta
    bar_delta: float = 0.0
    cum_delta: float = 0.0
    cum_delta_norm: float = 0.0       # normalizado pelo volume médio
    flow_confirms: Optional[bool] = None  # True=confirma, False=diverge, None=neutro


def sigmoid(x: float) -> float:
    """Logistic sigmoid, numericamente estável."""
    if x >= 0:
        return 1.0 / (1.0 + np.exp(-x))
    else:
        ex = np.exp(x)
        return ex / (1.0 + ex)


class IRAIEngine:
    """Motor de cálculo do IRAI."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH
        self.weights: dict[str, float] = {}
        self.sigmas: dict[str, float] = {}
        self.alpha: float = 1.0
        self.intercept: float = 0.0
        self.session_opens: dict[str, float] = {}  # symbol -> open price
        self.factor_states: dict[str, FactorState] = {}
        self._load_params()

    def _load_params(self):
        """Carrega parâmetros mais recentes do banco."""
        conn = get_connection(self.db_path)
        cursor = conn.execute("""
            SELECT param_name, value
            FROM model_params
            WHERE effective_from = (SELECT MAX(effective_from) FROM model_params)
        """)
        for row in cursor:
            name = row["param_name"]
            value = row["value"]
            if name.startswith("w_"):
                self.weights[name] = value
            elif name.startswith("sigma_"):
                label = name.replace("sigma_", "").replace("_session", "").replace("_daily", "")
                self.sigmas[label] = value
            elif name == "alpha":
                self.alpha = value
            elif name == "intercept":
                self.intercept = value

        conn.close()

        # Inicializar factor states
        for symbol, label in FACTOR_LABELS.items():
            self.factor_states[label] = FactorState(
                symbol=symbol,
                label=label,
                weight=self.weights.get(f"w_{label}", 0.0),
                sigma=self.sigmas.get(label, 0.01),
            )

    def set_session_opens(self, opens: dict[str, float]):
        """Define preços de abertura da sessão."""
        self.session_opens = opens
        for symbol, label in FACTOR_LABELS.items():
            if symbol in opens and opens[symbol] > 0:
                self.factor_states[label].open_price = opens[symbol]

    def update_price(self, symbol: str, price: float, timestamp: str = None):
        """Atualiza o preço corrente de um fator."""
        label = FACTOR_LABELS.get(symbol)
        if label is None:
            if symbol == TARGET:
                return  # WIN é tratado separadamente
            return

        state = self.factor_states[label]
        state.current_price = price
        state.last_update = timestamp or datetime.now().isoformat()
        state.stale = False

    def compute(self, bar_idx: int, win_current: float = 0, win_open: float = 0,
                session_date: str = None, stale_threshold_sec: int = 600) -> IRAISnapshot:
        """
        Computa P_up(t) para a barra corrente.

        Args:
            bar_idx: Índice da barra na sessão (0..95)
            win_current: Preço corrente do WIN
            win_open: Preço de abertura do WIN
            session_date: Data da sessão (YYYY-MM-DD)
            stale_threshold_sec: Segundos para considerar um fator stale
        """
        t_frac = max((bar_idx + 1) / BARS_PER_SESSION, 0.01)
        sqrt_t = np.sqrt(t_frac)
        now_str = datetime.now().isoformat()
        session_date = session_date or date.today().isoformat()

        # Computar z-scores e contribuições
        score = 0.0
        stale_factors = []

        for label, state in self.factor_states.items():
            # Retorno desde open
            if state.open_price > 0 and state.current_price > 0:
                state.ret = (state.current_price - state.open_price) / state.open_price
            else:
                state.ret = 0.0
                state.stale = True

            # Z-score normalizado por tempo
            if state.sigma > 0:
                state.z_score = state.ret / (state.sigma * sqrt_t)
            else:
                state.z_score = 0.0

            # Contribuição
            state.contribution = state.weight * state.z_score
            score += state.contribution

            # Check stale
            if state.stale:
                stale_factors.append(label)

        # P_up via sigmoid
        p_up = sigmoid(self.alpha * score + self.intercept) * 100.0

        # WIN return
        win_return = 0.0
        if win_open > 0 and win_current > 0:
            win_return = (win_current - win_open) / win_open * 100.0

        # Verdict
        if p_up > 65:
            verdict, verdict_color = "RISK-ON", "#6FB38A"
        elif p_up < 35:
            verdict, verdict_color = "RISK-OFF", "#C25C5C"
        elif p_up > 55:
            verdict, verdict_color = "levemente comprador", "#6FB38A"
        elif p_up < 45:
            verdict, verdict_color = "levemente vendedor", "#C25C5C"
        else:
            verdict, verdict_color = "indeciso", "#7A7F8A"

        return IRAISnapshot(
            timestamp=now_str,
            session_date=session_date,
            bar_idx=bar_idx,
            t_frac=t_frac,
            p_up=round(p_up, 2),
            score=round(score, 4),
            verdict=verdict,
            verdict_color=verdict_color,
            factors={
                label: {
                    "symbol": state.symbol,
                    "z_score": round(state.z_score, 4),
                    "contribution": round(state.contribution, 4),
                    "ret": round(state.ret * 100, 4),
                    "weight": round(state.weight, 4),
                    "current_price": round(state.current_price, 4),
                    "open_price": round(state.open_price, 4),
                    "stale": state.stale,
                }
                for label, state in self.factor_states.items()
            },
            win_return=round(win_return, 4),
            win_open=win_open,
            win_current=win_current,
            stale_factors=stale_factors,
        )

    def compute_from_db(self, session_date: str = None) -> list[IRAISnapshot]:
        """Computa série IRAI completa para uma sessão a partir do banco."""
        session_date = session_date or date.today().isoformat()

        conn = get_connection(self.db_path)

        # Pegar barras da sessão
        all_symbols = [TARGET] + FACTORS
        placeholders = ",".join(["?"] * len(all_symbols))
        query = f"""
            SELECT symbol, timestamp_utc, open, high, low, close, real_volume, delta
            FROM market_bars
            WHERE timeframe = 'M5'
              AND symbol IN ({placeholders})
              AND timestamp_utc >= ? AND timestamp_utc < ?
            ORDER BY timestamp_utc
        """
        start = f"{session_date}T00:00:00Z"
        end_dt = datetime.fromisoformat(session_date) + timedelta(days=1)
        end = end_dt.strftime("%Y-%m-%dT00:00:00Z")

        import pandas as pd
        df = pd.read_sql_query(query, conn, params=all_symbols + [start, end])
        conn.close()

        if df.empty:
            return []

        df["timestamp"] = pd.to_datetime(df["timestamp_utc"])
        df["hour"] = df["timestamp"].dt.hour

        # Detectar se timestamps são BRT ou UTC
        win_hours = df[df["symbol"] == TARGET]["hour"].value_counts()
        if win_hours.index.min() < 13:
            # BRT timestamps
            session_mask = (df["hour"] >= 10) & (df["hour"] < 18)
        else:
            session_mask = (df["hour"] >= 13) & (df["hour"] < 21)

        df = df[session_mask]

        # Opens = primeira barra de cada símbolo
        opens = {}
        for sym in all_symbols:
            sym_bars = df[df["symbol"] == sym].sort_values("timestamp")
            if len(sym_bars) > 0:
                opens[sym] = float(sym_bars.iloc[0]["open"])

        if TARGET not in opens:
            return []

        self.set_session_opens(opens)

        # Iterar sobre barras do WIN
        win_bars = df[df["symbol"] == TARGET].sort_values("timestamp")
        n_bars = len(win_bars)
        snapshots = []
        cum_delta = 0.0
        cum_real_vol = 0.0

        for bar_idx, (_, win_row) in enumerate(win_bars.iterrows()):
            ts = win_row["timestamp"]

            # Atualizar preços dos fatores (mais recente <= ts)
            for factor in FACTORS:
                factor_bars = df[(df["symbol"] == factor) & (df["timestamp"] <= ts)]
                if len(factor_bars) > 0:
                    price = float(factor_bars.sort_values("timestamp").iloc[-1]["close"])
                    self.update_price(factor, price, ts.isoformat())

            snap = self.compute(
                bar_idx=bar_idx,
                win_current=float(win_row["close"]),
                win_open=opens[TARGET],
                session_date=session_date,
            )
            snap.timestamp = ts.isoformat()

            # Cumulative Delta
            bar_d = float(win_row["delta"]) if "delta" in win_row.index and win_row["delta"] else 0.0
            rv = float(win_row["real_volume"]) if "real_volume" in win_row.index and win_row["real_volume"] else 0.0
            cum_delta += bar_d
            cum_real_vol += rv

            snap.bar_delta = round(bar_d, 0)
            snap.cum_delta = round(cum_delta, 0)

            # Normalizar: cum_delta / volume médio por barra * n_barras
            avg_vol = cum_real_vol / (bar_idx + 1) if bar_idx >= 0 else 1
            if avg_vol > 0:
                snap.cum_delta_norm = round(cum_delta / avg_vol, 3)
            else:
                snap.cum_delta_norm = 0.0

            # Flow confirms: delta positivo + P_up > 55 = confirma compra
            #                delta negativo + P_up < 45 = confirma venda
            if snap.p_up > 55 and cum_delta > 0:
                snap.flow_confirms = True
            elif snap.p_up < 45 and cum_delta < 0:
                snap.flow_confirms = True
            elif snap.p_up > 55 and cum_delta < 0:
                snap.flow_confirms = False
            elif snap.p_up < 45 and cum_delta > 0:
                snap.flow_confirms = False
            else:
                snap.flow_confirms = None  # neutro / indeciso

            snapshots.append(snap)

        return snapshots


# ── Teste rápido ──────────────────────────────────────────
if __name__ == "__main__":
    engine = IRAIEngine()
    print(f"Params carregados:")
    print(f"  Weights: {engine.weights}")
    print(f"  Sigmas: {engine.sigmas}")
    print(f"  Alpha: {engine.alpha}")
    print(f"  Intercept: {engine.intercept}")

    # Testar com última sessão do banco
    import pandas as pd
    conn = get_connection()
    last_date = conn.execute("""
        SELECT DISTINCT substr(timestamp_utc, 1, 10) as d
        FROM market_bars
        WHERE symbol = 'WIN$N' AND timeframe = 'M5'
        ORDER BY d DESC LIMIT 2
    """).fetchall()
    conn.close()

    if last_date and len(last_date) > 1:
        test_date = last_date[1]["d"]  # penúltimo dia (mais completo)
        print(f"\nComputando sessao {test_date}...")
        snaps = engine.compute_from_db(test_date)
        if snaps:
            print(f"  Barras: {len(snaps)}")
            print(f"  P_up range: {min(s.p_up for s in snaps):.1f}% - {max(s.p_up for s in snaps):.1f}%")
            print(f"  Final: P_up={snaps[-1].p_up:.1f}% | score={snaps[-1].score:.4f} | {snaps[-1].verdict}")
            print(f"  WIN: {snaps[-1].win_open:.0f} -> {snaps[-1].win_current:.0f} ({snaps[-1].win_return:+.2f}%)")
            for label, f in snaps[-1].factors.items():
                print(f"    {label:<8} z={f['z_score']:>+7.3f}  contrib={f['contribution']:>+7.4f}  ret={f['ret']:>+6.3f}%")
