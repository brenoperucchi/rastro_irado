"""Regressão: rastro-irado-gex.service falhou em 2026-07-17 09:10 BRT ao ler
o bundle B3 de 2026-07-16 como ZIP (`zipfile.BadZipFile: File is not a zip
file`), sem gravar snapshot WIN$N para aquela sessão.

Causa raiz confirmada: `download_b3_bundle` fazia uma única tentativa de
`urlopen` + abertura do ZIP externo, sem retry. Reproduzindo manualmente a
mesma query minutos depois, a B3 respondeu com um ZIP válido -- ou seja, a
falha foi transitória (payload não-ZIP num request isolado). O agravante:
`rastro-irado-gex.timer` roda uma vez por dia e busca sempre o D-1 mais
recente -- não há novo agendamento para o mesmo `source_session_date`, então
uma falha transitória nesse único disparo perde a janela do dia.

Estas specs travam:
  1. Falha transitória de transporte/formato (OSError, zipfile.BadZipFile) é
     retentada -- uma resposta ruim seguida de uma boa deve resultar em
     sucesso, não em falha permanente.
  2. Falha persistente (todas as tentativas ruins) continua falhando fechado
     depois de esgotar as tentativas -- não mascara um problema real de B3.
  3. Bundle logicamente incompleto (ZIP válido mas com arquivo faltando) NÃO
     é retentado -- não é falha transitória de transporte, é um problema de
     conteúdo; retry não ajudaria e só atrasaria o fail-closed.

Roda sem pytest:  python3 tests/test_gex_official.py
"""
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import gex_official as go

SESSION_DATE = "2026-07-16"
NAMES = go.expected_bundle_names(SESSION_DATE)


def _build_bundle_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in NAMES.values():
            zf.writestr(name, b"dummy-content")
    return buf.getvalue()


def _build_incomplete_bundle_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for kind, name in NAMES.items():
            if kind == "premiums":
                continue
            zf.writestr(name, b"dummy-content")
    return buf.getvalue()


class _FakeUrlopen:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return io.BytesIO(item)


def test_download_b3_bundle_retenta_apos_payload_transitorio_nao_zip():
    fake = _FakeUrlopen([b"<html>erro temporario da B3</html>", _build_bundle_zip()])
    original_urlopen = go.urllib.request.urlopen
    go.urllib.request.urlopen = fake
    try:
        with tempfile.TemporaryDirectory() as tmp:
            paths = go.download_b3_bundle(
                SESSION_DATE, Path(tmp), retry_delay=0,
            )
            for path in paths.values():
                assert path.is_file() and path.stat().st_size > 0, (
                    f"arquivo do bundle não foi gravado após o retry: {path}")
    finally:
        go.urllib.request.urlopen = original_urlopen

    assert fake.calls == 2, (
        "deveria ter tentado de novo após o payload não-ZIP -- "
        f"chamadas={fake.calls}")


def test_download_b3_bundle_falha_fechado_apos_esgotar_tentativas():
    fake = _FakeUrlopen([
        b"<html>erro 1</html>",
        b"<html>erro 2</html>",
    ])
    original_urlopen = go.urllib.request.urlopen
    go.urllib.request.urlopen = fake
    try:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                go.download_b3_bundle(
                    SESSION_DATE, Path(tmp),
                    max_attempts=2, retry_delay=0,
                )
                raise AssertionError(
                    "deveria falhar fechado após esgotar as tentativas")
            except zipfile.BadZipFile:
                pass
            assert not any(Path(tmp, SESSION_DATE).iterdir()), (
                "falha persistente não deveria deixar arquivo parcial em cache")
    finally:
        go.urllib.request.urlopen = original_urlopen

    assert fake.calls == 2, (
        f"deveria ter esgotado exatamente max_attempts tentativas: {fake.calls}")


def test_download_b3_bundle_incompleto_nao_e_retentado():
    fake = _FakeUrlopen([_build_incomplete_bundle_zip()])
    original_urlopen = go.urllib.request.urlopen
    go.urllib.request.urlopen = fake
    try:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                go.download_b3_bundle(
                    SESSION_DATE, Path(tmp),
                    max_attempts=3, retry_delay=0,
                )
                raise AssertionError(
                    "bundle sem um dos arquivos esperados deveria falhar")
            except ValueError as exc:
                assert "sem arquivos" in str(exc), exc
    finally:
        go.urllib.request.urlopen = original_urlopen

    assert fake.calls == 1, (
        "bundle logicamente incompleto não é falha transitória -- "
        f"não deveria retentar: chamadas={fake.calls}")


TESTS = [
    test_download_b3_bundle_retenta_apos_payload_transitorio_nao_zip,
    test_download_b3_bundle_falha_fechado_apos_esgotar_tentativas,
    test_download_b3_bundle_incompleto_nao_e_retentado,
]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(TESTS) - failures}/{len(TESTS)} passed")
    sys.exit(1 if failures else 0)
