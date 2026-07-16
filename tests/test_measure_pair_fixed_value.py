"""Spec do challenger Pair fixo WIN-WDO (IRAI-21 / IRAI-4 AC#3).

Ref: scripts/measure_pair_fixed_value.py. A metodologia de extração/medição
(entrada no open da barra seguinte, cooldown, MFE/MAE OHLC, timestamps causais,
bootstrap) é REUSADA de measure_pair_signal_value.py e já tem cobertura própria.
Este arquivo testa só o que é novo aqui: o cálculo do par FIXO (β OLS rolling,
alinhamento WIN-WDO por timestamp, marker edge-triggered causal) e a
integração via run() com o replay injetado.

Roda sem pytest:  python3 tests/test_measure_pair_fixed_value.py
Ou com pytest:    pytest tests/test_measure_pair_fixed_value.py
"""
import os
import sqlite3
import sys
import tempfile
import types
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

from backend.db import SCHEMA, migrate_divergence_config
from backend.irai.zscore import PAIR_SIGMA_WINDOW

import scripts.measure_pair_fixed_value as pf


def _seed_win_wdo(db_path, win_closes, wdo_closes, *, session="2026-07-10", start_hour=12):
    """Semeia market_bars com barras M5 alinhadas de WIN$N e WDO$N (mesmos
    timestamps). open=high=low=close por simplicidade (OHLC presente)."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    for i, (wc, dc) in enumerate(zip(win_closes, wdo_closes)):
        ts = f"{session}T{start_hour:02d}:{(i * 5) % 60:02d}:{'00'}Z"
        # timestamps únicos: avança a hora quando os minutos dão a volta
        hh = start_hour + (i * 5) // 60
        ts = f"{session}T{hh:02d}:{(i * 5) % 60:02d}:00Z"
        for sym, c in (("WIN$N", wc), ("WDO$N", dc)):
            conn.execute(
                "INSERT INTO market_bars (symbol, source, timeframe, timestamp_utc, "
                "open, high, low, close, volume, real_volume, delta) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (sym, "br", "M5", ts, c, c, c, c, 100.0, 100.0, 0.0),
            )
    conn.commit()
    conn.close()
    migrate_divergence_config(db_path)


# ── 1. β OLS rolling ─────────────────────────────────────────────────────

def test_beta_ols_sem_intercepto():
    # ret_t = 2*ret_f exatamente -> β = 2.0
    rt = [0.02, 0.04, 0.06]
    rf = [0.01, 0.02, 0.03]
    assert abs(pf._rolling_ols_beta(rt, rf) - 2.0) < 1e-12


def test_beta_zero_com_menos_de_2_pontos_ou_denominador_nulo():
    assert pf._rolling_ols_beta([0.01], [0.01]) == 0.0        # <2 pontos
    assert pf._rolling_ols_beta([0.01, 0.02], [0.0, 0.0]) == 0.0  # Σf²=0


# ── 2. build_fixed_pair_snapshots (alinhamento + par fixo + marker) ──────

def test_snapshots_alinham_win_wdo_e_estampam_marker():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    # WIN sobe forte, WDO fica de lado -> resíduo positivo cresce -> em algum
    # ponto z_pair >= threshold -> marker de VENDA (target caro vs hedge).
    n = PAIR_SIGMA_WINDOW + 15
    win = [100.0 + (2.0 * i if i > n // 2 else 0.1 * i) for i in range(n)]
    wdo = [50.0 + 0.01 * i for i in range(n)]  # WDO quase parado
    _seed_win_wdo(db, win, wdo)
    conn = pf.readonly_connection(db)
    try:
        snaps = pf.build_fixed_pair_snapshots(conn, "2026-07-10", "WIN$N")
    finally:
        conn.close()
    assert len(snaps) == n, "deve haver 1 snapshot por barra alinhada"
    # OHLC do TARGET (WIN) preenchido pra a metodologia de entrada/MFE-MAE
    assert all(s.win_bar_open is not None and s.win_high is not None for s in snaps)
    assert all(not s.is_ghost for s in snaps)
    # Ao menos um marker discreto disparou (venda, pela distorção construída)
    vendas = [i for i, s in enumerate(snaps) if s.pair_fixed_venda is not None]
    assert vendas, "a distorção WIN-caro deveria disparar ao menos 1 venda"
    # pair_factor identifica o par fixo (wdo p/ target WIN)
    assert snaps[0].pair_factor == "wdo"


def test_marker_e_edge_triggered_nao_spammado():
    """O marker só nasce na TRANSIÇÃO do sinal, não em toda barra em que |z|
    segue acima do threshold. WDO precisa se MOVER (senão β OLS=0 -> neutral,
    comportamento herdado de zscore.py: sem hedge não há par)."""
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    n = PAIR_SIGMA_WINDOW + 20
    wdo = [50.0 + 0.5 * i for i in range(n)]                  # sobe de forma constante
    # WIN acompanha WDO na 1ª metade, depois dispara sozinho (distorção).
    win = [100.0 + (1.0 * i if i <= n // 2 else 1.0 * (n // 2) + 5.0 * (i - n // 2))
           for i in range(n)]
    _seed_win_wdo(db, win, wdo)
    conn = pf.readonly_connection(db)
    try:
        snaps = pf.build_fixed_pair_snapshots(conn, "2026-07-10", "WIN$N")
    finally:
        conn.close()
    markers = [i for i, s in enumerate(snaps)
               if s.pair_fixed_compra is not None or s.pair_fixed_venda is not None]
    # Distorção monotônica: no máximo poucas transições, não uma por barra.
    assert 0 < len(markers) <= 3, f"esperado poucas transições, veio {len(markers)}"


def test_timestamp_deslocado_para_eixo_tickmill():
    """O timestamp do snapshot é deslocado +offset sazonal (verão=6h) — a
    barra B3 às 12:00 vira 18:00 no eixo Tickmill."""
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    _seed_win_wdo(db, [100.0] * 3, [50.0] * 3, start_hour=12)
    conn = pf.readonly_connection(db)
    try:
        snaps = pf.build_fixed_pair_snapshots(conn, "2026-07-10", "WIN$N")
    finally:
        conn.close()
    # 2026-07-10 está no DST americano -> offset 6h -> 12:00 B3 = 18:00 Tickmill
    assert snaps[0].timestamp.endswith("T18:00:00")


def test_sessao_sem_barras_comuns_retorna_vazio():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    _seed_win_wdo(db, [100.0, 101.0], [50.0, 50.0])
    conn = pf.readonly_connection(db)
    try:
        # target existe mas não há WDO nessa OUTRA data -> sem barras comuns
        snaps = pf.build_fixed_pair_snapshots(conn, "2026-07-11", "WIN$N")
    finally:
        conn.close()
    assert snaps == []


def test_anti_lookahead_prefixo_identico_ao_completo():
    """Prova DIRETA da causalidade (achado do /fable-reasoner): os markers/
    β/z das primeiras K barras têm que ser IDÊNTICOS quer a sessão termine
    ali (prefixo) quer continue (completa). Se o cálculo usasse qualquer dado
    futuro, o prefixo divergiria."""
    db_full = os.path.join(tempfile.mkdtemp(), "full.db")
    db_pre = os.path.join(tempfile.mkdtemp(), "pre.db")
    n = PAIR_SIGMA_WINDOW + 25
    wdo = [50.0 + 0.5 * i for i in range(n)]
    win = [100.0 + (1.0 * i if i <= n // 2 else 1.0 * (n // 2) + 5.0 * (i - n // 2))
           for i in range(n)]
    K = n - 8  # prefixo trunca as últimas 8 barras
    _seed_win_wdo(db_full, win, wdo)
    _seed_win_wdo(db_pre, win[:K], wdo[:K])

    cf = pf.readonly_connection(db_full)
    cp = pf.readonly_connection(db_pre)
    try:
        full = pf.build_fixed_pair_snapshots(cf, "2026-07-10", "WIN$N")
        pre = pf.build_fixed_pair_snapshots(cp, "2026-07-10", "WIN$N")
    finally:
        cf.close(); cp.close()
    assert len(pre) == K
    for a, b in zip(full[:K], pre):
        # markers idênticos barra a barra — nenhum vazamento do futuro
        assert a.pair_fixed_compra == b.pair_fixed_compra
        assert a.pair_fixed_venda == b.pair_fixed_venda
        assert a.win_current == b.win_current


def test_isolamento_entre_sessoes_nao_vaza_residuo():
    """O histórico de resíduos é local por sessão — a sessão 2 recomeça do
    zero, sem herdar β/resíduo da 1 (achado do /fable-reasoner)."""
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    n = PAIR_SIGMA_WINDOW + 10
    wdo = [50.0 + 0.5 * i for i in range(n)]
    win = [100.0 + 5.0 * i for i in range(n)]  # sessão 1 muito distorcida
    _seed_win_wdo(db, win, wdo, session="2026-07-10")
    # sessão 2: preço plano -> se herdasse resíduo da 1, dispararia; não deve.
    _seed_win_wdo(db, [100.0] * n, [50.0 + 0.5 * i for i in range(n)], session="2026-07-13")
    conn = pf.readonly_connection(db)
    try:
        s2 = pf.build_fixed_pair_snapshots(conn, "2026-07-13", "WIN$N")
    finally:
        conn.close()
    # 1ª barra da sessão 2: sem histórico herdado -> z=0/neutral -> sem marker
    assert s2[0].pair_fixed_compra is None and s2[0].pair_fixed_venda is None


def test_offset_de_inverno_desloca_5h():
    """Fora do DST americano (janeiro) o offset é 5h: 12:00 B3 -> 17:00
    Tickmill (o caminho de verão/6h já é coberto em outro teste)."""
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    _seed_win_wdo(db, [100.0] * 3, [50.0] * 3, session="2026-01-15", start_hour=12)
    conn = pf.readonly_connection(db)
    try:
        snaps = pf.build_fixed_pair_snapshots(conn, "2026-01-15", "WIN$N")
    finally:
        conn.close()
    assert snaps[0].timestamp.endswith("T17:00:00")


def test_data_quality_conta_barras_e_descartes():
    """O diagnóstico de alinhamento (achado do /fable-reasoner) conta barras
    de WIN/WDO e comuns. Com grade IDÊNTICA, common == target == factor."""
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    n = PAIR_SIGMA_WINDOW + 5
    _seed_win_wdo(db, [100.0 + i for i in range(n)], [50.0 + 0.5 * i for i in range(n)])
    q = {}
    conn = pf.readonly_connection(db)
    try:
        pf.build_fixed_pair_snapshots(conn, "2026-07-10", "WIN$N", quality=q)
    finally:
        conn.close()
    assert q["sessions"] == 1
    assert q["target_bars"] == n and q["factor_bars"] == n
    assert q["common_bars"] == n  # grade idêntica -> nada descartado


# ── 3. _pair_fixed_direction ─────────────────────────────────────────────

def test_direction_le_campos_estampados():
    class _S:
        pair_fixed_compra = 100.0
        pair_fixed_venda = None
    assert pf._pair_fixed_direction(_S()) == "buy"
    _S.pair_fixed_compra, _S.pair_fixed_venda = None, 100.0
    assert pf._pair_fixed_direction(_S()) == "sell"
    _S.pair_fixed_venda = None
    assert pf._pair_fixed_direction(_S()) is None


# ── 4. Integração via run_fixed (replay real do market_bars) ─────────────

def test_run_fixed_produz_eventos_e_serializa():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    n = PAIR_SIGMA_WINDOW + 30
    wdo = [50.0 + 0.5 * i for i in range(n)]  # WDO se move (β OLS != 0)
    # WIN acompanha, depois dispara sozinho (distorção -> venda)
    win = [100.0 + (1.0 * i if i <= n // 2 else 1.0 * (n // 2) + 5.0 * (i - n // 2))
           for i in range(n)]
    _seed_win_wdo(db, win, wdo)

    # candidate_sessions descartaria a única sessão (mais recente = parcial);
    # aqui interessa o caminho real do replay do market_bars -> extract ->
    # agregação, então fixamos a lista de sessões (padrão dos outros testes).
    class _Cands:
        dates = ["2026-07-10"]
        discarded = []

    with patch.object(pf.psv, "candidate_sessions", lambda db, target, limit: _Cands):
        report = pf.run_fixed(db, ["WIN$N"], limit=10, bootstrap=100)
    assert report["challenger"] == "pair_fixo_win_wdo"
    t = report["targets"]["WIN$N"]
    # eventos serializados (com os 4 timestamps causais) via emit_events
    events = t["by_direction"]["all"].get("n_events", 0)
    assert events >= 1
    assert "events" in t and t["events"], "run(emit_events=True) deve serializar eventos"
    ev = t["events"][0]
    for k in ("observation_bar_end", "confirmation_bar_end", "signal_available_at",
              "entry_at", "entry_price"):
        assert k in ev, f"evento sem {k}"
    # causalidade: sinal disponível nunca depois da entrada
    assert ev["signal_available_at"] <= ev["entry_at"]


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
