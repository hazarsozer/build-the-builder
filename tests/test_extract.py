"""Extractor plumbing with a fake Agent SDK client (no model calls)."""
import asyncio
import time
import copy
import json
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from builder import extract as ex
from builder import sdk

ROOT = Path(__file__).resolve().parent.parent
OA = json.load(open(ROOT / "tools/openapi.json"))
A = json.load(open(ROOT / "specs/reference/spec-a.json"))
BRIEF = [str(ROOT / "briefs/brief-a.md")]
FACTS = [{"id": "f1", "quote": "Ask \"Am I speaking with {title} {last_name}?\"", "maps_to": "opening.confirm_question"}]


class FakeClient:
    """Stands in for ClaudeSDKClient. `script(kind, cwd, prompt, n)` writes the files a model would."""

    def __init__(self, options, script, log):
        self.options = options
        self.cwd = Path(options.cwd)
        self.script = script
        self.log = log
        self.n = 0
        self.say = "ok"
        self.delay = 0.0      # seconds before the result, set by a script
        self.is_error = False

    async def connect(self):
        self.log.append(("connect", self.kind(), self.options))

    async def disconnect(self):
        pass

    async def interrupt(self):
        pass

    def kind(self):
        name = self.cwd.name
        for k in ("extract-a", "extract-b", "oracle", "settle"):
            if k in name:
                return k
        return "?"

    async def query(self, prompt):
        self.log.append(("query", self.kind(), prompt))
        self.script(self.kind(), self.cwd, prompt, self.n, self)
        self.n += 1

    async def receive_response(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        yield AssistantMessage(content=[ToolUseBlock(id="t1", name="Write", input={"file_path": "spec.json"}),
                                        TextBlock(text=self.say)], model="fake")
        yield ResultMessage(subtype="success", duration_ms=1000, duration_api_ms=900, is_error=self.is_error,
                            num_turns=2, session_id="s", total_cost_usd=0.5,
                            result="api error" if self.is_error else None)


def factory(script, log):
    return lambda options: FakeClient(options, script, log)


def write(cwd, name, obj):
    (cwd / name).write_text(json.dumps(obj))


@pytest.fixture(autouse=True)
def work_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sdk, "WORK_ROOT", tmp_path / ".work")
    return tmp_path / ".work"


def run(script, log, **kw):
    trace = sdk.NullTrace()
    spec, errs, info = asyncio.run(ex.extract_async(BRIEF, OA, trace, client_factory=factory(script, log), **kw))
    return spec, errs, info, trace


# --------------------------------------------------------------------------- tests

def test_single_extraction_clean():
    def script(kind, cwd, prompt, n, c):
        write(cwd, "facts.json", FACTS)
        write(cwd, "spec.json", A)
    log = []
    spec, errs, info, trace = run(script, log, dual=False, use_oracle=False)
    assert errs == []
    assert spec["bands"] == A["bands"] and spec["facts"] == FACTS
    assert info["extract"]["a"]["errors_per_round"] == [0]
    assert info["diff_paths"] == [] and "settle" not in info
    assert [q for q in log if q[0] == "query"][0][1] == "extract-a"


def test_repair_loop_feeds_errors_back_in_same_session():
    bad = copy.deepcopy(A)
    bad["outcomes"][0]["outcome"] = "promised"

    def script(kind, cwd, prompt, n, c):
        write(cwd, "facts.json", FACTS)
        write(cwd, "spec.json", bad if n == 0 else A)
    log = []
    spec, errs, info, _ = run(script, log, dual=False, use_oracle=False)
    assert errs == []
    assert info["extract"]["a"]["errors_per_round"][0] > 0 and info["extract"]["a"]["errors_per_round"][-1] == 0
    queries = [q for q in log if q[0] == "query"]
    assert len([q for q in log if q[0] == "connect"]) == 1   # one session, continued
    assert "promised" in queries[1][2]


def test_unmapped_fact_is_a_validation_error_and_rounds_are_capped():
    facts = FACTS + [{"id": "f2", "quote": "x", "maps_to": "bands[9].deadline"},
                     {"id": "f3", "quote": "y", "maps_to": ""}]

    def script(kind, cwd, prompt, n, c):
        write(cwd, "facts.json", facts)
        write(cwd, "spec.json", A)
    log = []
    spec, errs, info, _ = run(script, log, dual=False, use_oracle=False)
    assert any("facts[1]" in e for e in errs) and any("facts[2]" in e for e in errs)
    assert len(info["extract"]["a"]["errors_per_round"]) == 1 + ex.REPAIR_ROUNDS
    assert "bands[9].deadline" in [q for q in log if q[0] == "query"][1][2]


def test_missing_spec_reported():
    log = []
    spec, errs, info, _ = run(lambda *a: None, log, dual=False, use_oracle=False)
    assert spec is None and errs and "spec.json was not written" in str(errs)


def test_dual_disagreement_settled_with_quote():
    b = copy.deepcopy(A)
    [e for e in b["events"] if e["id"] == "human"][0]["queue"] = "support"
    b["opening"]["banned_before_verify"] = ["debt", "overdue"]   # union, no settle needed

    def script(kind, cwd, prompt, n, c):
        if kind.startswith("extract"):
            write(cwd, "facts.json", FACTS)
            write(cwd, "spec.json", A if kind == "extract-a" else b)
        elif kind == "settle":
            q = json.loads((cwd / "questions.json").read_text())
            assert [d["path"] for d in q["disagreements"]] == ["events[human].queue"]
            # a's value is right, but settle may restate it; one decision lacks a quote and is dropped
            write(cwd, "settle.json", {"decisions": [
                {"path": "events[human].queue", "value": "general", "quote": "call `transfer_to_agent` with queue `general`"},
                {"path": "bank_name", "value": "Other Bank", "quote": ""}]})
    log = []
    spec, errs, info, _ = run(script, log, use_oracle=False)
    assert errs == []
    assert sorted(info["diff_paths"]) == ["events[human].queue", "opening.banned_before_verify"]
    assert info["open_diffs"] == ["events[human].queue"]
    assert [d["path"] for d in info["settle_decisions"]] == ["events[human].queue"]
    assert info["settle_decisions"][0]["quote_found"] is True
    assert info["settle"]["skipped"][0]["path"] == "bank_name"
    assert spec["bank_name"] == A["bank_name"]
    assert "overdue" in spec["opening"]["banned_before_verify"]
    assert [e for e in spec["events"] if e["id"] == "human"][0]["queue"] == "general"


def test_oracle_mismatch_goes_to_settle_and_can_be_rejected():
    scen = [{"id": "s1", "now": "2026-10-14T11:00",
             "products": [{"product_id": "p1", "type": "credit_card", "past_due": 400.0, "days_late": 20,
                           "minimum_payment": 40.0, "card_last4": "1234"}],
             "customer_says": "...", "inputs": {},
             "expected": {"outcome": "promise", "reason": None, "promises": [], "notes": [], "callbacks": [],
                          "transfers": [], "deadlines": {"p1": "2026-10-21"}},
             "quote": "the 7th business day after today"}]

    def script(kind, cwd, prompt, n, c):
        if kind.startswith("extract"):
            write(cwd, "facts.json", FACTS)
            write(cwd, "spec.json", A)
        elif kind == "oracle":
            assert not (cwd / "spec_schema.json").exists()
            write(cwd, "scenarios.json", scen)
        elif kind == "settle":
            q = json.loads((cwd / "questions.json").read_text())
            assert q["oracle_mismatches"][0]["field"] == "deadline"
            write(cwd, "settle.json", {"decisions": [], "rejected": [
                {"scenario": "s1", "quote": "the 7th business day after today", "why": "examiner miscounted"}]})
    log = []
    spec, errs, info, _ = run(script, log)
    assert errs == []
    assert [m["field"] for m in info["oracle_mismatches"]] == ["deadline"]
    assert info["rejected_scenarios"][0]["scenario"] == "s1"
    assert info["oracle_mismatches_after"] == []
    assert spec["bands"] == A["bands"]


def test_oracle_mismatch_fixed_in_spec():
    """The settle session may change a value the two readings agreed on, citing a quote."""
    scen = [{"id": "s1", "now": "2026-10-14T11:00",
             "products": [{"product_id": "p1", "type": "credit_card", "past_due": 400.0, "days_late": 20,
                           "minimum_payment": 40.0}],
             "customer_says": "...", "inputs": {},
             "expected": {"outcome": None, "reason": None, "deadlines": {"p1": "2026-10-21"}}, "quote": "5 business days"}]
    fixed_band = copy.deepcopy(A["bands"][1])
    fixed_band["deadline"]["n"] = 5
    spec_ex = copy.deepcopy(A)
    spec_ex["examples"] = [e for e in A["examples"] if e["days_late"] < 8]

    def script(kind, cwd, prompt, n, c):
        if kind.startswith("extract"):
            write(cwd, "facts.json", FACTS)
            write(cwd, "spec.json", spec_ex)
        elif kind == "oracle":
            write(cwd, "scenarios.json", scen)
        elif kind == "settle":
            write(cwd, "settle.json", {"decisions": [
                {"path": "bands[1].deadline.n", "value": 5, "quote": "memo: 5 business days"}]})
    spec, errs, info, _ = run(script, [])
    assert errs == []
    assert spec["bands"][1]["deadline"]["n"] == 5
    assert info["oracle_mismatches_after"] == []


def test_merge_that_breaks_validation_falls_back_to_base():
    b = copy.deepcopy(A)
    [e for e in b["events"] if e["id"] == "human"][0]["queue"] = "support"

    def script(kind, cwd, prompt, n, c):
        if kind.startswith("extract"):
            write(cwd, "facts.json", FACTS)
            write(cwd, "spec.json", A if kind == "extract-a" else b)
        elif kind == "settle":
            write(cwd, "settle.json", {"decisions": [
                {"path": "events[human].queue", "value": "nowhere", "quote": "queue"}]})
    spec, errs, info, _ = run(script, [], use_oracle=False)
    assert errs == []
    assert info["merge_rejected_errors"]
    assert [e for e in spec["events"] if e["id"] == "human"][0]["queue"] == "general"
    assert len(info["settle"]["settle_rounds"]) == ex.SETTLE_ROUNDS


def test_one_extraction_crashing_uses_the_other():
    def script(kind, cwd, prompt, n, c):
        if kind == "extract-b":
            raise RuntimeError("boom")
        write(cwd, "facts.json", FACTS)
        write(cwd, "spec.json", A)
    spec, errs, info, trace = run(script, [], use_oracle=False)
    assert errs == [] and spec is not None
    assert "boom" in info["failures"]["b"]
    assert any(k == "extract_failed" for k, _ in trace.events)


def test_session_is_locked_down(work_root, monkeypatch):
    monkeypatch.setenv("BUILDER_MODEL", "claude-test-model")

    def script(kind, cwd, prompt, n, c):
        write(cwd, "facts.json", FACTS)
        write(cwd, "spec.json", A)
    log = []
    run(script, log, use_oracle=False)
    opts = [o for k, kind, o in log if k == "connect"]
    assert len(opts) == 2
    for o in opts:
        assert o.model == "claude-test-model"
        assert o.tools == ["Read", "Write", "Edit", "Glob"] and o.allowed_tools == ["Read", "Write", "Edit", "Glob"]
        assert "Bash" in o.disallowed_tools and any("env.sh" in d for d in o.disallowed_tools)
        assert o.permission_mode == "dontAsk"
        assert o.setting_sources == []
        assert o.strict_mcp_config is True and o.mcp_servers == {}
        cwd = Path(o.cwd)
        assert cwd.parent == work_root
        names = sorted(p.name for p in cwd.iterdir() if not p.name.startswith("."))
        assert {"brief-a.md", "openapi.json", "spec_schema.json", "spec-guide.md"} <= set(names)
        assert not any("env" in n and n.endswith(".sh") for n in names)


def test_trace_lines_are_scrubbed(monkeypatch):
    monkeypatch.setenv("FREYA_API_KEY", "fak_supersecretvalue123")

    def script(kind, cwd, prompt, n, c):
        c.say = "I saw fak_supersecretvalue123 somewhere"
        write(cwd, "facts.json", FACTS)
        write(cwd, "spec.json", A)
    spec, errs, info, trace = run(script, [], dual=False, use_oracle=False)
    dump = json.dumps(trace.events, default=str)
    assert "supersecret" not in dump
    assert "REDACTED" in dump
    assert trace.calls and trace.calls[0][1].startswith("extract:a")


def test_prompts_carry_no_brief_facts():
    """The prompts must be general: no bank names, enum values or numbers from Brief A/B."""
    text = " ".join(p.read_text() for p in (ROOT / "builder/prompts").glob("*.md")).lower()
    for banned in ["freya", "credit_card", "minimum_payment", "instalment", "phone_last4", "date_of_birth",
                   "collections team", "restructuring", "not_reached", "no_promise", "2026", "12th", "7th",
                   "day_month_swapped", "mobile app", "text message"]:
        assert banned not in text, banned


def test_apply_settle_remove_and_append():
    spec, applied, skipped, rejected = ex.apply_settle(A, {"decisions": [
        {"path": "events[unclear]", "remove": True, "quote": "q"},
        {"path": "unused_outcomes", "value": [], "quote": "q"}]}, "q")
    assert not any(e["id"] == "unclear" for e in spec["events"])
    assert spec["unused_outcomes"] == []
    assert applied[0]["removed"] is True and skipped == [] and rejected == []


# --------------------------------------------------------------------------- fix round 1: budget, recovery

def _human_queue(spec, q):
    s = copy.deepcopy(spec)
    [e for e in s["events"] if e["id"] == "human"][0]["queue"] = q
    return s


def test_second_extraction_gets_bounded_wait_and_its_disk_spec_is_kept(monkeypatch):
    monkeypatch.setattr(ex, "SECOND_WAIT_S", 0.3)
    b = _human_queue(A, "support")

    def script(kind, cwd, prompt, n, c):
        if kind.startswith("extract"):
            write(cwd, "facts.json", FACTS)
            write(cwd, "spec.json", A if kind == "extract-a" else b)
            if kind == "extract-b":
                c.delay = 30          # wrote its spec, then hangs
        elif kind == "settle":
            write(cwd, "settle.json", {"decisions": []})
    t0 = time.monotonic()
    spec, errs, info, trace = run(script, [], use_oracle=False)
    assert time.monotonic() - t0 < 5
    assert errs == []
    assert info["failures"]["b"] == "cancelled (time budget)"
    assert info["extract"]["b"]["interrupted"] == "cancelled (time budget)"
    assert info["diff_paths"] == ["events[human].queue"]       # b's spec was read back from disk
    assert any(k == "extract_interrupted" for k, _ in trace.events)


def test_phase_deadline_cuts_everything_and_skips_settle():
    b = _human_queue(A, "support")

    def script(kind, cwd, prompt, n, c):
        write(cwd, "facts.json", FACTS)
        write(cwd, "spec.json", A if kind == "extract-a" else b)
        c.delay = 30
    t0 = time.monotonic()
    spec, errs, info, _ = run(script, [], use_oracle=False, deadline_s=0.5)
    assert time.monotonic() - t0 < 5
    assert errs == [] and spec is not None
    assert set(info["failures"]) == {"a", "b"}
    assert "settle_skipped" in info and "settle" not in info
    assert info["open_diffs"] == ["events[human].queue"]


def test_session_error_keeps_spec_on_disk():
    def script(kind, cwd, prompt, n, c):
        write(cwd, "facts.json", FACTS)
        write(cwd, "spec.json", A)
        c.is_error = True
    spec, errs, info, _ = run(script, [], dual=False, use_oracle=False)
    assert errs == [] and spec["bands"] == A["bands"]
    assert "SessionError" in info["extract"]["a"]["interrupted"]


def test_no_candidates_falls_back_to_workdirs(monkeypatch):
    async def broken(brief_paths, openapi, trace, label, client_factory=None, workdirs=None):
        work = sdk.make_workdir(brief_paths, openapi, "extract-" + label)
        workdirs[label] = work
        write(work, "facts.json", FACTS)
        write(work, "spec.json", A)
        raise RuntimeError("crashed after writing")
    monkeypatch.setattr(ex, "extract_one", broken)
    spec, errs, info, trace = run(lambda *a: None, [], use_oracle=False)
    assert errs == [] and spec["bands"] == A["bands"]
    assert info["extract"]["a"]["recovered_from_disk"] is True
    assert any(k == "extract_recovered" for k, _ in trace.events)


def test_fallback_keeps_spec_with_fewer_errors():
    one_err = copy.deepcopy(A)
    one_err["examples"][0]["expect"] = "2026-11-03"          # one failing example, never repaired

    def script(kind, cwd, prompt, n, c):
        if kind.startswith("extract"):
            write(cwd, "facts.json", FACTS)
            write(cwd, "spec.json", one_err)
        elif kind == "settle":
            write(cwd, "settle.json", {"decisions": [
                {"path": "events[human].queue", "value": "nowhere", "quote": "queue"}]})
    spec, errs, info, _ = run(script, [], use_oracle=False)
    assert len(errs) == 1 and "example[0]" in errs[0]
    assert len(info["merge_rejected_errors"]) > 1
    assert [e for e in spec["events"] if e["id"] == "human"][0]["queue"] == "general"


def test_settle_budget_is_enforced(monkeypatch):
    monkeypatch.setattr(ex, "SETTLE_S", 0.3)
    monkeypatch.setattr(ex, "SETTLE_MIN_S", 0.1)
    b = _human_queue(A, "support")

    def script(kind, cwd, prompt, n, c):
        if kind.startswith("extract"):
            write(cwd, "facts.json", FACTS)
            write(cwd, "spec.json", A if kind == "extract-a" else b)
        elif kind == "settle":
            c.delay = 30
    t0 = time.monotonic()
    spec, errs, info, _ = run(script, [], use_oracle=False)
    assert time.monotonic() - t0 < 5
    assert "timed out" in info["settle_error"]
    assert errs == [] and spec["bank_name"] == A["bank_name"]


def test_brief_dirs_never_copy_secret_files(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    for name in ("mail.md", "env.sh", ".env", ".env.local", "openapi.json", "codes.csv"):
        (d / name).write_text("x")
    assert [p.name for p in sdk.expand_briefs([d])] == ["codes.csv", "mail.md"]
    assert sdk.expand_briefs([d / "env.sh", d / ".env.local", d / "mail.md"]) == [d / "mail.md"]
