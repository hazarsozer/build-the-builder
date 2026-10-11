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
    shutil.copy(ROOT / "tools/openapi.json", briefs / "openapi.json")
    f.brief = briefs / "brief-a.md"
    f.slot = M.slug_for([str(f.brief)])
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
    f.cancelled, f.sim_list = [], []
    monkeypatch.setattr(M, "SIMS_FILE", tmp_path / ".freya-sims.json")
    monkeypatch.setattr(M, "TUNNEL_BACKOFF_S", 0.01)
    monkeypatch.setattr(freya, "cancel_simulation", lambda sid: f.cancelled.append(sid) or
                        {"success": True, "cancelledJobs": 3, "cancelledIterations": 1})
    monkeypatch.setattr(freya, "list_simulations", lambda: f.sim_list)
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
    assert slot == env.slot
    assert "trycloudflare" not in wf and HOST in wf
    out = env.out / (env.slot + ".workflow.json")
    assert env.exported == [str(out)] and out.exists()
    assert json.loads((env.out / (env.slot + ".spec.json")).read_text())["bank_name"] == A["bank_name"]
    rows = trace_rows(env)
    start = rows[0]
    assert start["briefs"] and start["model"] and "git_commit" in start
    saved = [r for r in rows if r["kind"] == "saved"][0]
    assert saved["workflow_id"] == "wf-" + env.slot and saved["agent_id"] == "ag-" + env.slot and saved["url_kind"] == "official"
    assert [r for r in rows if r["kind"] == "export"][0]["path"] == str(out)
    end = rows[-1]
    assert end["ok"] is True and end["duration_s"] >= 0 and end["workflow_id"] == "wf-" + env.slot
    md = list(env.traces.glob("*.md"))
    assert md and "wf-" + env.slot in md[0].read_text()


def test_slot_flag_and_budget_passed_to_extract(env):
    assert run_main(env, "--no-selftest", "--slot", "dress-X") == 0
    assert env.saved[0][0] == "dress-X"
    assert 0 < env.extract_kw["deadline_s"] <= 360


def test_selftest_all_pass_skips_round_two(env, monkeypatch):
    def run(agent_id, cases, bank, styles=None, store=None, on_created=None, on_busy=None):
        env.selftest_calls.append((agent_id, list(cases), bank))
        on_created("sim-1")
        return selftest_result(cases)
    monkeypatch.setattr(selftest, "run", run)
    called = []
    monkeypatch.setattr(M, "run_fix", lambda *a, **k: called.append(1))
    assert run_main(env) == 0
    assert len(env.selftest_calls) == 1 and not called
    agent, cases, bank = env.selftest_calls[0]
    assert agent == "ag-" + env.slot + "-selftest" and bank == TUNNEL
    assert len(cases) == 11          # n=10 plus the injection case (notes off in spec A)
    assert env.tunnel_started == 1 and env.tunnel_stopped == 1
    (s1, wf1), (s2, wf2) = env.saved
    assert s1 == env.slot + "-selftest" and TUNNEL + "/v1" in wf1
    assert s2 == env.slot and "trycloudflare" not in wf2 and HOST in wf2
    rows = trace_rows(env)
    [r1] = [r for r in rows if r["kind"] == "selftest_round"]
    assert r1["passed"] == 11 and r1["total"] == 11 and r1["failing"] == []
    assert [r["url_kind"] for r in rows if r["kind"] == "saved"] == ["tunnel", "official"]
    assert "fix_round" not in kinds(env)
    [st] = [r for r in rows if r["kind"] == "simulation_started"]
    assert st["simulation_id"] == "sim-1" and st["round"] == 1


def test_notes_enabled_asks_for_eleven(env, monkeypatch):
    env.spec = B
    seen = []
    real = M.cases.generate

    def gen(spec, openapi, now, n=10, seed=0):
        seen.append((n, seed, now))
        return real(spec, openapi, now, n=n, seed=seed)
    monkeypatch.setattr(M.cases, "generate", gen)
    monkeypatch.setattr(selftest, "run", lambda a, c, b, **k: selftest_result(c))
    assert run_main(env, "--now", "2026-10-14T11:00") == 0
    assert seen == [(11, 0, "2026-10-14T11:00")]


def test_failures_run_fix_round_and_keep_best(env, monkeypatch):
    def run(agent_id, cases, bank, **k):
        env.selftest_calls.append([c["id"] for c in cases])
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
    assert json.loads((env.out / (env.slot + ".spec.json")).read_text())["bank_name"] == "Fixed Bank"
    assert [s for s, _ in env.saved] == [env.slot + "-selftest", env.slot + "-selftest", env.slot]
    assert env.selftest_calls[1] == env.selftest_calls[0]          # round 2 reruns round 1's cases (no extra here)
    best = [r for r in rows if r["kind"] == "best_spec"][0]
    assert best["comparable"] == 11 and best["round1_passed"] == 9 and best["round2_passed"] == 11


def test_worse_round_two_keeps_round_one_spec(env, monkeypatch):
    def run(agent_id, cases, bank, **k):
        env.selftest_calls.append(1)
        n = 1 if len(env.selftest_calls) == 1 else 3
        return selftest_result(cases, [c["id"] for c in cases[:n]])
    monkeypatch.setattr(selftest, "run", run)
    fixed = copy.deepcopy(A)
    fixed["bank_name"] = "Worse Bank"
    monkeypatch.setattr(M, "run_fix", lambda *a, **k: (fixed, {"changes": [{"path": "bank_name", "quote": "q"}],
                                                              "discarded": [], "errors": 0}))
    assert run_main(env) == 0
    assert json.loads((env.out / (env.slot + ".spec.json")).read_text())["bank_name"] == A["bank_name"]


def _fix_to(monkeypatch, fixed, path="x.y"):
    monkeypatch.setattr(M, "run_fix", lambda *a, **k: (fixed, {"changes": [{"path": path, "quote": "q"}],
                                                              "discarded": [], "errors": 0}))


def test_tie_keeps_round_one(env, monkeypatch):
    monkeypatch.setattr(selftest, "run", lambda a, cases, b, **k: selftest_result(cases, [cases[0]["id"]]))
    fixed = copy.deepcopy(A)
    fixed["bank_name"] = "Tie Bank"
    _fix_to(monkeypatch, fixed)
    assert run_main(env) == 0
    assert json.loads((env.out / (env.slot + ".spec.json")).read_text())["bank_name"] == A["bank_name"]


def test_round2_cases_compare_only_identical_expectations():
    c1 = [{"id": "SELF-01", "scenario": "a", "expected": {"o": 1}}, {"id": "SELF-02", "scenario": "b", "expected": {"o": 2}},
          {"id": "SELF-03", "scenario": "c", "expected": {"o": 3}}]
    c2 = [{"id": "SELF-01", "scenario": "a", "expected": {"o": 1}}, {"id": "SELF-02", "scenario": "b", "expected": {"o": 9}},
          {"id": "SELF-03", "scenario": "d", "expected": {"o": 4}}]
    run, comparable, extra = M.round2_cases(c1, c2)
    assert comparable == {"SELF-01"}
    assert [c["id"] for c in run] == ["SELF-01", "SELF-02", "SELF-03", "SELF-R2-01", "SELF-R2-02"]
    assert [c["scenario"] for c in extra] == ["b", "d"]
    assert c2[1]["id"] == "SELF-02"                                   # inputs not mutated


def test_extra_round2_cases_run_but_not_compared(env, monkeypatch):
    calls = []

    def run(agent_id, cases, bank, **k):
        calls.append([c["id"] for c in cases])
        if len(calls) == 1:
            return selftest_result(cases, [cases[0]["id"]])
        return selftest_result(cases, [c["id"] for c in cases if c["id"].startswith("SELF-R2")])
    monkeypatch.setattr(selftest, "run", run)
    fixed = copy.deepcopy(A)
    fixed["negotiation"]["floor_offer"]["times"] = 2
    _fix_to(monkeypatch, fixed, "negotiation.floor_offer.times")
    monkeypatch.setattr(M, "round2_cases", lambda c1, c2: (
        list(c1) + [dict(c2[0], id="SELF-R2-01")], {c["id"] for c in c1[1:]}, [dict(c2[0], id="SELF-R2-01")]))
    assert run_main(env) == 0
    rows = trace_rows(env)
    best = [r for r in rows if r["kind"] == "best_spec"][0]
    assert calls[1] == calls[0] + ["SELF-R2-01"]
    # SELF-R2-01 failed in round 2, SELF-01 failed in round 1; neither is comparable: 10 vs 10 -> tie -> round 1
    assert (best["comparable"], best["round1_passed"], best["round2_passed"]) == (10, 10, 10)
    assert best["label"] == "extract"


def test_budget_stop_marks_orphan_simulation(env, monkeypatch):
    monkeypatch.setattr(M, "FINAL_RESERVE_S", 0.5)

    def slow(agent_id, cases, bank, on_created=None, **k):
        on_created("sim-orphan")
        time.sleep(8)
    monkeypatch.setattr(selftest, "run", slow)
    assert run_main(env, "--budget-min", "0.05") == 0
    rows = trace_rows(env)
    assert [r["id"] for r in rows if r["kind"] == "orphan_simulation"] == ["sim-orphan"]
    assert env.cancelled == ["sim-orphan"]
    [oc] = [r for r in rows if r["kind"] == "orphan_cancelled"]
    assert oc["id"] == "sim-orphan" and oc["success"] is True and oc["cancelled_jobs"] == 3
    assert M.own_sims() == {"sim-orphan"}
    assert (env.out / (env.slot + ".workflow.json")).exists()


def test_late_created_orphan_is_cancelled(env, monkeypatch):
    monkeypatch.setattr(M, "FINAL_RESERVE_S", 0.5)
    done = []

    def late(agent_id, cases, bank, on_created=None, **k):
        time.sleep(3.5)                     # past the 2.5 s test window: abandoned first, created after
        on_created("sim-late")
        done.append(1)
    monkeypatch.setattr(selftest, "run", late)
    assert run_main(env, "--budget-min", "0.05") == 0
    t0 = time.monotonic()
    while not done and time.monotonic() - t0 < 5:
        time.sleep(0.1)
    assert env.cancelled == ["sim-late"]


def test_busy_workspace_cancels_only_our_running_simulations(env, monkeypatch):
    M.record_sim("sim-ours-running", "old-slot")
    M.record_sim("sim-ours-done", "old-slot")
    env.sim_list = [{"id": "sim-ours-running", "status": "running"}, {"id": "sim-foreign", "status": "running"},
                    {"id": "sim-ours-done", "status": "completed"}, {"id": "sim-foreign-2", "status": "pending"}]

    def busy_then_ok(agent_id, cases, bank, on_busy=None, on_created=None, **k):
        on_busy(0)
        on_created("sim-new")
        return selftest_result(cases)
    monkeypatch.setattr(selftest, "run", busy_then_ok)
    assert run_main(env) == 0
    assert env.cancelled == ["sim-ours-running"]
    rows = trace_rows(env)
    assert [r["id"] for r in rows if r["kind"] == "orphan_cancelled"] == ["sim-ours-running"]
    assert sorted(r["id"] for r in rows if r["kind"] == "foreign_simulation_running") == ["sim-foreign", "sim-foreign-2"]
    assert M.own_sims() == {"sim-ours-running", "sim-ours-done", "sim-new"}


def test_freya_cancel_and_list_call_the_api(monkeypatch):
    calls = []

    def api(method, path, body=None, **params):
        calls.append((method, path, params))
        return {"success": True, "cancelledJobs": 2, "cancelledIterations": 1} if method == "POST" else \
            {"simulations": [{"id": "s1", "status": "running"}]}
    monkeypatch.setattr(freya, "api", api)
    monkeypatch.setattr(freya, "workspace_id", lambda: "ws1")
    assert freya.cancel_simulation("s9")["cancelledJobs"] == 2
    assert freya.list_simulations() == [{"id": "s1", "status": "running"}]
    assert calls[0][:2] == ("POST", "/api/v2/simulations/s9/cancel")
    assert calls[1] == ("GET", "/api/v2/simulations", {"workspaceId": "ws1"})


def test_busy_workspace_skips_selftest(env, monkeypatch):
    def busy(agent_id, cases, bank, on_busy=None, **k):
        for w in (0, 30, 60, 90, 120, 150, 180):
            on_busy(w)
        raise AssertionError("on_busy should have stopped the wait")
    monkeypatch.setattr(selftest, "run", busy)
    assert run_main(env) == 0
    ks = kinds(env)
    assert ks.count("workspace_busy") == 1 and "selftest_skipped" in ks
    assert (env.out / (env.slot + ".workflow.json")).exists()


def test_lint_errors_in_best_spec_fall_back_to_extracted(env, monkeypatch):
    calls = []

    def run(agent_id, cases, bank, **k):
        calls.append(1)
        return selftest_result(cases, [cases[0]["id"]] if len(calls) == 1 else [])
    monkeypatch.setattr(selftest, "run", run)
    fixed = copy.deepcopy(A)
    fixed["bank_name"] = "Linty Bank"
    _fix_to(monkeypatch, fixed)
    real = M.lint
    monkeypatch.setattr(M, "lint", lambda wf, spec: real(wf, spec) + (["bad node"] if spec["bank_name"] == "Linty Bank" else []))
    assert run_main(env) == 0
    rows = trace_rows(env)
    assert [r["label"] for r in rows if r["kind"] == "best_spec"] == ["fix1"]
    assert any(r["kind"] == "final_spec_switch" for r in rows)
    assert json.loads((env.out / (env.slot + ".spec.json")).read_text())["bank_name"] == A["bank_name"]


def test_hanging_save_is_bounded(env, monkeypatch):
    monkeypatch.setattr(M, "FREYA_CALL_S", 0.5)
    monkeypatch.setattr(freya, "save_workflow", lambda *a, **k: time.sleep(8))
    t0 = time.monotonic()
    assert run_main(env, "--no-selftest") == 1
    assert time.monotonic() - t0 < 5
    assert any(r["kind"] == "phase_failed" and r["phase"] == "final" for r in trace_rows(env))


def test_selftest_crash_still_exports(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("simulated selftest crash")
    monkeypatch.setattr(selftest, "run", boom)
    assert run_main(env) == 0
    assert (env.out / (env.slot + ".workflow.json")).exists()
    rows = trace_rows(env)
    assert any(r["kind"] == "phase_failed" and r["phase"] == "selftest" for r in rows)
    assert rows[-1]["kind"] == "end" and rows[-1]["ok"] is True
    assert env.tunnel_stopped == 1


def test_tunnel_failure_still_exports(env, monkeypatch):
    tries = []

    def bad(port):
        tries.append(port)
        raise RuntimeError("no cloudflared")
    monkeypatch.setattr(tunnel, "start", bad)
    assert run_main(env) == 0
    assert (env.out / (env.slot + ".workflow.json")).exists()
    assert any(r["kind"] == "phase_failed" for r in trace_rows(env))
    assert len(tries) == 3 and [r["retry"] for r in trace_rows(env) if r["kind"] == "tunnel_failed"] == [True, True, False]


def test_budget_hard_stop_during_selftest(env, monkeypatch):
    monkeypatch.setattr(M, "FINAL_RESERVE_S", 0.5)

    def slow(*a, **k):
        time.sleep(8)
        return selftest_result([])
    monkeypatch.setattr(selftest, "run", slow)
    t0 = time.monotonic()
    assert run_main(env, "--budget-min", "0.05") == 0      # 3 s
    assert time.monotonic() - t0 < 6
    assert (env.out / (env.slot + ".workflow.json")).exists()
    assert "budget_stop" in kinds(env)


def test_no_spec_exits_nonzero(env):
    env.spec = None
    assert run_main(env, "--no-selftest") != 0
    rows = trace_rows(env)
    assert any(r["kind"] == "no_spec" for r in rows)
    assert rows[-1]["kind"] == "end" and rows[-1]["ok"] is False
    assert not env.saved and not env.exported


def test_crashing_case_builder_still_runs_selftest(env, monkeypatch):
    def boom(c, **o):
        raise KeyError("bands")
    monkeypatch.setattr(M.cases, "s_late_refusal", boom)
    monkeypatch.setattr(selftest, "run", lambda a, cases, b, **k: selftest_result(cases))
    assert run_main(env) == 0
    [ev] = [r for r in trace_rows(env) if r["kind"] == "cases"]
    assert "late_refusal" in ev["skipped"] and "KeyError" in ev["errors"]["late_refusal"]
    assert any(r["kind"] == "selftest_round" for r in trace_rows(env))


def _counting_extract(monkeypatch, results):
    calls = []

    def fake(brief_paths, openapi, trace=None, **kw):
        calls.append(kw)
        r = results[min(len(calls), len(results)) - 1]
        return (copy.deepcopy(r), [], {"seconds": 1.0}) if r is not None else (None, ["no candidate"], {})
    monkeypatch.setattr(ex, "extract", fake)
    return calls


def test_extraction_without_spec_is_retried_once(env, monkeypatch):
    calls = _counting_extract(monkeypatch, [None, A])
    assert run_main(env, "--no-selftest") == 0
    assert len(calls) == 2 and "dual" not in calls[0]
    assert calls[1]["dual"] is False and calls[1]["use_oracle"] is False and calls[1]["deadline_s"] <= 360
    ks = kinds(env)
    assert "extract_retry" in ks and "no_spec" not in ks and "export" in ks


def test_extraction_retry_needs_budget(env, monkeypatch):
    calls = _counting_extract(monkeypatch, [None, A])
    assert run_main(env, "--no-selftest", "--budget-min", "10") == 2       # 600 s - reserve < 10 min: no retry
    assert len(calls) == 1 and "extract_retry" not in kinds(env) and "no_spec" in kinds(env)


def test_tunnel_retried_with_backoff(env, monkeypatch):
    tries = []

    def flaky(port):
        tries.append(port)
        if len(tries) < 3:
            raise RuntimeError("cloudflared printed no URL")
        return TUNNEL, "proc"
    monkeypatch.setattr(tunnel, "start", flaky)
    monkeypatch.setattr(selftest, "run", lambda a, cases, b, **k: selftest_result(cases))
    assert run_main(env) == 0
    assert len(tries) == 3
    rows = trace_rows(env)
    assert [(r["attempt"], r["retry"]) for r in rows if r["kind"] == "tunnel_failed"] == [(1, True), (2, True)]
    assert any(r["kind"] == "selftest_round" for r in rows)


def test_empty_weekdays_spec_does_not_hang(env, monkeypatch):
    spec = copy.deepcopy(A)
    spec["calendar"]["weekdays"] = []
    env.spec = spec
    t0 = time.monotonic()
    run_main(env, "--budget-min", "0.2")
    assert time.monotonic() - t0 < 20
    rows = trace_rows(env)
    assert rows[-1]["kind"] == "end"
    assert any("calendar.weekdays" in json.dumps(r) for r in rows)


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

def test_openapi_order_flag_then_brief_dir_then_single_doc(tmp_path):
    (tmp_path / "b.md").write_text("x")
    (tmp_path / "cases.json").write_text(json.dumps([{"id": 1}]))              # JSON, not OpenAPI: ignored
    with pytest.raises(M.OpenapiNotFound, match="no OpenAPI"):
        M.resolve_openapi([str(tmp_path / "b.md")], None)                     # never a silent tools/openapi.json
    (tmp_path / "bank-api.json").write_text(json.dumps(OA))
    assert M.resolve_openapi([str(tmp_path / "b.md")], None) == (tmp_path / "bank-api.json").resolve()
    assert M.resolve_openapi([str(tmp_path)], None) == (tmp_path / "bank-api.json").resolve()
    (tmp_path / "other-api.json").write_text(json.dumps(OA))
    with pytest.raises(M.OpenapiNotFound, match="several"):
        M.resolve_openapi([str(tmp_path / "b.md")], None)
    (tmp_path / "openapi.json").write_text(json.dumps(OA))
    assert M.resolve_openapi([str(tmp_path / "b.md")], None) == (tmp_path / "openapi.json").resolve()
    assert M.resolve_openapi([str(tmp_path)], None) == (tmp_path / "openapi.json").resolve()
    assert M.resolve_openapi([str(tmp_path / "b.md")], "/x/other.json") == Path("/x/other.json")
    assert M.resolve_openapi([str(ROOT / "briefs/brief-a.md")], None).resolve() == (ROOT / "tools/openapi.json").resolve()


def test_openapi_doc_is_never_a_brief(tmp_path):
    from builder import sdk
    (tmp_path / "b.md").write_text("x")
    (tmp_path / "bank-api.json").write_text(json.dumps(OA))
    (tmp_path / "cases.json").write_text("[]")
    assert [p.name for p in sdk.expand_briefs([str(tmp_path)])] == ["b.md", "cases.json"]
    assert [p.name for p in sdk.expand_briefs([str(tmp_path / "b.md"), str(tmp_path / "bank-api.json")])] == ["b.md"]


def test_no_openapi_exits_2_with_trace_event(env):
    (env.brief.parent / "openapi.json").unlink()
    assert run_main(env, "--no-selftest") == 2
    rows = trace_rows(env)
    assert rows[0]["kind"] == "start" and rows[0]["openapi"] is None
    assert any(r["kind"] == "no_openapi" and "--openapi" in r["error"] for r in rows)
    assert rows[-1]["kind"] == "end" and rows[-1]["exit_code"] == 2
    assert not env.saved and not env.exported


def test_openapi_flag_wins_and_odd_name_is_found(env):
    (env.brief.parent / "openapi.json").rename(env.brief.parent / "bank.openapi.json")
    assert run_main(env, "--no-selftest") == 0
    assert trace_rows(env)[0]["openapi"].endswith("bank.openapi.json")
    shutil.rmtree(env.traces)
    assert run_main(env, "--no-selftest", "--openapi", str(ROOT / "tools/openapi.json")) == 0
    assert trace_rows(env)[0]["openapi"] == str(ROOT / "tools/openapi.json")


def test_slug(tmp_path):
    import re
    assert re.fullmatch(r"briefs-brief-a-v2-[0-9a-f]{6}", M.slug_for(["briefs/Brief A (v2).md"]))
    (tmp_path / "hidden-1").mkdir()
    assert re.fullmatch(r"hidden-1-[0-9a-f]{6}", M.slug_for([str(tmp_path / "hidden-1") + "/"]))
    # same file name in two folders: two slots (and two export files); the same brief set: the same slot
    x, y = M.slug_for(["/r/x/brief.md"]), M.slug_for(["/r/y/brief.md"])
    assert x != y and x.startswith("x-brief-") and y.startswith("y-brief-")
    assert M.slug_for(["/r/x/brief.md"]) == x
    assert M.slug_for(["/r/x/brief.md", "/r/x/memo.md"]) != x
    assert M.slug_for(["/r/x/" + "z" * 80 + ".md"]).count("z") <= 40


def test_two_folders_same_brief_name_do_not_overwrite(env, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    shutil.copy(env.brief, other / env.brief.name)
    shutil.copy(env.brief.parent / "openapi.json", other / "openapi.json")
    assert run_main(env, "--no-selftest") == 0
    assert M.main([str(other / env.brief.name), "--no-selftest", "--out-dir", str(env.out), "--trace-dir", str(env.traces)]) == 0
    slots = [s for s, _ in env.saved]
    assert len(set(slots)) == 2 and len(list(env.out.glob("*.workflow.json"))) == 2
    assert M.main([str(env.brief), "--no-selftest", "--slot", "final-1", "--out-dir", str(env.out),
                   "--trace-dir", str(env.traces)]) == 0
    assert env.saved[-1][0] == "final-1" and (env.out / "final-1.workflow.json").exists()


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
