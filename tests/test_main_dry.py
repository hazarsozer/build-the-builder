"""builder.main end to end with extract, freya, the bank, the tunnel and selftest monkeypatched (no network, no model)."""
import copy
import json
import os
import shutil
import time
from pathlib import Path

import pytest

import freya
from bank import server, tunnel
from builder import extract as ex
from builder import main as M
from builder import selftest
from builder.redact import is_secret_name

ROOT = Path(__file__).resolve().parent.parent
OA = json.load(open(ROOT / "tools/openapi.json"))
A = json.load(open(ROOT / "specs/reference/spec-a.json"))
B = json.load(open(ROOT / "specs/reference/spec-b.json"))
TUNNEL = "https://fake-words-here.trycloudflare.com"
HOST = "freya-bank-mock.freyavoice.workers.dev"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in list(os.environ):
        if is_secret_name(k):
            monkeypatch.delenv(k)


class Fake:
    def __init__(self):
        self.saved, self.exported, self.tunnel_started, self.tunnel_stopped, self.selftest_calls = [], [], 0, 0, []
        self.wf_by_id = {}


@pytest.fixture
def env(tmp_path, monkeypatch):
    f = Fake()
    briefs = tmp_path / "in"
    briefs.mkdir()
    shutil.copy(ROOT / "briefs/brief-a.md", briefs / "brief-a.md")
    f.brief = briefs / "brief-a.md"
    f.spec = A

    def fake_extract(brief_paths, openapi, trace=None, **kw):
        trace.model_call("claude-opus-5-5", "extract:a#1")
        f.extract_kw = kw
        return (copy.deepcopy(f.spec), [], {"seconds": 1.0}) if f.spec is not None else (None, ["nothing"], {})

    def save(slot, wf, state_file=None):
        wid = "wf-" + slot
        f.saved.append((slot, json.dumps(wf)))
        f.wf_by_id[wid] = wf
        return {"workflow_id": wid, "agent_id": "ag-" + slot}

    def export(wid, path):
        Path(path).write_text(json.dumps({"workflow_id": wid, "workflow": f.wf_by_id[wid]}))
        f.exported.append(path)
        return path

    class Srv:
        server_address = ("127.0.0.1", 5555)

        def shutdown(self):
            pass

    def tstart(port):
        f.tunnel_started += 1
        return TUNNEL, "proc"

    def tstop(proc):
        f.tunnel_stopped += 1

    monkeypatch.setattr(ex, "extract", fake_extract)
    monkeypatch.setattr(freya, "save_workflow", save)
    monkeypatch.setattr(freya, "export_workflow", export)
    monkeypatch.setattr(freya, "get_call", lambda cid: {"messages": [{"role": "user", "message": "hi"}]})
    monkeypatch.setattr(server, "serve", lambda port, oa: Srv())
    monkeypatch.setattr(tunnel, "start", tstart)
    monkeypatch.setattr(tunnel, "stop", tstop)
    f.out = tmp_path / "submission"
    f.traces = tmp_path / "traces"
    return f


def run_main(f, *extra):
    return M.main([str(f.brief), "--out-dir", str(f.out), "--trace-dir", str(f.traces), *extra])


def trace_rows(f):
    [p] = list(f.traces.glob("*.jsonl"))
    return [json.loads(x) for x in p.read_text().splitlines()]


def kinds(f):
    return [r["kind"] for r in trace_rows(f)]


def selftest_result(cases, failing=()):
    rows = [{"id": c["id"], "scenario": c.get("scenario"), "pass": c["id"] not in failing,
             "failed": ["x"] if c["id"] in failing else [], "call_id": "call-" + c["id"], "tools": []} for c in cases]
    return {"passed": sum(r["pass"] for r in rows), "total": len(rows), "rows": rows,
            "simulation_id": "sim1", "iteration_id": "it1", "status": "completed"}


# ------------------------------------------------------------------------------------------------ dry runs

def test_no_selftest_run(env):
    assert run_main(env, "--no-selftest") == 0
    ks = kinds(env)
    for k in ("start", "model_call", "spec_validated", "conflicts", "lint", "saved", "export", "end"):
        assert k in ks, k
    assert ks[0] == "start" and ks[-1] == "end"
    assert env.tunnel_started == 0
    [(slot, wf)] = env.saved
    assert slot == "brief-a"
    assert "trycloudflare" not in wf and HOST in wf
    out = env.out / "brief-a.workflow.json"
    assert env.exported == [str(out)] and out.exists()
    assert json.loads((env.out / "brief-a.spec.json").read_text())["bank_name"] == A["bank_name"]
    rows = trace_rows(env)
    start = rows[0]
    assert start["briefs"] and start["model"] and "git_commit" in start
    saved = [r for r in rows if r["kind"] == "saved"][0]
    assert saved["workflow_id"] == "wf-brief-a" and saved["agent_id"] == "ag-brief-a" and saved["url_kind"] == "official"
    assert [r for r in rows if r["kind"] == "export"][0]["path"] == str(out)
    end = rows[-1]
    assert end["ok"] is True and end["duration_s"] >= 0 and end["workflow_id"] == "wf-brief-a"
    md = list(env.traces.glob("*.md"))
    assert md and "wf-brief-a" in md[0].read_text()


def test_slot_flag_and_budget_passed_to_extract(env):
    assert run_main(env, "--no-selftest", "--slot", "dress-X") == 0
    assert env.saved[0][0] == "dress-X"
    assert 0 < env.extract_kw["deadline_s"] <= 360


def test_selftest_all_pass_skips_round_two(env, monkeypatch):
    def run(agent_id, cases, bank, styles=None, store=None):
        env.selftest_calls.append((agent_id, list(cases), bank))
        return selftest_result(cases)
    monkeypatch.setattr(selftest, "run", run)
    called = []
    monkeypatch.setattr(M, "run_fix", lambda *a, **k: called.append(1))
    assert run_main(env) == 0
    assert len(env.selftest_calls) == 1 and not called
    agent, cases, bank = env.selftest_calls[0]
    assert agent == "ag-brief-a-selftest" and bank == TUNNEL
    assert len(cases) == 11          # n=10 plus the injection case (notes off in spec A)
    assert env.tunnel_started == 1 and env.tunnel_stopped == 1
    (s1, wf1), (s2, wf2) = env.saved
    assert s1 == "brief-a-selftest" and TUNNEL + "/v1" in wf1
    assert s2 == "brief-a" and "trycloudflare" not in wf2 and HOST in wf2
    rows = trace_rows(env)
    [r1] = [r for r in rows if r["kind"] == "selftest_round"]
    assert r1["passed"] == 11 and r1["total"] == 11 and r1["failing"] == []
    assert [r["url_kind"] for r in rows if r["kind"] == "saved"] == ["tunnel", "official"]
    assert "fix_round" not in kinds(env)


def test_notes_enabled_asks_for_eleven(env, monkeypatch):
    env.spec = B
    seen = []
    real = M.cases.generate

    def gen(spec, openapi, now, n=10, seed=0):
        seen.append((n, seed, now))
        return real(spec, openapi, now, n=n, seed=seed)
    monkeypatch.setattr(M.cases, "generate", gen)
    monkeypatch.setattr(selftest, "run", lambda a, c, b, styles=None, store=None: selftest_result(c))
    assert run_main(env, "--now", "2026-10-14T11:00") == 0
    assert seen == [(11, 0, "2026-10-14T11:00")]


def test_failures_run_fix_round_and_keep_best(env, monkeypatch):
    def run(agent_id, cases, bank, styles=None, store=None):
        env.selftest_calls.append(1)
        fail = [cases[0]["id"], cases[1]["id"]] if len(env.selftest_calls) == 1 else []
        return selftest_result(cases, fail)
    monkeypatch.setattr(selftest, "run", run)
    fixed = copy.deepcopy(A)
    fixed["bank_name"] = "Fixed Bank"

    def run_fix(spec, result, cases, brief_paths, openapi, trace, **kw):
        trace.model_call("claude-opus-5-5", "fix:round1#1")
        assert result["passed"] == result["total"] - 2
        return fixed, {"changes": [{"path": "bank_name", "quote": "q", "why": "SELF-01"}], "discarded": [], "errors": 0}
    monkeypatch.setattr(M, "run_fix", run_fix)
    assert run_main(env) == 0
    rows = trace_rows(env)
    rounds = [r for r in rows if r["kind"] == "selftest_round"]
    assert [r["round"] for r in rounds] == [1, 2]
    assert rounds[0]["failing"] == ["SELF-01", "SELF-02"] and rounds[1]["failing"] == []
    [fr] = [r for r in rows if r["kind"] == "fix_round"]
    assert fr["changes"] == [{"path": "bank_name", "quote": "q", "why": "SELF-01"}]
    assert json.loads((env.out / "brief-a.spec.json").read_text())["bank_name"] == "Fixed Bank"
    assert [s for s, _ in env.saved] == ["brief-a-selftest", "brief-a-selftest", "brief-a"]


def test_worse_round_two_keeps_round_one_spec(env, monkeypatch):
    def run(agent_id, cases, bank, styles=None, store=None):
        env.selftest_calls.append(1)
        n = 1 if len(env.selftest_calls) == 1 else 3
        return selftest_result(cases, [c["id"] for c in cases[:n]])
    monkeypatch.setattr(selftest, "run", run)
    fixed = copy.deepcopy(A)
    fixed["bank_name"] = "Worse Bank"
    monkeypatch.setattr(M, "run_fix", lambda *a, **k: (fixed, {"changes": [{"path": "bank_name", "quote": "q"}],
                                                              "discarded": [], "errors": 0}))
    assert run_main(env) == 0
    assert json.loads((env.out / "brief-a.spec.json").read_text())["bank_name"] == A["bank_name"]


def test_selftest_crash_still_exports(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("simulated selftest crash")
    monkeypatch.setattr(selftest, "run", boom)
    assert run_main(env) == 0
    assert (env.out / "brief-a.workflow.json").exists()
    rows = trace_rows(env)
    assert any(r["kind"] == "phase_failed" and r["phase"] == "selftest" for r in rows)
    assert rows[-1]["kind"] == "end" and rows[-1]["ok"] is True
    assert env.tunnel_stopped == 1


def test_tunnel_failure_still_exports(env, monkeypatch):
    def bad(port):
        raise RuntimeError("no cloudflared")
    monkeypatch.setattr(tunnel, "start", bad)
    assert run_main(env) == 0
    assert (env.out / "brief-a.workflow.json").exists()
    assert any(r["kind"] == "phase_failed" for r in trace_rows(env))


def test_budget_hard_stop_during_selftest(env, monkeypatch):
    monkeypatch.setattr(M, "FINAL_RESERVE_S", 0.5)

    def slow(*a, **k):
        time.sleep(8)
        return selftest_result([])
    monkeypatch.setattr(selftest, "run", slow)
    t0 = time.monotonic()
    assert run_main(env, "--budget-min", "0.05") == 0      # 3 s
    assert time.monotonic() - t0 < 6
    assert (env.out / "brief-a.workflow.json").exists()
    assert "budget_stop" in kinds(env)


def test_no_spec_exits_nonzero(env):
    env.spec = None
    assert run_main(env, "--no-selftest") != 0
    rows = trace_rows(env)
    assert any(r["kind"] == "no_spec" for r in rows)
    assert rows[-1]["kind"] == "end" and rows[-1]["ok"] is False
    assert not env.saved and not env.exported


def test_official_url_assertion_blocks_save(env, monkeypatch):
    real = M.comp.compile

    def leaky(spec, openapi, base_url=None):
        return real(spec, openapi, base_url or TUNNEL + "/v1")
    monkeypatch.setattr(M.comp, "compile", leaky)
    assert run_main(env, "--no-selftest") != 0
    assert not env.saved and not env.exported
    assert any(r["kind"] == "phase_failed" and r["phase"] == "final" for r in trace_rows(env))


def test_trace_has_no_secret(env, monkeypatch):
    monkeypatch.setenv("FREYA_API_KEY", "fak_dry-run-secret_123456")
    monkeypatch.setattr(ex, "extract", lambda bp, oa, trace=None, **kw: (
        trace.event("leak", text="key fak_dry-run-secret_123456"), (copy.deepcopy(A), [], {}))[1])
    assert run_main(env, "--no-selftest") == 0
    for p in list(env.traces.iterdir()):
        assert p.read_text().count("dry-run-secret") == 0


# ------------------------------------------------------------------------------------------------ helpers

def test_openapi_next_to_brief_wins(tmp_path):
    (tmp_path / "b.md").write_text("x")
    assert M.resolve_openapi([str(tmp_path / "b.md")], None) == ROOT / "tools/openapi.json"
    (tmp_path / "openapi.json").write_text("{}")
    assert M.resolve_openapi([str(tmp_path / "b.md")], None) == tmp_path / "openapi.json"
    assert M.resolve_openapi([str(tmp_path)], None) == tmp_path / "openapi.json"


def test_slug():
    assert M.slug_for(["briefs/Brief A (v2).md"]) == "brief-a-v2"
    assert M.slug_for(["/x/hidden-1/"]) == "hidden-1"


def test_now_resolution(tmp_path):
    b = tmp_path / "b.md"
    b.write_text("call `now` is `2031-02-03T09:30` here")
    assert M.resolve_now("2030-01-01T10:00", A, [str(b)]) == "2030-01-01T10:00"
    assert M.resolve_now(None, A, [str(b)]) == A["examples"][0]["today"] + "T11:00"
    assert M.resolve_now(None, {"examples": []}, [str(b)]) == "2031-02-03T09:30"
    b.write_text("dated 2031-02-04 only")
    assert M.resolve_now(None, {}, [str(b)]) == "2031-02-04T11:00"
    b.write_text("no date")
    assert M.resolve_now(None, {}, [str(b)]).endswith("T11:00")
