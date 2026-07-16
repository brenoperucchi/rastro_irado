"""Spec do construtor de artefato NF-01 (backlog IRAI-2, item 4).

Testa a MONTAGEM do artefato (metadata, estrutura, políticas provisórias,
escolha de limitações por modo) com `run_fn`/`pit_builder` injetados — sem
depender de sklearn/pykalman nem de banco real. A metodologia de medição em
si já tem cobertura nos testes dos 5 módulos de sinal.

Roda sem pytest:  python3 tests/test_build_nf01_artifact.py
Ou com pytest:    pytest tests/test_build_nf01_artifact.py
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

import scripts.build_nf01_artifact as art
import scripts.measure_pair_signal_value as pair


def _fake_run(db_path, targets, limit, bootstrap, burn_in_sessions, *,
              direction_of=None, preprocess=None, limitations=None,
              pit_schedule=None, emit_events=False):
    """Devolve um report mínimo compatível, ecoando o que recebeu pra os
    testes poderem inspecionar o que build_artifact passou a cada sinal."""
    return {
        "targets": {
            t: {"events": [{"session_date": "2026-07-10"}] if emit_events else [],
                "by_direction": {"all": {"n_events": 1}}}
            for t in targets
        },
        "limitations": limitations,
        "_echo": {"pit_schedule": pit_schedule, "has_preprocess": preprocess is not None,
                  "direction_of": getattr(direction_of, "__name__", None)},
    }


def _build(point_in_time):
    return art.build_artifact(
        "unused.db", ["WIN$N", "WDO$N"], limit=10, bootstrap=50,
        burn_in_sessions=0, point_in_time=point_in_time,
        command="python3 -X utf8 scripts/build_nf01_artifact.py --output x.json",
        generated_at="2026-07-16T00:00:00+00:00",
        pit_builder=lambda db, targets: ("SCHEDULE", tuple(targets)),
        run_fn=_fake_run,
    )


def test_artefato_tem_os_5_sinais():
    a = _build(point_in_time=False)
    assert set(a["signals"]) == {
        "pair", "z", "intersection", "baseline_momentum", "baseline_reversao"}


def test_metadata_reprodutivel_presente():
    a = _build(point_in_time=True)
    assert a["schema_version"] == art.ARTIFACT_SCHEMA_VERSION
    assert "command" in a and a["command"].startswith("python3")
    assert "git" in a  # commit pode ser None em ambiente sem git, mas a chave existe
    assert a["generated_at"] == "2026-07-16T00:00:00+00:00"
    assert a["parameters"]["point_in_time"] is True
    assert a["parameters"]["targets"] == ["WIN$N", "WDO$N"]


def test_git_state_registra_head_e_origin_main():
    """_git_state deve expor tanto o HEAD do host quanto origin/main (o
    localizável) e se o HEAD já está publicado — corrige o achado de que o
    host de execução pode ter commits locais por cima."""
    g = art._git_state()
    # Este próprio repo tem git; as chaves devem existir (valores podem variar).
    assert "commit" in g
    assert "origin_main" in g
    assert "head_in_origin_main" in g
    assert "dirty" in g


def test_politicas_provisorias_documentadas():
    a = _build(point_in_time=False)
    pol = a["provisional_policies"]
    assert "HIPOTÉTICO" in pol["entry_price"]
    assert "intrabar" in pol["mfe_mae"]
    assert "IRAI-4/VAL-04" in pol["costs"]
    assert "confirmatório" in pol["significance"]


def test_eventos_incluidos_por_sinal():
    a = _build(point_in_time=False)
    for name, sig in a["signals"].items():
        for t, tr in sig["targets"].items():
            assert "events" in tr, f"{name}/{t} sem events"
            assert tr["events"], f"{name}/{t} events vazio (emit_events não propagou)"


def test_pit_schedule_construido_uma_vez_e_repassado_a_todos():
    a = _build(point_in_time=True)
    for name, sig in a["signals"].items():
        assert sig["_echo"]["pit_schedule"] == ("SCHEDULE", ("WIN$N", "WDO$N")), (
            f"{name} não recebeu o mesmo pit_schedule")


def test_modo_retrospectivo_nao_constroi_schedule():
    a = _build(point_in_time=False)
    for name, sig in a["signals"].items():
        assert sig["_echo"]["pit_schedule"] is None


def test_cada_sinal_recebe_seu_direction_e_preprocess():
    a = _build(point_in_time=False)
    echo = {name: sig["_echo"] for name, sig in a["signals"].items()}
    # pair: default (None) -> sem preprocess; interseção e baselines usam preprocess
    assert echo["pair"]["has_preprocess"] is False
    assert echo["z"]["has_preprocess"] is False
    assert echo["intersection"]["has_preprocess"] is True
    assert echo["baseline_momentum"]["has_preprocess"] is True
    assert echo["baseline_reversao"]["has_preprocess"] is True
    assert echo["z"]["direction_of"] == "_divergence_direction"
    assert echo["intersection"]["direction_of"] == "_intersection_direction"


def test_limitacoes_diferem_entre_modo_retro_e_pit():
    retro = _build(point_in_time=False)
    pit = _build(point_in_time=True)
    # No modo PIT, a ressalva de cesta substituta (POINT_IN_TIME_LIMITATIONS)
    # deve aparecer nas limitações do Pair; no retrospectivo, não.
    pit_pair_lims = pit["signals"]["pair"]["limitations"]
    retro_pair_lims = retro["signals"]["pair"]["limitations"]
    assert any("cesta de fatores é FIXA" in x for x in pit_pair_lims)
    assert not any("cesta de fatores é FIXA" in x for x in retro_pair_lims)


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
