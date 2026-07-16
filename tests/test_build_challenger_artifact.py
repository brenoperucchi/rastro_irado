"""Spec do construtor de artefato do challenger Pair fixo (IRAI-21).

Testa a MONTAGEM do artefato e a comparação (bruta + frequência equivalente)
com run_fixed/reference injetados — sem sklearn/pykalman nem banco real.

Roda sem pytest:  python3 tests/test_build_challenger_artifact.py
Ou com pytest:    pytest tests/test_build_challenger_artifact.py
"""
import os
import sys
import types

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

import scripts.build_challenger_artifact as bca


def _fake_target_report(mean, events, sessions_replayed, burn_in=0):
    """target_report mínimo com um único estimate por horizonte."""
    horizons = {}
    for h in (3, 6, 10, 20):
        horizons[str(h)] = {
            "estimate": {"value": mean, "ci_low": mean - 1, "ci_high": mean + 1,
                         "n_sessions": events, "n_events": events, "significant": False,
                         "standard_error": 1.0},
            "win_rate_pct": 50.0, "wins": events // 2, "total": events,
        }
    return {
        "sessions_replayed": sessions_replayed, "sessions_burn_in": burn_in,
        "sessions_before_first_pit_cutoff": 0, "gate_verdict": "AMOSTRA_SUFICIENTE_PARA_GATE",
        "by_direction": {"all": {"n_events": events, "horizons": horizons}},
    }


def _fake_challenger_target():
    """Challenger target com eventos serializados: metade ANTES do cutoff
    (2022-06), metade DEPOIS (2023-06) — pra a filtragem windowed ter o que
    recortar. fwd fixo -2.0 em todos os horizontes."""
    tr = _fake_target_report(mean=-2.0, events=1000, sessions_replayed=1250)
    fwd = {"3": -2.0, "6": -2.0, "10": -2.0, "20": -2.0}
    events = ([{"session_date": "2022-06-15", "fwd": dict(fwd)} for _ in range(400)]
              + [{"session_date": "2023-06-15", "fwd": dict(fwd)} for _ in range(600)])
    tr["events"] = events
    return tr


def _fake_run_fixed(db_path, targets, limit, bootstrap):
    return {"challenger": "pair_fixo_win_wdo",
            "targets": {t: _fake_challenger_target() for t in targets}}


def _fake_reference():
    def _pair_tr(mean, events):
        tr = _fake_target_report(mean=mean, events=events, sessions_replayed=1250, burn_in=5)
        tr["sessions_before_first_pit_cutoff"] = 370  # PIT: mede ~875 sessões
        tr["pit_cutoffs_used"] = ["2022-12-30", "2023-03-31"]
        return tr
    return {
        "generated_at": "2026-07-16T00:00:00+00:00",
        "git": {"commit": "abc123"},
        "parameters": {"point_in_time": True},
        "signals": {
            "pair": {"targets": {t: _pair_tr(-1.0, 3000) for t in ("WIN$N", "WDO$N")}},
            "baseline_momentum": {"targets": {t: _pair_tr(-0.5, 2500) for t in ("WIN$N", "WDO$N")}},
            "baseline_reversao": {"targets": {t: _pair_tr(0.5, 2500) for t in ("WIN$N", "WDO$N")}},
        },
    }


def _build(with_reference=True):
    return bca.build_artifact(
        "unused.db", ["WIN$N", "WDO$N"], limit=2000, bootstrap=100,
        dynamic_summary="ref.json" if with_reference else None,
        command="python3 -X utf8 scripts/build_challenger_artifact.py --output x.json",
        generated_at="2026-07-16T00:00:00+00:00",
        run_fixed_fn=_fake_run_fixed,
        reference_loader=lambda p: _fake_reference(),
    )


def test_metadata_e_metodologia_presentes():
    a = _build()
    assert a["schema_version"] == bca.ARTIFACT_SCHEMA_VERSION
    assert a["artifact"] == "challenger-pair-fixo-win-wdo"
    assert "congelada" in a["methodology"]
    assert a["command"].startswith("python3")
    assert a["parameters"]["independent_of_calibration"] is True


def test_comparacao_tem_challenger_sinais_e_janela_alinhada():
    a = _build()
    comp = a["comparison"]["WIN$N"]
    assert set(comp) == {"pair_fixo", "pair", "baseline_momentum",
                         "baseline_reversao", "pair_fixo_windowed"}


def test_expectativa_por_sessao_normaliza_frequencia():
    """expectancy_per_session = mean_per_event × events/sessions_measured.
    Challenger: mean=-2.0, 1000 eventos, 1250 sessões medidas (burn_in=0)."""
    a = _build()
    ch = a["comparison"]["WIN$N"]["pair_fixo"]["horizons"]["6"]
    assert ch["mean_per_event"] == -2.0
    assert ch["events_per_session"] == round(1000 / 1250, 4)
    assert ch["expectancy_per_session"] == round(-2.0 * (1000 / 1250), 4)
    # Pair dinâmico: mean=-1.0, 3000 eventos, 875 medidas (1250-5-370 pre_cutoff)
    dyn = a["comparison"]["WIN$N"]["pair"]["horizons"]["6"]
    assert dyn["events_per_session"] == round(3000 / 875, 4)


def test_janela_alinhada_recorta_challenger_no_cutoff():
    """pair_fixo_windowed recorta o challenger em session_date > 1º cutoff
    (2022-12-30): dos 1000 eventos (400 em 2022-06, 600 em 2023-06), só os
    600 pós-cutoff entram, e o mean/evento continua -2.0 (fwd fixo)."""
    a = _build()
    win = a["comparison"]["WIN$N"]["pair_fixo_windowed"]
    assert win["n_events_window"] == 600
    h6 = win["horizons"]["6"]
    assert h6["mean_per_event"] == -2.0
    assert h6["events"] == 600
    # denominador = sessões medidas do dinâmico (mesma janela), não 1250
    assert win["sessions_measured"] == 875
    assert h6["events_per_session"] == round(600 / 875, 4)
    assert "session_date > 2022-12-30" in win["note"]


def test_reference_meta_registra_ressalva_de_janela():
    a = _build()
    assert a["reference"]["point_in_time"] is True
    assert "janela" in a["reference"]["note"]
    assert a["reference"]["git_commit"] == "abc123"


def test_sem_referencia_omite_comparacao_de_sinais():
    a = _build(with_reference=False)
    assert a["reference"] is None
    comp = a["comparison"]["WIN$N"]
    assert set(comp) == {"pair_fixo"}  # só o challenger


def test_sessions_measured_desconta_burn_in_e_pre_cutoff():
    tr = _fake_target_report(mean=-1.0, events=100, sessions_replayed=1250, burn_in=5)
    tr["sessions_before_first_pit_cutoff"] = 369
    assert bca._sessions_measured(tr) == 1250 - 5 - 369


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
