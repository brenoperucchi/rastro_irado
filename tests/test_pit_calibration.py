"""Spec de scripts/pit_calibration.py — calibração point-in-time (achado C1-a).

`build_schedule()` normalmente chamaria `calibrate_universal.calibrate_target`,
que importa sklearn (lazy, só dentro da função) — ausente neste ambiente
Linux de dev. Todos os testes injetam `calibrate_fn` fake (mesmo padrão de
`kalman_cls` em chronological_replay) — produção sempre usa o default real.

Roda sem pytest:  python3 tests/test_pit_calibration.py
Ou com pytest:    pytest tests/test_pit_calibration.py
"""
import os
import sqlite3
import sys
import tempfile
import types
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pykalman  # noqa: F401
except ModuleNotFoundError:
    stub = types.ModuleType("pykalman")
    stub.KalmanFilter = object
    sys.modules["pykalman"] = stub
try:
    import statsmodels  # noqa: F401
except ModuleNotFoundError:
    for _sub in ("statsmodels", "statsmodels.tsa", "statsmodels.tsa.vector_ar",
                 "statsmodels.tsa.vector_ar.vecm"):
        sys.modules[_sub] = types.ModuleType(_sub)
    sys.modules["statsmodels.tsa.vector_ar.vecm"].coint_johansen = lambda *a, **k: None

import test_premarket as tp  # reaproveita _seed/TARGET/SLUG
from test_measure_pair_signal_value import _snap

import scripts.measure_pair_signal_value as psv
from scripts.pit_calibration import (
    PitEntry,
    PitSchedule,
    build_schedule,
    div_sigma_as_of,
)
from scripts.measure_d1_inflation import readonly_connection


def _duplicate_session_across_dates(db_path, dates):
    """Copia as barras de market_bars da sessão semeada por tp._seed
    (2026-07-10) pra cada data extra em `dates`. Escala só o CLOSE (open/
    high/low ficam como estão) por um fator que varia por data — isso
    muda de verdade o retorno diário (last_close/first_open - 1) a cada
    data. Achado do /codex-r: uma versão anterior escalava open E close
    pelo MESMO fator, o que preserva exatamente a razão close/open —
    produzindo o retorno IDÊNTICO em toda data duplicada (só a barra órfã
    do fixture original introduzia alguma variância, por acidente, não
    por desenho do teste)."""
    c = sqlite3.connect(db_path)
    rows = c.execute(
        "SELECT symbol, source, timeframe, timestamp_utc, open, high, low, close, "
        "volume, real_volume, delta FROM market_bars WHERE timestamp_utc LIKE '2026-07-10%'"
    ).fetchall()
    for i, date in enumerate(dates, start=1):
        close_mult = 1.0 + 0.01 * i * (-1 if i % 2 == 0 else 1)  # alterna sobe/desce
        for row in rows:
            symbol, source, timeframe, ts, o, h, l, close, vol, rvol, delta = row
            new_ts = ts.replace("2026-07-10", date)
            new_close = close * close_mult if close else close
            c.execute(
                "INSERT OR IGNORE INTO market_bars (symbol, source, timeframe, timestamp_utc, "
                "open, high, low, close, volume, real_volume, delta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (symbol, source, timeframe, new_ts, o, h, l, new_close, vol, rvol, delta),
            )
    c.commit()
    c.close()


def _fake_calibrate_fn(factors=None, vary_factors_after=None):
    """Fábrica de calibrate_fn fake: devolve um dict de calibração
    determinístico, com `factors` fixo (ou variando após um índice, pra
    testar o assert de assinatura estável). Ignora `daily_override` — não
    precisa dele pra testar a mecânica do schedule."""
    factors = factors or ["US500"]
    calls = []

    def fn(conn, target, s_start, s_end, proxy, min_factors=None, max_factors=None,
           forced_factors=None, holdout_sessions=50, as_of=None, daily_override=None):
        calls.append(as_of)
        idx = len(calls)
        used_factors = (
            list(reversed(factors)) if (vary_factors_after and idx > vary_factors_after)
            else list(factors)
        )
        return {
            "factors": used_factors,
            "factor_labels": {f: f.lower() for f in used_factors},
            "weights": {f.lower(): 1.0 + 0.1 * idx for f in used_factors},
            "sigmas": {f.lower(): 0.01 for f in used_factors},
            "alpha": 1.0,
            "intercept": 0.0,
        }

    fn.calls = calls
    return fn


# ── 1. div_sigma_as_of ──────────────────────────────────────────────────

def test_div_sigma_as_of_calcula_desvio_padrao_dos_retornos_diarios():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    tp._seed(db)
    extra_dates = [f"2026-07-{d:02d}" for d in range(11, 20)]  # +9 sessões
    _duplicate_session_across_dates(db, extra_dates)

    conn = readonly_connection(db)
    try:
        # Ambos os cutoffs cobrem >= 5 sessões reais (não caem no fallback
        # de amostra pequena) — só assim a diferença entre eles prova que
        # o corte "<=" está filtrando de verdade, não coincidência com o
        # fallback (achado do /codex-r sobre o fixture original).
        sigma_full = div_sigma_as_of(conn, tp.TARGET, "2026-07-19")   # 10 sessões
        sigma_partial = div_sigma_as_of(conn, tp.TARGET, "2026-07-15")  # 6 sessões
    finally:
        conn.close()

    assert sigma_full > 0.03  # variação de até ±9% no close entre datas
    assert sigma_partial > 0.01
    assert sigma_full != sigma_partial
    assert round(sigma_full, 4) == sigma_full  # arredondado a 4 casas, como calc_sigmas.py


def test_div_sigma_as_of_usa_default_com_poucos_dados():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    tp._seed(db)  # só 1 sessão -> menos de 5 retornos diários
    conn = readonly_connection(db)
    try:
        sigma = div_sigma_as_of(conn, tp.TARGET, "2026-07-10")
    finally:
        conn.close()
    assert sigma == 0.005  # default de backend/irai/engine.py


# ── 2. PitSchedule ───────────────────────────────────────────────────────

class _FakeEngine:
    def __init__(self, target_slugs, models):
        self.target_slugs = target_slugs
        self.models = models


def _make_fake_engine():
    return _FakeEngine(
        target_slugs={"WIN$N": "win"},
        models={"win": {
            "factors": ["OLD"], "factor_labels": {"OLD": "old"},
            "weights": {"w_old": 0.0}, "sigmas": {"old": 0.01},
            "alpha": 0.0, "intercept": 0.0,
            "divergence_config": {"sigma": 0.005, "threshold": 0.5},
        }},
    )


def test_apply_for_session_retorna_false_mas_ja_aplica_1a_calibracao_pra_aquecer():
    """Antes do 1º cutoff a sessão continua NÃO mensurável (retorna False),
    mas a cesta FIXA do 1º cutoff disponível já é aplicada (não fica na
    cesta default do fixture) — achado do /codex-r: sem isso, a troca de
    cesta no instante em que o schedule "liga" causaria um cold-restart
    silencioso do Kalman logo no início da janela medida."""
    entries = {"WIN$N": [PitEntry(cutoff="2023-01-01", calibration={
        "factors": ["US500"], "factor_labels": {"US500": "us500"},
        "weights": {"us500": 1.0}, "sigmas": {"us500": 0.01},
        "alpha": 1.0, "intercept": 0.0,
    }, div_sigma=0.007)]}
    schedule = PitSchedule(entries)
    engine = _make_fake_engine()
    assert schedule.apply_for_session(engine, "WIN$N", "2022-06-01") is False
    assert engine.models["win"]["factors"] == ["US500"], (
        "a cesta fixa devia já estar aplicada, mesmo com a sessão marcada "
        "como não-mensurável — só assim o Kalman aquece na cesta certa")


def test_apply_for_session_retorna_false_sem_nenhum_cutoff_disponivel():
    """Schedule vazio pro target (build_schedule não achou nenhum cutoff
    viável) -> sempre False, sem tentar aplicar nada (não há entries[0])."""
    schedule = PitSchedule({"WIN$N": []})
    engine = _make_fake_engine()
    assert schedule.apply_for_session(engine, "WIN$N", "2026-01-01") is False
    assert engine.models["win"]["factors"] == ["OLD"]  # sem mutação, nada disponível


def test_apply_for_session_aplica_calibracao_apos_cutoff():
    entries = {"WIN$N": [PitEntry(cutoff="2023-01-01", calibration={
        "factors": ["US500"], "factor_labels": {"US500": "us500"},
        "weights": {"us500": 1.0}, "sigmas": {"us500": 0.01},
        "alpha": 1.0, "intercept": 0.0,
    }, div_sigma=0.007)]}
    schedule = PitSchedule(entries)
    engine = _make_fake_engine()
    assert schedule.apply_for_session(engine, "WIN$N", "2023-01-02") is True
    assert engine.models["win"]["factors"] == ["US500"]
    assert engine.models["win"]["weights"] == {"w_us500": 1.0}
    assert engine.models["win"]["divergence_config"]["sigma"] == 0.007


def test_apply_for_session_troca_de_cutoff_ao_avancar_no_tempo():
    entries = {"WIN$N": [
        PitEntry(cutoff="2023-01-01", calibration={
            "factors": ["US500"], "factor_labels": {"US500": "us500"},
            "weights": {"us500": 1.0}, "sigmas": {"us500": 0.01},
            "alpha": 1.0, "intercept": 0.0,
        }, div_sigma=0.007),
        PitEntry(cutoff="2023-06-01", calibration={
            "factors": ["US500"], "factor_labels": {"US500": "us500"},
            "weights": {"us500": 2.0}, "sigmas": {"us500": 0.02},
            "alpha": 1.0, "intercept": 0.0,
        }, div_sigma=0.009),
    ]}
    schedule = PitSchedule(entries)
    engine = _make_fake_engine()
    schedule.apply_for_session(engine, "WIN$N", "2023-03-01")
    assert engine.models["win"]["weights"] == {"w_us500": 1.0}
    schedule.apply_for_session(engine, "WIN$N", "2023-08-01")
    assert engine.models["win"]["weights"] == {"w_us500": 2.0}
    assert engine.models["win"]["divergence_config"]["sigma"] == 0.009


# ── 3. build_schedule ────────────────────────────────────────────────────

def test_build_schedule_gera_1_entrada_por_cutoff_com_dados_suficientes():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    tp._seed(db)
    extra_dates = [f"2026-07-{d:02d}" for d in range(11, 20)]
    _duplicate_session_across_dates(db, extra_dates)

    fake_fn = _fake_calibrate_fn(factors=["US500"])
    schedule = build_schedule(
        db, ["WIN$N"], cutoffs=["2026-07-15", "2026-07-19"],
        forced_baskets={"WIN$N": ["US500"]}, calibrate_fn=fake_fn,
    )
    cutoffs_used = schedule.cutoffs_used("WIN$N")
    assert cutoffs_used == ["2026-07-15", "2026-07-19"]
    assert len(fake_fn.calls) == 2


def test_build_schedule_pula_cutoff_quando_calibrate_fn_devolve_none():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    tp._seed(db)

    def fn(*args, **kwargs):
        as_of = kwargs.get("as_of")
        if as_of == "2020-01-01":
            return None  # dados insuficientes pra este cutoff
        return {
            "factors": ["US500"], "factor_labels": {"US500": "us500"},
            "weights": {"us500": 1.0}, "sigmas": {"us500": 0.01},
            "alpha": 1.0, "intercept": 0.0,
        }

    schedule = build_schedule(
        db, ["WIN$N"], cutoffs=["2020-01-01", "2026-07-10"],
        forced_baskets={"WIN$N": ["US500"]}, calibrate_fn=fn,
    )
    assert schedule.cutoffs_used("WIN$N") == ["2026-07-10"]


def test_build_schedule_rejeita_cesta_que_varia_entre_cutoffs():
    """A cesta forçada precisa ser a MESMA em todos os cutoffs (senão o
    encadeamento do Kalman quebra silenciosamente nas fronteiras — achado
    da revisão de design via deep-reasoner/fable-reasoner). Se
    calibrate_fn devolver uma cesta diferente a partir de um certo cutoff,
    build_schedule deve estourar, não seguir em frente silenciosamente —
    o assert por-iteração (bate exatamente com `forced_baskets`, achado do
    /codex-r: o assert original só pegava inconsistência ENTRE cutoffs, não
    uma cesta sistematicamente errada) já pega isso na 2ª chamada."""
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    tp._seed(db)
    fake_fn = _fake_calibrate_fn(factors=["US500", "XAUUSD"], vary_factors_after=1)
    try:
        build_schedule(
            db, ["WIN$N"], cutoffs=["2026-07-05", "2026-07-10"], calibrate_fn=fake_fn,
        )
        assert False, "deveria ter estourado AssertionError"
    except AssertionError as e:
        assert "cesta diferente da forçada" in str(e)


def test_build_schedule_rejeita_cesta_consistentemente_errada():
    """calibrate_fn devolve a MESMA cesta errada em todos os cutoffs
    (internamente consistente, mas nunca bate com forced_baskets) — o
    assert por-iteração precisa pegar isso mesmo sem nenhuma variação
    entre cutoffs (achado do /codex-r: o assert original, que só checava
    `len(signatures) <= 1`, deixaria passar silenciosamente)."""
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    tp._seed(db)
    fake_fn = _fake_calibrate_fn(factors=["OUTRO_FATOR"])  # nunca é a cesta forçada real
    try:
        build_schedule(
            db, ["WIN$N"], cutoffs=["2026-07-05", "2026-07-10"], calibrate_fn=fake_fn,
        )
        assert False, "deveria ter estourado AssertionError"
    except AssertionError as e:
        assert "cesta diferente da forçada" in str(e)


# ── 4. Integração: run() + pit_schedule real através de chronological_replay fake ──

def test_run_aplica_pit_schedule_e_conta_sessoes_pre_cutoff():
    """3 sessões: 2 antes do único cutoff, 1 depois. Todas geram o mesmo
    evento de compra na barra 0 — prova que as pré-cutoff são REPLAYADAS
    (não quebram o loop) mas ficam fora da medição, contadas em
    `sessions_before_first_pit_cutoff`, distinto de sessions_burn_in."""
    dates = ["2022-01-01", "2022-01-02", "2023-06-01"]

    class _FakeCandidates:
        pass
    _FakeCandidates.dates = dates
    _FakeCandidates.discarded = []

    engine = _make_fake_engine()
    factors_seen_at_compute = []

    @contextmanager
    def fake_replay(db_path):
        def compute(date, target):
            # Captura o estado do engine NO MOMENTO da chamada — prova que
            # apply_for_session() já rodou antes de compute() (achado do
            # /codex-r: o teste original não provava a ORDEM, só que
            # apply_for_session era chamado em algum momento).
            factors_seen_at_compute.append(tuple(engine.models["win"]["factors"]))
            return [_snap(0, 100.0, pair_compra=100.0)] + [
                _snap(j, 100.0 + j * 5.0) for j in range(1, 25)
            ]
        yield compute, engine

    entries = {"WIN$N": [PitEntry(cutoff="2023-01-01", calibration={
        "factors": ["US500"], "factor_labels": {"US500": "us500"},
        "weights": {"us500": 1.0}, "sigmas": {"us500": 0.01},
        "alpha": 1.0, "intercept": 0.0,
    }, div_sigma=0.007)]}
    schedule = PitSchedule(entries)

    with patch.object(psv, "candidate_sessions", lambda db, target, limit: _FakeCandidates), \
         patch.object(psv, "chronological_replay", fake_replay):
        report = psv.run("unused.db", ["WIN$N"], limit=3, iterations=50, burn_in_sessions=0,
                          pit_schedule=schedule)

    t = report["targets"]["WIN$N"]
    assert t["pit_mode"] is True
    assert t["sessions_before_first_pit_cutoff"] == 2
    # A cesta fixa já estava ativa em TODAS as 3 chamadas de compute(),
    # inclusive as 2 pré-cutoff (achado do /codex-r sobre o cold-restart:
    # sem aplicar a calibração do 1º cutoff retroativamente pro
    # aquecimento, essas 2 sessões teriam rodado com a cesta default do
    # engine, não a fixa).
    assert factors_seen_at_compute == [("US500",), ("US500",), ("US500",)]
    assert t["by_direction"]["all"]["n_events"] == 1, (
        "só a sessão de 2023-06-01 (depois do cutoff) devia contar")
    # Confirma que a calibração foi realmente aplicada no engine em memória.
    assert engine.models["win"]["factors"] == ["US500"]


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL {name}: {e}")
    print("todos passaram" if not fails else f"{fails} falha(s)")
    sys.exit(1 if fails else 0)
