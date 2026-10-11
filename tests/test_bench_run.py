import json
import os
import pathlib
import time

import pytest

from bench import run as br


def _mk(tmp, name, files=("brief.md",), cases=True):
    d = tmp / name
    d.mkdir()
    (d / "openapi.json").write_text("{}")
    for f in files: (d / f).write_text("x")
    for f in ("notes.md", "verify.py", "bank_patch.md"): (d / f).write_text("x")
    if cases: (d / "cases.json").write_text("[]")
    return d


def test_discover_skips_underscore_files_and_marks_sim_skips(tmp_path):
    _mk(tmp_path, "ids")
    _mk(tmp_path, "plan2")
    _mk(tmp_path, "raw-a", files=("01-mail.md", "04-codes.csv"), cases=False)
    (tmp_path / "_replay.py").write_text("")
    (tmp_path / "_scratch").mkdir()
    (tmp_path / "noopenapi").mkdir()
    bs = br.discover(tmp_path)
    assert [b["name"] for b in bs] == ["ids", "plan2", "raw-a"]
    by = {b["name"]: b for b in bs}
    assert [f.name for f in by["ids"]["files"]] == ["brief.md"]
    assert [f.name for f in by["raw-a"]["files"]] == ["01-mail.md", "04-codes.csv"]
    assert by["ids"]["sim_skip"] is None
    assert "extraction-only" in by["plan2"]["sim_skip"]
    assert by["raw-a"]["sim_skip"] == "no cases.json" and by["raw-a"]["cases"] is None


def test_discover_names_and_unknown(tmp_path):
    _mk(tmp_path, "ids"); _mk(tmp_path, "sme")
    assert [b["name"] for b in br.discover(tmp_path, ["sme"])] == ["sme"]
    with pytest.raises(SystemExit, match="nope"):
        br.discover(tmp_path, ["nope"])


def test_real_corpus_discovers():
    names = [b["name"] for b in br.discover()]
    assert "plan2" in names and "ids" in names and not any(n.startswith("_") for n in names)
    for b in br.discover():
        assert b["files"] and all(f.name not in br.NOT_BRIEF for f in b["files"])


def test_build_cmd(tmp_path):
    d = _mk(tmp_path, "ids")
    b = br.discover(tmp_path)[0]
    c = br.build_cmd("ours", b, tmp_path / "out", python="py")
    assert c[:3] == ["py", "-m", "builder.main"] and str(d / "brief.md") in c
    assert c[c.index("--slot") + 1] == "bench-ids" and "--no-selftest" in c
    assert c[c.index("--openapi") + 1] == str(d / "openapi.json") and c[c.index("--out-dir") + 1] == str(tmp_path / "out")
    f = br.build_cmd("fallback", b, tmp_path / "out", python="py", repo_root=tmp_path)
    assert f == ["py", str(tmp_path / "fallback" / "agent_builder.py"), str(d / "brief.md")]


def test_extraction_checks():
    cases = [{"identity": {"postcode": "x", "birth_year": "1"},
              "seed": {"reasons": {"promise": [], "no_promise": ["refused", "dispute"]}}}]
    spec = {"outcomes": [{"outcome": "promise", "reason": None}, {"outcome": "no_promise", "reason": "refused"},
                         {"outcome": "no_promise", "reason": "bogus"}],
            "identity": {"questions": [{"field": "postcode"}, {"field": "date_of_birth"}]}}
    ch = br.extraction_checks(spec, cases)
    assert ch["outcomes"] == {"ok": False, "missing": [["no_promise", "dispute"]], "extra": [["no_promise", "bogus"]]}
    assert ch["identity"] == {"ok": False, "missing": ["birth_year"], "extra": ["date_of_birth"]}
    good = {"outcomes": [{"outcome": "promise", "reason": None}, {"outcome": "no_promise", "reason": "refused"},
                         {"outcome": "no_promise", "reason": "dispute"}],
            "identity": {"questions": [{"field": "postcode"}, {"field": "birth_year"}]}}
    assert all(v["ok"] for v in br.extraction_checks(good, cases).values())
    assert br.extraction_checks({}, []) == {}


def test_summarize_sim():
    runs = [{"passed": 1, "total": 2, "rows": [{"id": "A", "pass": True}, {"id": "B", "pass": False, "failed": ["x", "y"]}]},
            {"passed": 2, "total": 2, "rows": [{"id": "A", "pass": True}, {"id": "B", "pass": True}]}]
    assert br.summarize_sim(runs) == {"passed": 3, "total": 4, "failures": [{"id": "B", "rep": 1, "failed": ["x", "y"]}],
                                       "void": []}


def _brief(name="ids", skip=None):
    return {"name": name, "sim_skip": skip, "cases": "c"}


def test_run_brief_happy():
    rec = br.run_brief(_brief(), "ours", lambda b: {"seconds": 3.0, "exit": 0, "artifact": "spec.json"},
                       lambda b, a: {"passed": 7, "total": 8, "failures": []},
                       extract_fn=lambda b, a: {"spec_errors": 0, "conflicts": 2, "checks": {}})
    assert rec["build_s"] == 3.0 and rec["exit"] == 0 and rec["spec_errors"] == 0 and rec["conflicts"] == 2
    assert rec["sim"]["passed"] == 7 and rec["errors"] == []


def test_run_brief_captures_errors_and_continues(monkeypatch):
    monkeypatch.setenv("FREYA_API_KEY", "fak_supersecretvalue123")

    def boom(b, a): raise RuntimeError("sim died with fak_supersecretvalue123")
    rec = br.run_brief(_brief(), "ours", lambda b: {"seconds": 1, "exit": 0, "artifact": "s"}, boom,
                       extract_fn=lambda b, a: 1 / 0)
    assert [e["phase"] for e in rec["errors"]] == ["extract", "sim"]
    assert "ZeroDivisionError" in rec["errors"][0]["error"]
    assert "supersecret" not in json.dumps(rec)

    def bad_build(b): raise OSError("no python")
    called = []
    rec = br.run_brief(_brief(), "ours", bad_build, lambda b, a: called.append(1))
    assert rec["errors"][0]["phase"] == "build" and rec["sim_skip"] == "no build output" and not called


def test_run_brief_sim_skips():
    called = []
    sim = lambda b, a: called.append(1)
    ok = lambda b: {"seconds": 1, "exit": 0, "artifact": "s"}
    assert br.run_brief(_brief(), "ours", ok, sim, no_sim=True)["sim_skip"] == "--no-sim"
    assert br.run_brief(_brief(skip="extraction-only"), "ours", ok, sim)["sim_skip"] == "extraction-only"
    failed = br.run_brief(_brief(), "ours", lambda b: {"seconds": 1, "exit": 2, "tail": "no spec", "artifact": None}, sim)
    assert failed["sim_skip"] == "no build output" and failed["build_tail"] == "no spec"
    assert not called


def test_render_md():
    meta = {"builder": "ours", "started": "s", "reps": 1, "git_commit": "abc"}
    rows = [{"name": "ids", "build_s": 300.2, "exit": 0, "spec_errors": 1, "conflicts": 0,
             "checks": {"outcomes": {"ok": False, "missing": [["a", "b"]], "extra": []}, "identity": {"ok": True}},
             "sim": {"passed": 7, "total": 8, "failures": [{"id": "IDS-03", "rep": 1, "failed": ["wrong | date"]}]}, "errors": []},
            {"name": "plan2", "build_s": None, "exit": None, "sim": None, "sim_skip": "extraction-only",
             "errors": [{"phase": "build", "error": "OSError: x"}], "build_tail": "trace"}]
    md = br.render_md(meta, rows)
    assert "| ids | 300.2 | 0 | 1 | 0 | outcomes MISMATCH, identity ok | 7/8 | IDS-03: wrong \\| date |" in md
    assert "| plan2 | - | - | - | - | - | skipped: extraction-only | build error: OSError: x |" in md
    assert "**Sim total: 7/8**" in md and "missing [['a', 'b']]" in md and "trace" in md


def test_newest_workflow(tmp_path):
    old = tmp_path / "a.workflow.json"; old.write_text("{}")
    os.utime(old, (time.time() - 100, time.time() - 100))
    since = time.time() - 10
    assert br.newest_workflow(tmp_path, since) is None
    new = tmp_path / "b.workflow.json"; new.write_text("{}")
    assert br.newest_workflow(tmp_path, since) == new


def test_sync_state(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    br.sync_state(a, b)
    assert not b.exists()
    a.write_text('{"x": 1}')
    br.sync_state(a, b)
    assert b.read_text() == '{"x": 1}'
    br.sync_state(a, a)


def test_main_mocked_writes_results_and_syncs_state(tmp_path, monkeypatch):
    bench, main_root, root = tmp_path / "bench", tmp_path / "main", tmp_path / "wt"
    for d in (bench, main_root, root): d.mkdir()
    _mk(bench, "ids"); _mk(bench, "plan2")
    (main_root / ".freya-agents.json").write_text('{"keep": 1}')
    monkeypatch.setattr(br, "HERE", tmp_path); monkeypatch.setattr(br, "BENCH", bench)
    monkeypatch.setattr(br, "main_checkout", lambda: main_root)
    seen = []

    def build(self, b):
        seen.append(json.loads(self.state.read_text()))
        self.state.write_text('{"keep": 1, "bench-%s": 2}' % b["name"])
        if b["name"] == "plan2": raise RuntimeError("crash")
        return {"seconds": 1, "exit": 0, "artifact": "s"}
    monkeypatch.setattr(br.Runner, "build", build)
    monkeypatch.setattr(br.Runner, "extract", lambda self, b, a: {"spec_errors": 0, "conflicts": 0, "checks": {}})
    monkeypatch.setattr(br.Runner, "sim", lambda self, b, a: {"passed": 8, "total": 8, "failures": []})
    monkeypatch.setattr(br.Runner, "stop", lambda self: seen.append("stopped"))
    assert br.main(["ours", "--repo-root", str(root)]) == 0
    out = list((tmp_path / "results").glob("bench-ours-*.json"))
    assert len(out) == 1 and out[0].with_suffix(".md").exists()
    rows = json.loads(out[0].read_text())["rows"]
    assert [r["name"] for r in rows] == ["ids", "plan2"]
    assert rows[0]["sim"]["passed"] == 8 and rows[1]["errors"][0]["phase"] == "build"
    assert seen[0] == {"keep": 1} and seen[1] == {"keep": 1, "bench-ids": 2} and seen[-1] == "stopped"
    assert json.loads((main_root / ".freya-agents.json").read_text()) == {"keep": 1, "bench-plan2": 2}


class FakePopen:
    behaviour, killed = None, []

    def __init__(self, cmd, **kw):
        self.pid, self.returncode = 4242, None
        self.out_dir = pathlib.Path(cmd[cmd.index("--out-dir") + 1])

    def communicate(self, timeout=None):
        if self.behaviour == "interrupt" and timeout is not None: raise KeyboardInterrupt
        if self.behaviour in ("ok", "fail_with_output"):
            (self.out_dir / "bench-ids.spec.json").write_text("{}")   # a crashed build may still leave a file
        self.returncode = 0 if self.behaviour == "ok" else 1
        return "log line", None


def _runner(tmp_path, monkeypatch, behaviour):
    monkeypatch.setattr(br, "HERE", tmp_path)
    FakePopen.behaviour, FakePopen.killed = behaviour, []
    monkeypatch.setattr(br.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(br.os, "killpg", lambda pid, sig: FakePopen.killed.append((pid, sig)))
    _mk(tmp_path, "ids")
    out = tmp_path / "results" / "bench" / "ids"
    out.mkdir(parents=True)
    (out / "bench-ids.spec.json").write_text('{"stale": true}')
    (out / "old.workflow.json").write_text("{}")
    return br.Runner(br.parse_args(["ours"]), tmp_path, tmp_path), br.discover(tmp_path)[0], out


def test_build_clears_stale_outputs_and_uses_fresh_spec(tmp_path, monkeypatch):
    run, b, out = _runner(tmp_path, monkeypatch, "ok")
    res = run.build(b)
    assert res["exit"] == 0 and res["artifact"] == out / "bench-ids.spec.json"
    assert (out / "bench-ids.spec.json").read_text() == "{}" and not (out / "old.workflow.json").exists()


def test_build_nonzero_exit_has_no_artifact_and_skips_sim(tmp_path, monkeypatch):
    run, b, out = _runner(tmp_path, monkeypatch, "fail")
    res = run.build(b)
    assert res["exit"] == 1 and "artifact" not in res and not (out / "bench-ids.spec.json").exists()
    FakePopen.behaviour = "fail_with_output"
    called = []
    rec = br.run_brief(b, "ours", run.build, lambda b, a: called.append(1), extract_fn=lambda b, a: called.append(2))
    assert rec["exit"] == 1 and rec["sim_skip"] == "no build output" and not called


def test_build_ctrl_c_kills_group_and_reraises(tmp_path, monkeypatch):
    import signal
    run, b, _ = _runner(tmp_path, monkeypatch, "interrupt")
    with pytest.raises(KeyboardInterrupt):
        run.build(b)
    assert FakePopen.killed == [(4242, signal.SIGKILL)]


def test_main_records_interrupted_row(tmp_path, monkeypatch):
    bench, root = tmp_path / "bench", tmp_path / "wt"
    bench.mkdir(); root.mkdir()
    _mk(bench, "ids"); _mk(bench, "sme")
    monkeypatch.setattr(br, "HERE", tmp_path); monkeypatch.setattr(br, "BENCH", bench)
    monkeypatch.setattr(br, "main_checkout", lambda: root)
    monkeypatch.setattr(br.signal, "signal", lambda *a: None)

    def build(self, b): raise KeyboardInterrupt
    stopped = []
    monkeypatch.setattr(br.Runner, "build", build)
    monkeypatch.setattr(br.Runner, "stop", lambda self: stopped.append(1))
    with pytest.raises(KeyboardInterrupt):
        br.main(["ours", "--repo-root", str(root)])
    rows = json.loads(next((tmp_path / "results").glob("bench-ours-*.json")).read_text())["rows"]
    assert [(r["name"], r.get("interrupted")) for r in rows] == [("ids", True)] and stopped


def test_signal_handler_exits_143():
    with pytest.raises(SystemExit) as e:
        br._exit_on_signal(15, None)
    assert e.value.code == 143


def test_load_env_setdefault_even_with_key_set(tmp_path, monkeypatch):
    env = tmp_path / "env.sh"
    env.write_text("export FREYA_API_KEY=from_file\nexport BENCH_TEST_ONLY_VAR=loaded\n")
    monkeypatch.setenv("FREYA_API_KEY", "already")
    monkeypatch.setenv("BENCH_TEST_ONLY_VAR", "x")
    monkeypatch.delenv("BENCH_TEST_ONLY_VAR")
    br.load_env(env)
    assert os.environ["FREYA_API_KEY"] == "already" and os.environ["BENCH_TEST_ONLY_VAR"] == "loaded"
    monkeypatch.delenv("BENCH_TEST_ONLY_VAR")


def test_bank_reset_swaps_openapi_and_drops_sessions():
    from bank.server import Bank

    def oa(v):
        return {"paths": {"/close_call": {"post": {"requestBody": {"content": {"application/json": {"schema": {
            "properties": {"outcome": {"enum": [v]}}}}}}}}}}
    bank = Bank(oa("promise"))
    bank.sessions["x"] = {}
    lock = bank.lock
    bank.reset(oa("paid"))
    assert bank.outcomes == ["paid"] and bank.sessions == {} and bank.lock is lock and not lock.locked()
