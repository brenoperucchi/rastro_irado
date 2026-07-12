"""
IRAI Engine — Cálculo do P_up(t) em tempo real.

Carrega parâmetros calibrados do banco e computa z-scores,
contribuições e probabilidade a cada barra M5.
"""

import sqlite3
import math
import json
import numpy as np
from datetime import datetime, date, timezone, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional

import sys, os
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backend.db import get_connection, DB_PATH, load_kalman_state, save_kalman_state
from backend.irai.kalman import KalmanFilterWrapper
from backend.irai.johansen import check_cointegration
from backend.irai.zscore import (
    DEFAULT_SIGMA, normalized_zscore,
    select_active_pair, pairwise_residual, pair_zscore, pair_signal,
    PAIR_THRESHOLD, PAIR_SIGMA_WINDOW, PAIR_MIN_BETA,
)


# ── Alias de símbolos ─────────────────────────────────────
SYMBOL_ALIASES = {}  # WDO$N agora tem barras próprias no banco

def resolve_symbol(sym: str) -> str:
    """Resolve alias para o símbolo real no banco."""
    return SYMBOL_ALIASES.get(sym, sym)

# Defaults para backward compat
TARGET = "WIN$N"
FACTOR_LABELS = {}  # carregado do DB
BARS_PER_SESSION = 108
# DEFAULT_SIGMA e normalized_zscore vivem em backend/irai/zscore.py (primitiva
# pura, testável sem numpy/pykalman) e são importados no topo deste módulo.


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
    price_diverges: bool = False
    price_diverge_z: Optional[float] = None
    is_preview: bool = False  # True = preview pré-abertura (sem dados do target)
    johansen_p_value: float = 1.0
    is_cointegrated: bool = True
    # Pair z-score (sinal pairwise dinâmico, só v2/Kalman)
    pair_z: float = 0.0
    pair_factor: Optional[str] = None   # label do par ativo (maior |β|)
    pair_beta: float = 0.0
    pair_signal: str = "neutral"        # "buy" | "sell" | "neutral"


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
        self.alpha: float = 1.0
        self.intercept: float = 0.0
        self._load_params()

    def _load_params(self):
        """Carrega configs de asset_models + params de model_params."""
        conn = get_connection(self.db_path)

        # 1) Carregar asset_models
        try:
            rows = conn.execute(
                "SELECT target, slug, display_name, icon, factors, factor_labels, "
                "session_start_h, session_end_h, data_proxy, accuracy, r_squared, active, divergence_config "
                "FROM asset_models WHERE active=1"
            ).fetchall()
        except Exception:
            rows = []

        for row in rows:
            target = row["target"]
            slug = row["slug"]
            factors = json.loads(row["factors"]) if row["factors"] else []
            factor_labels = json.loads(row["factor_labels"]) if row["factor_labels"] else {}
            divergence_config = json.loads(row["divergence_config"]) if row["divergence_config"] else {"sigma": 0.005, "threshold": 0.5}
            
            self.target_slugs[target] = slug
            self.registered_targets.append({
                "target": target, "slug": slug,
                "display_name": row["display_name"], "icon": row["icon"],
                "factors": factors, "factor_labels": factor_labels,
                "session_start_h": row["session_start_h"] or 0,
                "session_end_h": row["session_end_h"] or 24,
                "data_proxy": row["data_proxy"],
                "accuracy": row["accuracy"], "r_squared": row["r_squared"],
                "divergence_config": divergence_config,
            })

            # Determinar prefixo dos params
            prefix = f"{slug}_"

            # 2) Carregar model_params para este slug
            # Usar MAX(effective_from) POR param_name para pegar a versão mais recente
            # de cada parâmetro individualmente (evita bug onde calibrações de outros
            # assets com mesmo prefixo sobrescrevem os params corretos).
            params_cursor = conn.execute("""
                SELECT mp.param_name, mp.value FROM model_params mp
                INNER JOIN (
                    SELECT param_name, MAX(effective_from) as max_eff
                    FROM model_params
                    WHERE param_name LIKE ?
                    GROUP BY param_name
                ) latest ON mp.param_name = latest.param_name
                         AND mp.effective_from = latest.max_eff
                WHERE mp.param_name LIKE ?
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
                "divergence_config": divergence_config,
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
                "divergence_config": m["divergence_config"],
                "use_johansen": m["divergence_config"].get("use_johansen", True),
            }
        # Fallback WIN
        if "win" in self.models:
            m = self.models["win"]
            return m["weights"], m["sigmas"], m["alpha"], m["intercept"], {
                "factors": m["factors"], "labels": m["factor_labels"],
                "param_prefix": "", "session_start_h": 12, "session_end_h": 21,
                "data_proxy": None,
                "divergence_config": m.get("divergence_config", {"sigma": 0.009, "threshold": 0.5}),
                "use_johansen": m.get("divergence_config", {}).get("use_johansen", True),
            }
        return {}, {}, 1.0, 0.0, {"factors": [], "labels": {}, "param_prefix": "",
                                    "session_start_h": 0, "session_end_h": 24, "data_proxy": None, "divergence_config": {"sigma": 0.005, "threshold": 0.5}, "use_johansen": True}

    def set_session_opens(self, opens: dict[str, float], factor_states: dict = None):
        """Define preços de abertura da sessão."""
        fs = factor_states if factor_states is not None else self.factor_states
        self.session_opens = opens
        for label, state in fs.items():
            # Resolver alias de símbolos (atualmente vazio — WDO$N tem barras próprias)
            db_sym = resolve_symbol(state.symbol)
            if db_sym in opens and opens[db_sym] > 0:
                state.open_price = opens[db_sym]
            elif state.symbol in opens and opens[state.symbol] > 0:
                state.open_price = opens[state.symbol]

    def update_price(self, symbol: str, price: float, timestamp: str = None, factor_states: dict = None):
        """Atualiza o preço corrente de um fator."""
        fs = factor_states if factor_states is not None else self.factor_states
        # Procurar nos factor_states pelo símbolo lógico
        for label, state in fs.items():
            if state.symbol == symbol:
                state.current_price = price
                state.last_update = timestamp or datetime.now().isoformat()
                state.stale = False
                return

    def compute(self, bar_idx: int, win_current: float = 0, win_open: float = 0,
                session_date: str = None, stale_threshold_sec: int = 600,
                bars_per_session: int = BARS_PER_SESSION, factor_states: dict = None,
                alpha: float = None, intercept: float = None,
                is_cointegrated: bool = True, johansen_p_value: float = 1.0) -> IRAISnapshot:
        """
        Computa P_up(t) para a barra corrente.

        Args:
            bar_idx: Índice da barra na sessão (0..95)
            win_current: Preço corrente do WIN
            win_open: Preço de abertura do WIN
            session_date: Data da sessão (YYYY-MM-DD)
            stale_threshold_sec: Segundos para considerar um fator stale
            bars_per_session: Quantidade de barras na sessão (default: 108)
            factor_states: Dicionário local opcional de factor_states
            alpha: Alpha local opcional
            intercept: Intercept local opcional
        """
        t_frac = (bar_idx + 1) / bars_per_session
        if t_frac > 1.0:
            t_frac = 1.0
        
        sqrt_t = np.sqrt(t_frac)
        now_str = datetime.now().isoformat()
        session_date = session_date or date.today().isoformat()

        # Computar z-scores e contribuições
        score = 0.0
        stale_factors = []
        
        fs = factor_states if factor_states is not None else self.factor_states
        a = alpha if alpha is not None else self.alpha
        i_cept = intercept if intercept is not None else self.intercept

        for label, state in fs.items():
            # Retorno desde open
            if state.open_price > 0 and state.current_price > 0:
                state.ret = (state.current_price - state.open_price) / state.open_price
            else:
                state.ret = 0.0
                state.stale = True

            # Z-score normalizado por tempo (σ≤0 usa piso, não zera o sinal)
            state.z_score = normalized_zscore(state.ret, state.sigma, sqrt_t)

            # Contribuição
            state.contribution = state.weight * state.z_score
            score += state.contribution

            # Check stale
            if state.stale:
                stale_factors.append(label)

        # P_up via sigmoid
        p_up = sigmoid(a * score + i_cept) * 100.0

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

        # Guardrail Johansen
        if not is_cointegrated:
            # Em vez de forçar p_up = 50.0 (o que causa quedas verticais agressivas no gráfico),
            # apenas marcamos o veredito como indeciso, mas deixamos o P(↑) fluir matematicamente.
            verdict, verdict_color = "indeciso (Sem Coint)", "#7A7F8A"

        return IRAISnapshot(
            timestamp=now_str,
            session_date=session_date,
            bar_idx=bar_idx,
            t_frac=t_frac,
            p_up=round(p_up, 2),
            score=round(score, 4),
            verdict=verdict,
            verdict_color=verdict_color,
            johansen_p_value=round(johansen_p_value, 4),
            is_cointegrated=is_cointegrated,

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
                for label, state in fs.items()
            },
            win_return=round(win_return, 4),
            win_open=win_open,
            win_current=win_current,
            stale_factors=stale_factors,
        )

    def compute_from_db(self, session_date: str = None, target: str = None, version: str = "v1",
                        persist_state: bool = True) -> list[IRAISnapshot]:
        """persist_state=False para replay histórico/backtest: não grava o estado
        do Kalman. O `save_kalman_state` já é monotônico (db.py) e rejeitaria o
        write antigo, mas o replay não deve sequer tentar — quem manda no estado
        vivo é a sessão viva. Ver Fase 4 do plano e tests/test_kalman_state.py."""
        """Computa série IRAI completa para uma sessão a partir do banco.
        
        Args:
            session_date: Data YYYY-MM-DD
            target: Símbolo alvo (WIN$N, WDO$N). Default: WIN$N
            version: 'v1' para regressão estática, 'v2' para Kalman dinâmico
        """
        session_date = session_date or date.today().isoformat()
        target = target or TARGET

        # Carregar modelo correto para o target
        t_weights, t_sigmas, t_alpha, t_intercept, cfg = self._get_model_config(target)
        active_factors = cfg["factors"]
        active_labels = cfg["labels"]

        # Setup factor states para este target
        local_factor_states = {}
        for symbol, label in active_labels.items():
            local_factor_states[label] = FactorState(
                symbol=symbol,
                label=label,
                weight=t_weights.get(f"w_{label}", 0.0),
                sigma=t_sigmas.get(label, DEFAULT_SIGMA),
            )
        
        local_alpha = t_alpha
        local_intercept = t_intercept

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

        rows_raw = conn.execute(query, all_symbols + [start, end]).fetchall()
        
        # Load previous Kalman state if valid
        slug = cfg.get("slug", self.target_slugs.get(target, "win"))
        saved_state = load_kalman_state(conn, slug)

        # A sessão VIVA é a última que o target tem no banco. Só ela pode gravar
        # o estado do Kalman — replay histórico (date-picker, fallback do
        # /api/irai/current, scripts) é read-only. O guard monotônico do
        # save_kalman_state é a segunda linha de defesa, mas ele só protege
        # quando JÁ existe estado mais novo; num banco recém-resetado um replay
        # antigo semearia o prior vivo. Comparar contra as sessões do próprio
        # target evita misturar fusos (B3=BRT vs Tickmill=EEST).
        latest_row = conn.execute(
            "SELECT MAX(substr(timestamp_utc, 1, 10)) AS d FROM market_bars "
            "WHERE symbol = ? AND timeframe = 'M5'", (data_target,)).fetchone()
        latest_session = latest_row["d"] if latest_row else None
        is_live_session = (latest_session is not None and session_date >= latest_session)

        conn.close()

        if not rows_raw:
            return []

        # Detectar sessão via config do modelo (antes de iterar rows)
        session_start = cfg.get("session_start_h", 0)
        is_b3 = session_start != 0

        # Converter sqlite3.Row para dicts com timestamp parsed e hora extraída
        rows = []
        for r in rows_raw:
            d = dict(r)
            ts_str = d["timestamp_utc"]
            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            
            # Alinhar fuso horário: XP (BRT, UTC-3) para Tickmill (EEST, UTC+3) = +6 horas
            if is_b3 and d["symbol"] == data_target:
                ts_dt += timedelta(hours=6)
                d["timestamp_utc"] = ts_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            d["timestamp"] = ts_dt
            d["hour"] = d["timestamp"].hour
            rows.append(d)

        # Detectar sessão via config do modelo
        session_start = cfg.get("session_start_h", 0)
        session_end = cfg.get("session_end_h", 24)

        duration_h = session_end - session_start
        if duration_h <= 0:
            duration_h += 24
        
        target_bars_per_session = int(duration_h * 12)
        if target_bars_per_session <= 0:
            target_bars_per_session = BARS_PER_SESSION

        target_rows = [r for r in rows if r["symbol"] == data_target]
        is_preview_mode = len(target_rows) == 0

        if is_preview_mode:
            # ── Preview pré-abertura ──────────────────────────────────────
            conn2 = get_connection(self.db_path)
            last_row = conn2.execute("""
                SELECT close FROM market_bars
                WHERE symbol = ? AND timeframe = 'M5'
                ORDER BY timestamp_utc DESC LIMIT 1
            """, (data_target,)).fetchone()
            conn2.close()

            if not last_row:
                return []

            target_last_close = float(last_row["close"])

            factor_rows = [r for r in rows if r["symbol"] in db_factors]
            if not factor_rows:
                return []

            all_timestamps = sorted(set(r["timestamp"] for r in factor_rows))

            opens = {}
            for sym in db_factors:
                sym_bars = sorted([r for r in rows if r["symbol"] == sym], key=lambda r: r["timestamp"])
                if sym_bars:
                    opens[sym] = float(sym_bars[0]["open"])
            opens[data_target] = target_last_close

            self.set_session_opens(opens, factor_states=local_factor_states)

            factor_prices = {}
            for factor in active_factors:
                db_sym = factor_to_db.get(factor, factor)
                fb = sorted([r for r in rows if r["symbol"] == db_sym], key=lambda r: r["timestamp"])
                factor_prices[factor] = [(r["timestamp"], float(r["close"])) for r in fb] if fb else []

            factor_cursors = {f: 0 for f in active_factors}
            snapshots = []

            for bar_idx, ts in enumerate(all_timestamps):
                for factor in active_factors:
                    prices = factor_prices[factor]
                    cursor = factor_cursors[factor]
                    while cursor < len(prices) - 1 and prices[cursor + 1][0] <= ts:
                        cursor += 1
                    factor_cursors[factor] = cursor
                    if cursor < len(prices) and prices[cursor][0] <= ts:
                        self.update_price(factor, prices[cursor][1], ts.isoformat() if hasattr(ts, 'isoformat') else str(ts), factor_states=local_factor_states)

                snap = self.compute(
                    bar_idx=bar_idx,
                    win_current=target_last_close,
                    win_open=target_last_close,
                    session_date=session_date,
                    bars_per_session=target_bars_per_session,
                    factor_states=local_factor_states,
                    alpha=local_alpha,
                    intercept=local_intercept,
                )
                snap.timestamp = ts.isoformat() if hasattr(ts, 'isoformat') else str(ts)
                snap.is_preview = True
                snap.is_ghost = True
                snap.flow_confirms = None
                snapshots.append(snap)

            return snapshots

        # ── Modo normal (target tem barras) ───────────────────────────────
        # Filter by session removed to allow 24/7 continuous timeline

        # Opens = primeira barra de cada símbolo
        opens = {}
        for sym in all_symbols:
            sym_bars = sorted([r for r in rows if r["symbol"] == sym], key=lambda r: r["timestamp"])
            if sym_bars:
                opens[sym] = float(sym_bars[0]["open"])

        if data_target not in opens:
            return []

        self.set_session_opens(opens, factor_states=local_factor_states)

        # Pré-indexar preços dos fatores por timestamp (O(n) em vez de O(n²))
        factor_prices = {}
        for factor in active_factors:
            db_sym = factor_to_db.get(factor, factor)
            fb = sorted([r for r in rows if r["symbol"] == db_sym], key=lambda r: r["timestamp"])
            factor_prices[factor] = [(r["timestamp"], float(r["close"])) for r in fb] if fb else []

        # Iterar sobre barras do target e alinhar com todas as timestamps para forward-fill
        target_bars = sorted([r for r in rows if r["symbol"] == data_target], key=lambda r: r["timestamp"])
        all_timestamps = sorted(set(r["timestamp"] for r in rows))
        n_bars = len(all_timestamps)
        snapshots = []
        cum_delta = 0.0
        cum_real_vol = 0.0
        # -1 = sentinela de PRÉ-MERCADO ("o target ainda não negociou hoje").
        # Os fatores globais negociam 24h e all_timestamps é a UNIÃO, então há
        # ~180 barras M5 antes da 1ª barra do target B3. Ver Fase 3 do plano e
        # tests/test_premarket.py.
        target_cursor = -1
        
        # --- Kalman Filter Setup ---
        kf = None
        if version == "v2":
            n_dim_state = 1 + len(active_factors) # Intercept + Factors
            initial_state_mean = np.zeros(n_dim_state)
            initial_state_mean[0] = local_intercept
            for i, factor in enumerate(active_factors):
                label = active_labels.get(factor, factor)
                initial_state_mean[i+1] = t_weights.get(f"w_{label}", 0.0)
                
            trans_cov = float(t_sigmas.get("kalman_trans_cov", 1e-5))
            obs_cov = float(t_sigmas.get("kalman_obs_cov", 1e-3))
            
            kf = KalmanFilterWrapper(
                n_dim_state=n_dim_state,
                n_dim_obs=1,
                transition_covariance=trans_cov,
                observation_covariance=obs_cov,
                initial_state_mean=initial_state_mean
            )
            
            if saved_state:
                state_ts = datetime.fromisoformat(saved_state["timestamp_utc"].replace("Z", "+00:00"))
                session_start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                if state_ts < session_start_dt:
                    kf.set_state(saved_state["state_mean"], saved_state["state_covariance"])
        
        # --- Johansen Setup ---
        johansen_lookback = int(t_sigmas.get("johansen_lookback", 50))
        price_history = []

        factor_cursors = {f: 0 for f in active_factors}

        div_cfg = cfg.get("divergence_config", {"sigma": 0.005, "threshold": 0.5})
        use_johansen = cfg.get("use_johansen", True)
        target_div_sigma = div_cfg.get("sigma", 0.005)
        target_div_threshold = div_cfg.get("threshold", 0.5)

        # Pair z-score (params via divergence_config; ver backend/irai/zscore.py).
        # Só computado no v2 (precisa dos betas do Kalman). Buffer de resíduos
        # acumula ao longo da sessão para o σ rolling.
        pair_threshold = float(div_cfg.get("pair_threshold", PAIR_THRESHOLD))
        pair_sigma_window = int(div_cfg.get("pair_sigma_window", PAIR_SIGMA_WINDOW))
        pair_min_beta = float(div_cfg.get("pair_min_beta", PAIR_MIN_BETA))
        pair_residual_history = []
        prev_pair_label = None  # p/ resetar o histórico quando o par ativo muda

        # Set de timestamps reais do target para detecção precisa de ghost bars
        target_ts_set = set(r["timestamp"] for r in target_bars)

        # Buscar último close de ontem para ghost bars pré-mercado
        conn_prev = get_connection(self.db_path)
        prev_close_row = conn_prev.execute("""
            SELECT close FROM market_bars
            WHERE symbol = ? AND timeframe = 'M5' AND timestamp_utc < ?
            ORDER BY timestamp_utc DESC LIMIT 1
        """, (data_target, f"{session_date}T00:00:00Z")).fetchone()
        conn_prev.close()
        pre_market_close = float(prev_close_row["close"]) if prev_close_row else opens.get(data_target, 0)
        pre_market_open = pre_market_close  # Open = last known close for ghost bars

        for bar_idx, ts in enumerate(all_timestamps):
            while target_cursor < len(target_bars) - 1 and target_bars[target_cursor + 1]["timestamp"] <= ts:
                target_cursor += 1
                
            is_pre_market = (target_cursor < 0)
            
            if not is_pre_market and target_cursor < len(target_bars) \
                    and target_bars[target_cursor]["timestamp"] <= ts:
                row = target_bars[target_cursor]
                is_ghost_bar = (ts not in target_ts_set)
            else:
                # Pré-mercado: target ainda não tem barras para este timestamp
                # Criar barra sintética com o último close conhecido
                row = {"close": pre_market_close, "open": pre_market_open, "delta": 0, "real_volume": 0, "timestamp": ts}
                is_ghost_bar = True

            for factor in active_factors:
                prices = factor_prices[factor]
                cursor = factor_cursors[factor]
                while cursor < len(prices) - 1 and prices[cursor + 1][0] <= ts:
                    cursor += 1
                factor_cursors[factor] = cursor
                if cursor < len(prices) and prices[cursor][0] <= ts:
                    self.update_price(factor, prices[cursor][1], ts.isoformat(), factor_states=local_factor_states)

            is_coint = True
            p_val = 0.01
            pending_pair = None  # (z_pair, label, beta, signal) — só preenchido no v2

            if version == "v2":
                # `sem_print` = o target não imprimiu neste timestamp: pré-mercado
                # (antes da 1ª barra) ou gap intra-sessão (leilão/halt/feed). Nos
                # dois casos NÃO há observação nova do target — o preço é
                # sintético/forward-filled. Alimentar o Kalman ou o Johansen com
                # isso fabrica informação. Ver Fase 3 + review Codex.
                sem_print = is_pre_market or is_ghost_bar

                # Preparar dados para Johansen (Preços)
                if use_johansen and not sem_print:
                    basket_prices = {"target": float(row["close"])}
                    for factor in active_factors:
                        label = active_labels.get(factor, factor)
                        basket_prices[label] = local_factor_states[label].current_price
                    price_history.append(basket_prices)
                    
                    if len(price_history) > johansen_lookback:
                        price_history.pop(0)
                        
                    if len(price_history) >= 20:
                        df_basket = pd.DataFrame(price_history)
                        p_val, is_coint = check_cointegration(df_basket)
                
                # Preparar dados para Kalman (Retornos)
                win_ret = 0.0
                if opens[data_target] > 0 and not is_pre_market:
                    win_ret = (float(row["close"]) - opens[data_target]) / opens[data_target]
                
                obs_matrix = [1.0] # Intercept
                for factor in active_factors:
                    label = active_labels.get(factor, factor)
                    state = local_factor_states[label]
                    f_ret = 0.0
                    if state.open_price > 0 and state.current_price > 0:
                        f_ret = (state.current_price - state.open_price) / state.open_price
                    obs_matrix.append(f_ret)
                    
                # Kalman: no pré-mercado NÃO há observação do target — roda só o
                # time update (média intocada sob F=I, covariância difunde em Q).
                # Alimentar observação (a falsa de antes, ou zero) apagaria o
                # prior: a memória efetiva do filtro é sqrt(R/Q)=10 barras contra
                # ~180 barras de pré-mercado (=18 memórias). A difusão deixa o
                # filtro corretamente mais receptivo à 1ª barra real após o gap.
                if sem_print:
                    kf.predict(np.array([obs_matrix]))
                else:
                    kf.update(observation=win_ret, observation_matrix=np.array([obs_matrix]))
                current_betas, _ = kf.get_state()
                
                # Update local weights based on Kalman
                local_intercept = current_betas[0]
                for i, factor in enumerate(active_factors):
                    label = active_labels.get(factor, factor)
                    local_factor_states[label].weight = current_betas[i+1]

                # --- Pair z-score (sinal pairwise dinâmico) ---
                # Par ativo = fator de maior |β| no Kalman deste bar (excluindo σ
                # quase-nula); resíduo = retorno do target − β·retorno_do_par; z de
                # reversão à média (centrado, sem √t) sobre a janela do resíduo.
                # obs_matrix[idx+1] é o retorno do fator.
                pair_labels = [active_labels.get(f, f) for f in active_factors]
                pair_sigmas = [local_factor_states[lbl].sigma for lbl in pair_labels]
                pair = select_active_pair(current_betas, pair_labels, pair_min_beta,
                                          sigmas=pair_sigmas)
                if pair is not None and not is_pre_market:
                    # Se o par ativo mudou, zera o histórico: média/σ do z não podem
                    # misturar resíduos de spreads diferentes (senão a troca de par
                    # gera um falso deslocamento).
                    if pair["label"] != prev_pair_label:
                        pair_residual_history = []
                        prev_pair_label = pair["label"]
                    ret_pair_factor = obs_matrix[pair["index"] + 1]
                    residual = pairwise_residual(win_ret, pair["beta"], ret_pair_factor)
                    pair_residual_history.append(residual)
                    # z de reversão à média do resíduo (centrado, sem √t) — evita o
                    # z degenerado quando o par é um hedge ruim (resíduo em tendência).
                    z_pair = pair_zscore(pair_residual_history, pair_sigma_window)
                    pending_pair = (
                        z_pair, pair["label"], pair["beta"],
                        pair_signal(z_pair, pair["beta"], pair_threshold),
                    )
            else:
                # V1: Garantir que os pesos sejam os estáticos (default setup at the start)
                pass

            snap = self.compute(
                bar_idx=bar_idx,
                win_current=float(row["close"]),
                win_open=pre_market_open if is_pre_market else opens[data_target],
                session_date=session_date,
                bars_per_session=target_bars_per_session,
                factor_states=local_factor_states,
                alpha=local_alpha,
                intercept=local_intercept,
                is_cointegrated=is_coint,
                johansen_p_value=p_val
            )
            snap.timestamp = ts.isoformat()
            snap.is_ghost = is_ghost_bar

            if pending_pair is not None:
                snap.pair_z, snap.pair_factor, snap.pair_beta, snap.pair_signal = pending_pair

            if is_pre_market:
                snap.win_return = 0.0

            # Cumulative Delta
            bar_d = 0.0 if is_ghost_bar else float(row.get("delta") or 0)
            rv = 0.0 if is_ghost_bar else float(row.get("real_volume") or 0)
            cum_delta += bar_d
            cum_real_vol += rv

            snap.bar_delta = round(bar_d, 0)
            snap.cum_delta = round(cum_delta, 0)

            avg_vol = cum_real_vol / (bar_idx + 1) if bar_idx >= 0 else 1
            if avg_vol > 0:
                snap.cum_delta_norm = round(cum_delta / avg_vol, 3)
            else:
                snap.cum_delta_norm = 0.0

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

            # Price Divergence
            if target_div_sigma > 0 and snap.t_frac > 0:
                ret_frac = snap.win_return / 100.0
                ret_z = ret_frac / (target_div_sigma * math.sqrt(snap.t_frac))
                snap.price_diverge_z = round(ret_z, 2)
                if snap.p_up > 55 and ret_z < -target_div_threshold:
                    snap.price_diverges = True
                elif snap.p_up < 45 and ret_z > target_div_threshold:
                    snap.price_diverges = True

            snapshots.append(snap)
            
        # Salvar o último estado do Kalman no banco
        if snapshots and version == "v2" and kf is not None and persist_state and is_live_session:
            last_ts = snapshots[-1].timestamp
            last_p = snapshots[-1].johansen_p_value
            last_coint = snapshots[-1].is_cointegrated
            s_mean, s_cov = kf.get_state()
            conn2 = get_connection(self.db_path)
            save_kalman_state(conn2, slug, s_mean, s_cov, last_p, last_coint, last_ts)
            conn2.close()

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
