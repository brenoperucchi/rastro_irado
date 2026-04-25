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


# ── Alias de símbolos ─────────────────────────────────────
SYMBOL_ALIASES = {"WDO$N": "DOL$N"}

def resolve_symbol(sym: str) -> str:
    """Resolve alias para o símbolo real no banco."""
    return SYMBOL_ALIASES.get(sym, sym)

# Defaults para backward compat
TARGET = "WIN$N"
FACTOR_LABELS = {}  # carregado do DB
BARS_PER_SESSION = 108


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
    """Motor de cálculo do IRAI — suporta N targets via DB."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH
        # Modelo por slug: {slug: {weights, sigmas, alpha, intercept, factors, labels, session_h}}
        self.models: dict[str, dict] = {}
        # Target → slug mapping
        self.target_slugs: dict[str, str] = {}
        # All registered targets
        self.registered_targets: list[dict] = []

        self.session_opens: dict[str, float] = {}
        self.factor_states: dict[str, FactorState] = {}
        # Active model's alpha/intercept (set during compute)
        self.alpha: float = 1.0
        self.intercept: float = 0.0
        self._load_params()

    def _load_params(self):
        """Carrega configs de asset_models + params de model_params."""
        import json
        conn = get_connection(self.db_path)

        # 1) Carregar asset_models
        try:
            rows = conn.execute(
                "SELECT target, slug, display_name, icon, factors, factor_labels, "
                "session_start_h, session_end_h, data_proxy, accuracy, r_squared, active "
                "FROM asset_models WHERE active=1"
            ).fetchall()
        except Exception:
            rows = []

        for row in rows:
            target = row["target"]
            slug = row["slug"]
            factors = json.loads(row["factors"]) if row["factors"] else []
            factor_labels = json.loads(row["factor_labels"]) if row["factor_labels"] else {}
            
            self.target_slugs[target] = slug
            self.registered_targets.append({
                "target": target, "slug": slug,
                "display_name": row["display_name"], "icon": row["icon"],
                "factors": factors, "factor_labels": factor_labels,
                "session_start_h": row["session_start_h"] or 0,
                "session_end_h": row["session_end_h"] or 24,
                "data_proxy": row["data_proxy"],
                "accuracy": row["accuracy"], "r_squared": row["r_squared"],
            })

            # Determinar prefixo dos params
            prefix = f"{slug}_"

            # 2) Carregar model_params para este slug
            params_cursor = conn.execute("""
                SELECT param_name, value FROM model_params
                WHERE param_name LIKE ? AND effective_from = (
                    SELECT MAX(effective_from) FROM model_params WHERE param_name LIKE ?
                )
            """, (f"{prefix}%", f"{prefix}%"))

            weights, sigmas = {}, {}
            alpha, intercept = 1.0, 0.0

            for p in params_cursor:
                name = p["param_name"]
                value = p["value"]
                # Strip prefix
                clean = name[len(prefix):] if prefix and name.startswith(prefix) else name
                if clean.startswith("w_"):
                    weights[clean] = value
                elif clean.startswith("sigma_"):
                    label = clean.replace("sigma_", "").replace("_session", "").replace("_daily", "")
                    sigmas[label] = value
                elif clean == "alpha":
                    alpha = value
                elif clean == "intercept":
                    intercept = value

            self.models[slug] = {
                "weights": weights, "sigmas": sigmas,
                "alpha": alpha, "intercept": intercept,
                "factors": factors, "factor_labels": factor_labels,
                "prefix": prefix,
                "session_start_h": row["session_start_h"] or 0,
                "session_end_h": row["session_end_h"] or 24,
                "data_proxy": row["data_proxy"],
            }

        conn.close()

        # Backward compat: set default FACTOR_LABELS from WIN model
        global FACTOR_LABELS
        if "win" in self.models:
            FACTOR_LABELS = self.models["win"]["factor_labels"]

    def _get_model_config(self, target: str):
        """Retorna weights, sigmas, alpha, intercept, cfg para o target."""
        slug = self.target_slugs.get(target)
        if slug and slug in self.models:
            m = self.models[slug]
            return m["weights"], m["sigmas"], m["alpha"], m["intercept"], {
                "factors": m["factors"],
                "labels": m["factor_labels"],
                "param_prefix": m["prefix"],
                "session_start_h": m["session_start_h"],
                "session_end_h": m["session_end_h"],
                "data_proxy": m["data_proxy"],
            }
        # Fallback WIN
        if "win" in self.models:
            m = self.models["win"]
            return m["weights"], m["sigmas"], m["alpha"], m["intercept"], {
                "factors": m["factors"], "labels": m["factor_labels"],
                "param_prefix": "", "session_start_h": 12, "session_end_h": 21,
                "data_proxy": None,
            }
        return {}, {}, 1.0, 0.0, {"factors": [], "labels": {}, "param_prefix": "",
                                    "session_start_h": 0, "session_end_h": 24, "data_proxy": None}

    def set_session_opens(self, opens: dict[str, float]):
        """Define preços de abertura da sessão."""
        self.session_opens = opens
        for label, state in self.factor_states.items():
            # Resolver alias: se o símbolo lógico é WDO$N, procurar DOL$N nos opens
            db_sym = resolve_symbol(state.symbol)
            if db_sym in opens and opens[db_sym] > 0:
                state.open_price = opens[db_sym]
            elif state.symbol in opens and opens[state.symbol] > 0:
                state.open_price = opens[state.symbol]

    def update_price(self, symbol: str, price: float, timestamp: str = None):
        """Atualiza o preço corrente de um fator."""
        # Procurar nos factor_states pelo símbolo lógico
        for label, state in self.factor_states.items():
            if state.symbol == symbol:
                state.current_price = price
                state.last_update = timestamp or datetime.now().isoformat()
                state.stale = False
                return

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

    def compute_from_db(self, session_date: str = None, target: str = None) -> list[IRAISnapshot]:
        """Computa série IRAI completa para uma sessão a partir do banco.
        
        Args:
            session_date: Data YYYY-MM-DD
            target: Símbolo alvo (WIN$N, WDO$N, DOL$N). Default: WIN$N
        """
        session_date = session_date or date.today().isoformat()
        target = target or TARGET

        # Carregar modelo correto para o target
        t_weights, t_sigmas, t_alpha, t_intercept, cfg = self._get_model_config(target)
        active_factors = cfg["factors"]
        active_labels = cfg["labels"]

        # Setup factor states para este target
        self.factor_states = {}
        for symbol, label in active_labels.items():
            self.factor_states[label] = FactorState(
                symbol=symbol,
                label=label,
                weight=t_weights.get(f"w_{label}", 0.0),
                sigma=t_sigmas.get(label, 0.01),
            )
        # Temporariamente setar alpha/intercept para compute()
        saved_alpha, saved_intercept = self.alpha, self.intercept
        self.alpha = t_alpha
        self.intercept = t_intercept

        # Resolver aliases: símbolo lógico → símbolo no banco
        data_target = cfg.get("data_proxy") or resolve_symbol(target)
        # Mapear fatores lógicos → símbolos no banco
        factor_to_db = {f: resolve_symbol(f) for f in active_factors}
        db_factors = list(set(factor_to_db.values()))
        # Reverso: DB symbol → fator lógico (para update_price)
        db_to_factor = {v: k for k, v in factor_to_db.items()}

        conn = get_connection(self.db_path)

        # Pegar barras da sessão (usa símbolos do banco)
        all_symbols = list(set([data_target] + db_factors))
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
            self.alpha, self.intercept = saved_alpha, saved_intercept
            return []

        df["timestamp"] = pd.to_datetime(df["timestamp_utc"])
        df["hour"] = df["timestamp"].dt.hour

        # Detectar sessão via config do modelo
        session_start = cfg.get("session_start_h", 0)
        session_end = cfg.get("session_end_h", 24)

        target_hours = df[df["symbol"] == data_target]["hour"].value_counts()
        if target_hours.empty:
            self.alpha, self.intercept = saved_alpha, saved_intercept
            return []

        if session_end == 24 and session_start == 0:
            # 24h asset — sem filtro de sessão
            pass
        else:
            # Detectar se timestamps são BRT ou UTC
            if target_hours.index.min() < 13:
                # BRT timestamps: ajustar para horários locais
                brt_start = max(session_start - 3, 9)  # UTC→BRT approx
                brt_end = min(session_end - 3, 18)
                session_mask = (df["hour"] >= brt_start) & (df["hour"] < brt_end)
            else:
                session_mask = (df["hour"] >= session_start) & (df["hour"] < session_end)
            df = df[session_mask]

        # Opens = primeira barra de cada símbolo
        opens = {}
        for sym in all_symbols:
            sym_bars = df[df["symbol"] == sym].sort_values("timestamp")
            if len(sym_bars) > 0:
                opens[sym] = float(sym_bars.iloc[0]["open"])

        if data_target not in opens:
            self.alpha, self.intercept = saved_alpha, saved_intercept
            return []

        self.set_session_opens(opens)

        # Pré-indexar preços dos fatores por timestamp (O(n) em vez de O(n²))
        factor_prices = {}
        for factor in active_factors:
            db_sym = factor_to_db.get(factor, factor)
            fb = df[df["symbol"] == db_sym].sort_values("timestamp")
            if len(fb) > 0:
                # Lista de (timestamp, close) ordenada
                factor_prices[factor] = list(zip(fb["timestamp"], fb["close"].astype(float)))
            else:
                factor_prices[factor] = []

        # Iterar sobre barras do target
        target_bars = df[df["symbol"] == data_target].sort_values("timestamp")
        n_bars = len(target_bars)
        snapshots = []
        cum_delta = 0.0
        cum_real_vol = 0.0

        # Cursor de posição para cada fator (evita busca repetida)
        factor_cursors = {f: 0 for f in active_factors}

        for bar_idx, (_, row) in enumerate(target_bars.iterrows()):
            ts = row["timestamp"]

            # Atualizar preços dos fatores via cursor (O(1) amortizado)
            for factor in active_factors:
                prices = factor_prices[factor]
                cursor = factor_cursors[factor]
                # Avançar cursor até a última barra <= ts
                while cursor < len(prices) - 1 and prices[cursor + 1][0] <= ts:
                    cursor += 1
                factor_cursors[factor] = cursor
                if cursor < len(prices) and prices[cursor][0] <= ts:
                    self.update_price(factor, prices[cursor][1], ts.isoformat())

            snap = self.compute(
                bar_idx=bar_idx,
                win_current=float(row["close"]),
                win_open=opens[data_target],
                session_date=session_date,
            )
            snap.timestamp = ts.isoformat()

            # Cumulative Delta
            bar_d = float(row["delta"]) if "delta" in row.index and row["delta"] else 0.0
            rv = float(row["real_volume"]) if "real_volume" in row.index and row["real_volume"] else 0.0
            cum_delta += bar_d
            cum_real_vol += rv

            snap.bar_delta = round(bar_d, 0)
            snap.cum_delta = round(cum_delta, 0)

            # Normalizar: cum_delta / volume médio por barra
            avg_vol = cum_real_vol / (bar_idx + 1) if bar_idx >= 0 else 1
            if avg_vol > 0:
                snap.cum_delta_norm = round(cum_delta / avg_vol, 3)
            else:
                snap.cum_delta_norm = 0.0

            # Flow confirms
            if snap.p_up > 55 and cum_delta > 0:
                snap.flow_confirms = True
            elif snap.p_up < 45 and cum_delta < 0:
                snap.flow_confirms = True
            elif snap.p_up > 55 and cum_delta < 0:
                snap.flow_confirms = False
            elif snap.p_up < 45 and cum_delta > 0:
                snap.flow_confirms = False
            else:
                snap.flow_confirms = None

            snapshots.append(snap)

        # Restaurar alpha/intercept default (WIN)
        self.alpha, self.intercept = saved_alpha, saved_intercept

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
