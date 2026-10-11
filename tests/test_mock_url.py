"""W2: --mock-url (the final export calls our own mock) and the organisers' run command builder/build.py."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

from builder import build as BD
from builder import main as M
from builder.compile import compile as C

from test_main_dry import A, B, HOST, OA, ROOT, env, kinds, trace_rows, clean_env  # noqa: F401 - fixtures

MOCK = "https://our-mock.example-team.workers.dev"
MOCK_HOST = "our-mock.example-team.workers.dev"
URL_RE = re.compile(r"https?://[^\s\"'\\)]+")


def urls(wf):
    """Every URL in the workflow except the compiler's fail-closed sentinels on the reserved .invalid TLD
    (compile.NOT_SENT_URL): those are never called, by design."""
    return {u for u in URL_RE.findall(json.dumps(wf)) if not urlparse(u).netloc.endswith(".invalid")}


# ------------------------------------------------------------------------------------------------ joining rule

@pytest.mark.parametrize("mock,want", [
    (MOCK, MOCK + "/v1"),
    (MOCK + "/", MOCK + "/v1"),
    (MOCK + "/v1", MOCK + "/v1"),
    (MOCK + "/v1/", MOCK + "/v1"),
    (MOCK + "/api", MOCK + "/api/v1"),
])
def test_mock_base_join(mock, want):
    assert M.mock_base("https://freya-bank-mock.freyavoice.workers.dev/v1", mock) == want


def test_mock_base_no_server_path():
    assert M.mock_base("https://bank.example", MOCK + "/") == MOCK
    assert M.mock_base("https://bank.example/", MOCK + "/x") == MOCK + "/x"


@pytest.mark.parametrize("bad", [
    "http://our-mock.workers.dev", "ftp://x.dev", "our-mock.workers.dev", "https://", "",
    "https://words-here.trycloudflare.com", "https://a.TryCloudflare.com/v1",
    "https://x.workers.dev/v1?x=1", "https://x.workers.dev/#f", "https://user:pw@x.workers.dev",
    "https://x.workers.dev/a b",
])
def test_check_mock_url_rejects(bad):
    with pytest.raises(ValueError):
        M.check_mock_url(bad)


def test_check_mock_url_accepts():
    assert M.check_mock_url(MOCK + "/") == MOCK
    assert M.check_mock_url(MOCK + "/v1") == MOCK + "/v1"


# ------------------------------------------------------------------------------------------------ compile

@pytest.mark.parametrize("spec", [A, B], ids=["spec-a", "spec-b"])
def test_compile_with_mock_puts_every_url_on_mock_host(spec):
    wf = C(spec, OA, M.mock_base(OA["servers"][0]["url"], MOCK))
    us = urls(wf)
    assert us, "no URL found in the workflow"
    assert {urlparse(u).netloc for u in us} == {MOCK_HOST}, sorted(us)[:5]
    assert all(urlparse(u).path.startswith("/v1/") for u in us)
    assert HOST not in json.dumps(wf)


# ------------------------------------------------------------------------------------------------ main

def run_main(f, *extra):
    return M.main([str(f.brief), "--out-dir", str(f.out), "--trace-dir", str(f.traces), "--no-selftest", *extra])


def test_main_without_flag_is_byte_identical(env):
    assert run_main(env) == 0
    [(slot, text)] = env.saved
    assert text == json.dumps(C(A, OA))
    start = trace_rows(env)[0]
    assert start["mock_url"] is None


def test_main_with_mock_url_exports_mock(env):
    assert run_main(env, "--mock-url", MOCK) == 0
    [(slot, text)] = env.saved
    assert text == json.dumps(C(A, OA, MOCK + "/v1"))
    assert HOST not in text and MOCK_HOST in text and "trycloudflare" not in text
    rows = trace_rows(env)
    assert rows[0]["mock_url"] == MOCK
    saved = [r for r in rows if r["kind"] == "saved"][0]
    assert saved["url_kind"] == "mock" and saved["base_url"] == MOCK + "/v1"
    exported = json.loads((env.out / (env.slot + ".workflow.json")).read_text())
    assert {urlparse(u).netloc for u in urls(exported["workflow"])} == {MOCK_HOST}


def test_main_rejects_bad_mock_url(env, capsys):
    with pytest.raises(SystemExit) as e:
        run_main(env, "--mock-url", "https://x.trycloudflare.com")
    assert e.value.code == 2
    assert env.saved == []


def test_selftest_still_uses_tunnel_with_mock_url(env, monkeypatch):
    from builder import selftest
    seen = []

    def fake_run(agent_id, cs, url, **kw):
        seen.append(url)
        rows = [{"id": c["id"], "scenario": c.get("scenario"), "pass": True, "failed": [], "tools": []} for c in cs]
        return {"passed": len(rows), "total": len(rows), "rows": rows, "simulation_id": "s", "status": "completed"}
    monkeypatch.setattr(selftest, "run", fake_run)
    rc = M.main([str(env.brief), "--out-dir", str(env.out), "--trace-dir", str(env.traces), "--mock-url", MOCK,
                 "--now", "2026-10-14T11:00"])
    assert rc == 0
    assert seen and all("trycloudflare" in u for u in seen)
    (s1, t1), (s2, t2) = env.saved
    assert s1.endswith("-selftest") and "trycloudflare" in t1 and MOCK_HOST not in t1
    assert s2 == env.slot and MOCK_HOST in t2 and "trycloudflare" not in t2


# ------------------------------------------------------------------------------------------------ build.py

def case(tmp_path, name="Case 1", openapis=("openapi.json",)):
    d = tmp_path / name
    d.mkdir()
    shutil.copy(ROOT / "briefs/brief-a.md", d / "brief.md")
    for n in openapis:
        shutil.copy(ROOT / "tools/openapi.json", d / n)
    return d


def test_build_slot_name(tmp_path):
    assert BD.slot_for(tmp_path / "Case 1") == "final-case-1"
    assert BD.slot_for(str(tmp_path / "brief_x") + "/") == "final-brief-x"
    assert BD.slot_for(tmp_path / "!!!") == "final-case"


def test_build_argv(tmp_path):
    d = case(tmp_path)
    argv = BD.main_argv([str(d), MOCK])
    assert argv[0] == str(d)
    assert argv[argv.index("--openapi") + 1] == str((d / "openapi.json").resolve())
    assert argv[argv.index("--slot") + 1] == "final-case-1"
    assert argv[argv.index("--mock-url") + 1] == MOCK


def test_build_finds_openapi_under_other_name(tmp_path):
    d = case(tmp_path, openapis=("bank-api.json",))
    argv = BD.main_argv([str(d), MOCK])
    assert argv[argv.index("--openapi") + 1] == str((d / "bank-api.json").resolve())


def test_build_falls_back_on_no_openapi_and_fails_on_several(tmp_path):
    argv = BD.main_argv([str(case(tmp_path, "none", ())), MOCK])   # D1: no contract file -> loud kit fallback
    assert "--openapi-fallback" in argv and "--openapi" not in argv and argv[argv.index("--mock-url") + 1] == MOCK
    with pytest.raises(SystemExit) as e:
        BD.main_argv([str(case(tmp_path, "two", ("a.json", "b.json"))), MOCK])
    assert e.value.code == 2


def test_build_rejects_bad_args(tmp_path):
    d = case(tmp_path)
    for bad in ([str(d)], [str(d), MOCK, "extra"], [str(tmp_path / "missing"), MOCK],
                [str(d), "http://x.workers.dev"], [str(d), "https://a.trycloudflare.com"]):
        with pytest.raises(SystemExit) as e:
            BD.main_argv(bad)
        assert e.value.code == 2, bad


def test_build_runs_main(tmp_path, monkeypatch):
    d = case(tmp_path)
    got = []
    monkeypatch.setattr(M, "main", lambda argv: got.append(argv) or 0)
    assert BD.run([str(d), MOCK]) == 0
    assert got == [BD.main_argv([str(d), MOCK])]


def test_build_script_runs_with_plain_python(tmp_path):
    """`python3 builder/build.py --help` from the repo root, with the interpreter of this test run: no import errors."""
    r = subprocess.run([sys.executable, "builder/build.py", "--help"], cwd=ROOT, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert "case_folder" in r.stdout and "mock_url" in r.stdout


def test_build_script_reexecs_into_venv(tmp_path):
    """A python without the dependencies re-execs into the repo venv (or uv run)."""
    r = subprocess.run([sys.executable, "-S", "-s", "builder/build.py", "--help"], cwd=ROOT, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "case_folder" in r.stdout
