"""A25: a run Freya retried on its own (run record `attempts > 0`) is VOID: not a pass, not a failure.

Every attempt of a persona reuses one bank session, so retried attempts interleave in the ledger and the judge reads
only the last one. Fixture: the A24 gate iteration (sim 37df28da), where every run has attempts 4 and PUB-12/PUB-13
FAILed a correct agent. Offline: freya, the bank and the judge are monkeypatched.
"""
import copy
import json
from pathlib import Path

import pytest

import freya
import run_cases as rc
import score
from builder import main as M
from builder import selftest as S

ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "tests" / "fixtures" / "a25-retried"
ITER = json.loads((FX / "iteration.json").read_text())
LEDGER = {c: json.loads((FX / ("ledger-%s.json" % c)).read_text()) for c in ("PUB-12", "PUB-13")}
EMPTY = {"outcome": None, "reason": None, "promises": [], "notes": [], "callbacks": [], "transfers": [], "calls": []}
CASES = [c for c in json.loads((ROOT / "public-test-set.json").read_text()) if c["id"] in ("PUB-11", "PUB-12", "PUB-13")]


def _pid(cid):
    return next(r["personaId"] for r in ITER["runs"] if r["personaName"].startswith(cid + " "))


def _iteration(attempts=None):
    it = copy.deepcopy(ITER)
    if attempts is not None:
        for r in it["runs"]:
            r["attempts"] = attempts
    return it


# ------------------------------------------------------------------------------------------------- the rule

def test_retried_reads_attempts_of_the_run_record():
    assert [score.retried(r) for r in ITER["runs"]] == [True, True, True]          # the gate: attempts 4
    assert score.retried({"attempts": 0}) is False
    assert score.retried({}) is False                                           # an older record without the field
    assert score.retried({"attempts": None}) is False


# ------------------------------------------------------------------------------------------ tools/run_cases.py

@pytest.fixture
def fake_run_once(monkeypatch):
    state = {"it": _iteration()}

    def http(path, body=None):
        if path == "/v1/practice/sessions":
            return {"variables": {"customer_id": "c-" + body["case"]}}
        cid = path.rsplit("/", 1)[1][2:]
        return LEDGER.get(cid, EMPTY)
    monkeypatch.setattr(rc, "http", http)
    monkeypatch.setattr(rc.freya, "run_simulation", lambda *a, **k: ("sim1", "it1", "completed"))
    monkeypatch.setattr(rc.freya, "get_iteration", lambda s, i: state["it"])

    def no_call(cid):
        raise RuntimeError("offline")
    monkeypatch.setattr(rc.freya, "get_call", no_call)
    state["mp"] = monkeypatch
    return state


def test_run_once_retried_run_is_void_not_fail(fake_run_once, capsys):
    rows, *_ = rc.run_once("ag", CASES, {c["id"]: _pid(c["id"]) for c in CASES})
    out = capsys.readouterr().out
    assert [(r["id"], r["void"], r["pass"]) for r in rows] == [("PUB-11", True, False), ("PUB-12", True, False),
                                                               ("PUB-13", True, False)]
    for cid in ("PUB-11", "PUB-12", "PUB-13"):
        assert "VOID %s (platform retried the run)" % cid in out
    assert "FAIL " not in out and "PASS " not in out


def test_run_once_attempts_zero_scored_as_before(fake_run_once, capsys):
    fake_run_once["it"] = _iteration(attempts=0)
    rows, *_ = rc.run_once("ag", CASES, {c["id"]: _pid(c["id"]) for c in CASES})
    out = capsys.readouterr().out
    by = {r["id"]: r for r in rows}
    assert not any(r["void"] for r in rows)
    assert not by["PUB-12"]["pass"] and any(f.startswith("conversation:") for f in by["PUB-12"]["failed"])
    assert "FAIL PUB-12" in out and "VOID" not in out


def _main(monkeypatch, tmp_path, reps_rows, *argv):
    cases_file = tmp_path / "cases.json"
    cases_file.write_text(json.dumps(CASES))
    monkeypatch.setattr(rc, "ROOT", tmp_path)
    monkeypatch.setattr(rc.freya, "create_personas", lambda ps: {"personas": [{"id": "p%d" % i} for i, _ in enumerate(ps)]})
    it = iter(reps_rows)
    monkeypatch.setattr(rc, "run_once", lambda *a: (next(it), "sim1", "it1", "completed", 1.0, 0))
    monkeypatch.setattr("sys.argv", ["run_cases.py", "ag", "--cases", str(cases_file), "--out", str(tmp_path / "o.json"), *argv])
    rc.main()
    return json.loads((tmp_path / "o.json").read_text())


def _row(cid, ok, void=False):
    return {"id": cid, "level": "x", "family": "y", "pass": ok and not void, "void": void, "failed": [] if ok else ["f"]}


def test_main_void_case_not_counted_and_named_in_summary(monkeypatch, tmp_path, capsys):
    rows = [_row("PUB-11", True), _row("PUB-12", False, void=True), _row("PUB-13", False)]
    res = _main(monkeypatch, tmp_path, [rows])
    out = capsys.readouterr().out
    line = [x for x in out.splitlines() if "passed (bank" in x][0]
    assert line.startswith("1 / 2 passed (bank records and conversation checks)")
    assert "1 void" in line and "PUB-12" in line
    assert (res["passed"], res["total"], res["void"]) == (1, 2, ["PUB-12"])


def test_main_reps_void_rep_does_not_fail_a_case(monkeypatch, tmp_path, capsys):
    reps = [[_row("PUB-11", True), _row("PUB-12", True), _row("PUB-13", False)],
            [_row("PUB-11", True), _row("PUB-12", False, void=True), _row("PUB-13", False, void=True)],
            [_row("PUB-11", True), _row("PUB-12", True), _row("PUB-13", True)]]
    res = _main(monkeypatch, tmp_path, reps, "--reps", "3")
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert "PASS PUB-11 3/3" in lines
    assert [x for x in lines if x.startswith("FAIL ")] == ["FAIL PUB-13 1/3 (1 void)"]   # a real failure in rep 1 still fails
    assert any(x.startswith("VOID PUB-12 ") for x in lines)                      # 2 passes and 1 retried run: no verdict
    line = [x for x in lines if "passed (bank" in x][0]
    assert line.startswith("1 / 2 passed (bank") and "PUB-12" in line
    assert "-- run 2 of 3: 1 / 1 passed, 2 void" in out
    assert res["void"] == ["PUB-12"] and (res["passed"], res["total"]) == (1, 2)


def test_main_without_void_prints_as_before(monkeypatch, tmp_path, capsys):
    _main(monkeypatch, tmp_path, [[_row("PUB-11", True), _row("PUB-12", False), _row("PUB-13", True)]])
    out = capsys.readouterr().out
    line = [x for x in out.splitlines() if "passed (bank" in x][0]
    assert line.startswith("2 / 3 passed (bank records and conversation checks). Last simulation sim1")
    assert "void" not in out.lower()


# ----------------------------------------------------------------------------------------- builder/selftest

@pytest.fixture
def fake_selftest(monkeypatch, tmp_path):
    state = {"it": _iteration()}
    cases = [dict(c, seed={}) for c in CASES]

    def http(base, path, body=None):
        if path == "/v1/practice/sessions":
            return {"ok": True, "variables": {"customer_id": "c-" + body["case"]}}
        return LEDGER.get(path.rsplit("/", 1)[1][2:], EMPTY)
    monkeypatch.setattr(S, "_http", http)
    monkeypatch.setattr(S, "_personas", lambda styled, store: {c["id"]: _pid(c["id"]) for c, _ in styled})
    monkeypatch.setattr(S.freya, "run_simulation", lambda *a, **k: ("sim1", "it1", "completed"))
    monkeypatch.setattr(S.freya, "get_iteration", lambda s, i: state["it"])
    monkeypatch.setattr(S.freya, "get_call", lambda cid: {"messages": []})
    state["cases"] = cases
    return state


def test_selftest_retried_runs_are_void_not_rows(fake_selftest):
    res = S.run("ag", fake_selftest["cases"], "http://bank")
    assert (res["passed"], res["total"], res["rows"]) == (0, 0, [])
    assert [v["id"] for v in res["void"]] == ["PUB-11", "PUB-12", "PUB-13"]
    assert all(v["attempts"] == 4 for v in res["void"])


def test_selftest_attempts_zero_scored_as_before(fake_selftest):
    fake_selftest["it"] = _iteration(attempts=0)
    res = S.run("ag", fake_selftest["cases"], "http://bank")
    assert res["void"] == [] and res["total"] == 3
    assert not {r["id"]: r for r in res["rows"]}["PUB-12"]["pass"]


# ---------------------------------------------------------------------------------------------- builder/main

from test_main_dry import env, clean_env, run_main, trace_rows, kinds, A  # noqa: E402,F401


def _result(cases, failing=(), void=()):
    rows = [{"id": c["id"], "scenario": c.get("scenario"), "pass": c["id"] not in failing,
             "failed": ["x"] if c["id"] in failing else [], "call_id": "call-" + c["id"], "tools": []}
            for c in cases if c["id"] not in void]
    return {"passed": sum(r["pass"] for r in rows), "total": len(rows), "rows": rows,
            "void": [{"id": c["id"], "void": True, "attempts": 4} for c in cases if c["id"] in void],
            "simulation_id": "sim1", "iteration_id": "it1", "status": "completed"}


def _counting(results):
    """selftest.run stand-in: call k returns results[k](cases); records each call's case ids."""
    calls = []

    def run(a, cases, b, **k):
        calls.append([c["id"] for c in cases])
        return results[min(len(calls), len(results)) - 1](cases)
    return run, calls


def _all_void(cases):
    return _result(cases, void=[c["id"] for c in cases])


def test_main_all_void_twice_is_low_signal_and_no_fix(env, monkeypatch):
    run, calls = _counting([_all_void])
    monkeypatch.setattr(S, "run", run)
    called = []
    monkeypatch.setattr(M, "run_fix", lambda *a, **k: called.append(a))
    assert run_main(env) == 0
    assert len(calls) == 2 and calls[0] == calls[1]                  # one re-run of round 1, never more
    assert not called
    rows = trace_rows(env)
    r1 = [r for r in rows if r["kind"] == "selftest_round"]
    assert [r["round"] for r in r1] == [1, 1] and all(r["total"] == 0 and len(r["void"]) == 11 for r in r1)
    assert [r["round"] for r in rows if r["kind"] == "selftest_rerun"] == [1]
    [low] = [r for r in rows if r["kind"] == "selftest_low_signal"]
    assert low["round"] == 1 and low["scored"] == 0 and low["cases"] == 11 and low["rerun"] is True
    assert "round2_skipped" not in kinds(env)
    assert json.loads((env.out / (env.slot + ".spec.json")).read_text())["bank_name"] == A["bank_name"]
    md = next(env.traces.glob("*.md")).read_text()
    assert "void" in md.lower()


def test_main_all_void_without_time_keeps_todays_behaviour(env, monkeypatch):
    run, calls = _counting([_all_void])
    monkeypatch.setattr(S, "run", run)
    monkeypatch.setattr(M, "ROUND2_MIN_S", 10 ** 6)
    called = []
    monkeypatch.setattr(M, "run_fix", lambda *a, **k: called.append(a))
    assert run_main(env) == 0
    assert len(calls) == 1 and not called
    rows = trace_rows(env)
    assert "selftest_rerun" not in kinds(env)
    [low] = [r for r in rows if r["kind"] == "selftest_low_signal"]
    assert low["round"] == 1 and low["rerun"] is False
    assert [r["round"] for r in rows if r["kind"] == "selftest_no_signal"] == [1]


def test_main_all_void_rerun_with_signal_goes_on_normally(env, monkeypatch):
    run, calls = _counting([_all_void, lambda cs: _result(cs, failing=[cs[0]["id"]]), lambda cs: _result(cs)])
    monkeypatch.setattr(S, "run", run)
    seen = {}
    fixed = copy.deepcopy(A)
    fixed["bank_name"] = "Fixed Bank"

    def run_fix(spec, result, cases, *a, **k):
        seen["failing"] = [r["id"] for r in result["rows"] if not r["pass"]]
        seen["total"] = result["total"]
        return fixed, {"changes": [{"path": "bank_name", "quote": "q"}], "discarded": [], "errors": 0}
    monkeypatch.setattr(M, "run_fix", run_fix)
    assert run_main(env) == 0
    assert len(calls) == 3                                           # round 1, its re-run, round 2
    assert seen == {"failing": ["SELF-01"], "total": 11}
    rows = trace_rows(env)
    assert "selftest_low_signal" not in kinds(env)
    best = [r for r in rows if r["kind"] == "best_spec"][0]
    assert best["comparable"] == 11 and best["round1_passed"] == 10 and best["round2_passed"] == 11
    assert json.loads((env.out / (env.slot + ".spec.json")).read_text())["bank_name"] == "Fixed Bank"


def test_main_one_scored_of_eleven_is_rerun_and_combined(env, monkeypatch):
    def first(cs):          # only SELF-01 scored, and it failed
        return _result(cs, failing=[cs[0]["id"]], void=[c["id"] for c in cs[1:]])

    def again(cs):          # SELF-01 void now, SELF-02..06 scored, SELF-02 failed
        return _result(cs, failing=[cs[1]["id"]], void=[cs[0]["id"]] + [c["id"] for c in cs[6:]])
    run, calls = _counting([first, again, lambda cs: _result(cs)])
    monkeypatch.setattr(S, "run", run)
    seen = {}

    def run_fix(spec, result, cases, *a, **k):
        seen["failing"] = sorted(r["id"] for r in result["rows"] if not r["pass"])
        seen["scored"] = sorted(r["id"] for r in result["rows"])
        return spec, {"changes": [], "discarded": [], "errors": 0}
    monkeypatch.setattr(M, "run_fix", run_fix)
    assert run_main(env) == 0
    assert len(calls) == 2
    assert seen["failing"] == ["SELF-01", "SELF-02"]
    assert seen["scored"] == ["SELF-0%d" % i for i in range(1, 7)]   # 6 of 11 scored over both runs: enough signal
    assert "selftest_low_signal" not in kinds(env)


def test_low_signal_rule():
    assert M.low_signal({"total": 0, "void": [{"id": "a"}]}, 1)
    assert M.low_signal({"total": 1}, 10) and M.low_signal({"total": 4}, 10)
    assert not M.low_signal({"total": 5}, 10) and not M.low_signal({"total": 0}, 0)


def test_main_void_cases_never_reach_the_fix_round(env, monkeypatch):
    calls = []

    def run(a, cases, b, **k):
        calls.append(1)
        ids = [c["id"] for c in cases]
        if len(calls) == 1:
            return _result(cases, failing=ids[:1], void=ids[1:3])
        return _result(cases, void=ids[1:2])
    monkeypatch.setattr(S, "run", run)
    seen = {}
    fixed = copy.deepcopy(A)
    fixed["bank_name"] = "Fixed Bank"

    def run_fix(spec, result, cases, *a, **k):
        seen["rows"] = [r["id"] for r in result["rows"] if not r["pass"]]
        return fixed, {"changes": [{"path": "bank_name", "quote": "q"}], "discarded": [], "errors": 0}
    monkeypatch.setattr(M, "run_fix", run_fix)
    assert run_main(env) == 0
    assert seen["rows"] == ["SELF-01"]
    rows = trace_rows(env)
    r1 = [r for r in rows if r["kind"] == "selftest_round"][0]
    assert r1["failing"] == ["SELF-01"] and r1["void"] == ["SELF-02", "SELF-03"] and r1["total"] == 9
    best = [r for r in rows if r["kind"] == "best_spec"][0]
    # void in either round: not comparable (SELF-02, SELF-03 out); round 1 passed 8 of the 9 left, round 2 all 9
    assert best["comparable"] == 9 and best["round1_passed"] == 8 and best["round2_passed"] == 9


# --------------------------------------------------------------------------------------------- bench/run.py

def test_bench_summarize_reports_void_separately():
    import bench.run as br
    runs = [{"passed": 1, "total": 2, "rows": [{"id": "A", "pass": True}, {"id": "B", "pass": False, "failed": ["x"]}],
             "void": [{"id": "C", "void": True, "attempts": 4}]}]
    s = br.summarize_sim(runs)
    assert (s["passed"], s["total"]) == (1, 2)
    assert [f["id"] for f in s["failures"]] == ["B"]
    assert s["void"] == [{"id": "C", "rep": 1}]
    md = br.render_md({"builder": "ours", "started": "t", "reps": 1}, [{"name": "x", "sim": s}])
    assert "void" in md.lower() and "C" in md


# ----------------------------------------------------------------------------------------- tools/conformance.py

def test_conformance_drops_retried_runs(monkeypatch, tmp_path, capsys):
    import conformance as cf
    monkeypatch.setattr(cf.freya, "run_simulation", lambda *a, **k: ("sim1", "it1", "completed"))
    it = _iteration()
    it["runs"][1]["attempts"] = 0                                                # PUB-13 healthy, PUB-11/12 retried
    monkeypatch.setattr(cf.freya, "get_iteration", lambda s, i: it)
    got = cf.live("ag", CASES, {c["id"]: _pid(c["id"]) for c in CASES}, lambda i: {"customer_id": "c-" + i},
                  lambda cid: LEDGER.get(cid[2:], EMPTY), "ours")
    assert sorted(got) == ["PUB-13"]                    # an interleaved ledger says nothing about the bank
    assert "VOID PUB-12 (platform retried the run)" in capsys.readouterr().out
    # official ledgers taken from an earlier run_cases output skip its void rows
    p = tmp_path / "run.json"
    p.write_text(json.dumps({"rows": [{"id": "PUB-12", "customer_id": "c1", "void": True},
                                      {"id": "PUB-13", "customer_id": "c2"}]}))
    monkeypatch.setattr(cf, "official", lambda path, body=None: LEDGER["PUB-13"])
    off = cf.load_official([p], CASES)
    assert off["PUB-12"] == [] and len(off["PUB-13"]) == 1


# ----------------------------------------------------------------------------------------- review round 1

def test_retried_accepts_numeric_strings_and_floats():
    assert score.retried({"attempts": "4"}) and score.retried({"attempts": 4.0}) and score.retried({"attempts": " 2 "})
    assert not score.retried({"attempts": "0"}) and not score.retried({"attempts": 0.0})
    assert not score.retried({"attempts": "n/a"}) and not score.retried({"attempts": True}) and not score.retried({"attempts": []})


def test_main_reps_fail_line_keeps_reps_and_names_void(monkeypatch, tmp_path, capsys):
    reps = [[_row("PUB-11", True), _row("PUB-12", True), _row("PUB-13", False)],
            [_row("PUB-11", True), _row("PUB-12", True), _row("PUB-13", False, void=True)],
            [_row("PUB-11", True), _row("PUB-12", True), _row("PUB-13", True)]]
    _main(monkeypatch, tmp_path, reps, "--reps", "3")
    import re
    fails = [x for x in capsys.readouterr().out.splitlines() if x.startswith("FAIL ")]
    assert fails == ["FAIL PUB-13 1/3 (1 void)"]
    assert re.match(r"^FAIL [A-Z0-9-]+ [0-2]/3", fails[0])                 # results/regate.sh still finds it


def test_run_once_slow_median_and_hangups_skip_void(fake_run_once, capsys):
    it = _iteration()
    by_case = {r["personaName"].split()[0]: r for r in it["runs"]}
    by_case["PUB-13"]["attempts"] = 0                                        # PUB-11/12 void, PUB-13 scored
    fake_run_once["it"] = it
    slow = {by_case["PUB-11"]["callId"], by_case["PUB-12"]["callId"]}

    def get_call(cid):
        gap = 100 if cid in slow else 1
        return {"endedReason": "customer-ended-call" if cid in slow else "assistant-ended-call",
                "messages": [{"role": "user", "secondsFromStart": 0}, {"role": "assistant", "secondsFromStart": gap}]}
    monkeypatch_get_call = fake_run_once["mp"]
    monkeypatch_get_call.setattr(rc.freya, "get_call", get_call)
    rows, sim, itid, status, took, med = rc.run_once("ag", CASES, {c["id"]: _pid(c["id"]) for c in CASES})
    out = capsys.readouterr().out
    assert med == 1
    assert "median 1 s, worst 1 s" in out and "hung up: 0" in out and "WARNING" not in out
