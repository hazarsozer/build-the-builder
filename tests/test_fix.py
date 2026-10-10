"""LLM fix round on the spec, with a fake Agent SDK client (no model calls)."""
import asyncio
import copy
import json
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from builder import fix, sdk

ROOT = Path(__file__).resolve().parent.parent
OA = json.load(open(ROOT / "tools/openapi.json"))
A = json.load(open(ROOT / "specs/reference/spec-a.json"))
BRIEF = [str(ROOT / "briefs/brief-a.md")]
BRIEF_TEXT = (ROOT / "briefs/brief-a.md").read_text()
QUOTE = "Address the customer as title plus last name."
Q2 = "If the customer says twice that they do not want to talk"
PATH = "negotiation.first_ask"
NEW = "Could you pay this today?"
CASES = [{"id": "SELF-01", "scenario": "happy_full_today", "title": "pays", "script": "s", "checks": ["c"],
          "expected": {"outcome": "promise"}},
         {"id": "SELF-02", "scenario": "callback", "title": "cb", "script": "s2", "checks": ["c2"],
          "expected": {"outcome": "no_promise"}}]
RESULT = {"passed": 1, "total": 2, "rows": [
    {"id": "SELF-01", "pass": True, "failed": [], "call_id": "c1", "tools": []},
    {"id": "SELF-02", "pass": False, "failed": ["outcome: expected no_promise"], "call_id": "c2", "warnings": [],
     "tools": ["verify_identity"]}]}


class FakeClient:
    def __init__(self, options, script, log):
        self.options, self.cwd, self.script, self.log, self.n = options, Path(options.cwd), script, log, 0

    async def connect(self):
        self.log.append(("connect", self.options))

    async def disconnect(self):
        pass

    async def interrupt(self):
        pass

    async def query(self, prompt):
        self.log.append(("query", prompt))
        self.script(self.cwd, prompt, self.n)
        self.n += 1

    async def receive_response(self):
        yield AssistantMessage(content=[TextBlock(text="done")], model="fake")
        yield ResultMessage(subtype="success", duration_ms=10, duration_api_ms=9, is_error=False, num_turns=1,
                            session_id="s", total_cost_usd=0.1, result="done")


@pytest.fixture(autouse=True)
def work_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sdk, "WORK_ROOT", tmp_path / ".work")


def run(script, get_call=None, spec=None):
    log, trace = [], sdk.NullTrace()
    out = asyncio.run(fix.fix_async(spec or A, RESULT, CASES, BRIEF, OA, trace,
                                    client_factory=lambda o: FakeClient(o, script, log),
                                    get_call=get_call or (lambda cid: {"messages": [
                                        {"role": "assistant", "message": "Hello " * 3000},
                                        {"role": "user", "message": "bye"}]})))
    return out, log, trace


def dec(path=PATH, value=NEW, quote=QUOTE, **k):
    return dict(path=path, value=value, quote=quote, **k)


def write_fix(cwd, *decisions):
    (cwd / "fix.json").write_text(json.dumps({"decisions": list(decisions)}))


def test_quoted_change_applied_and_failures_written():
    assert QUOTE in BRIEF_TEXT and Q2 in BRIEF_TEXT

    def script(cwd, prompt, n):
        failures = json.loads((cwd / "failures.json").read_text())
        assert [f["id"] for f in failures] == ["SELF-02"]
        assert failures[0]["expected"] == {"outcome": "no_promise"}
        assert len(json.dumps(failures[0]["transcript"])) < fix.TRANSCRIPT_CHARS + 500
        assert failures[0]["transcript"][-1] == "user: bye"
        write_fix(cwd, dec(why="SELF-02"))
    (spec, info), log, trace = run(script)
    assert spec["negotiation"]["first_ask"] == NEW
    assert A["negotiation"]["first_ask"] != NEW                     # input not mutated
    [c] = info["changes"]
    assert (c["path"], c["quote"], c["why"]) == (PATH, QUOTE, "SELF-02")
    assert c["before"] == A["negotiation"]["first_ask"] and c["after"] == NEW
    assert info["errors"] == 0 and info["sandbox"] == []
    assert trace.calls and trace.calls[0][1].startswith("fix")
    opts = log[0][1]
    assert opts.setting_sources == [] and opts.strict_mcp_config is True and "Bash" not in opts.allowed_tools
    assert opts.hooks and "PreToolUse" in opts.hooks


def test_invented_quote_does_not_ride_on_a_real_one_same_path():
    """C1 regression: two decisions on one path, one quoted truthfully, one invented -> whole fix.json rejected."""
    def script(cwd, prompt, n):
        write_fix(cwd, dec(value="real"), dec(value="invented", quote="the brief never says these words zzq"))
    (spec, info), _, _ = run(script)
    assert spec == A and info["changes"] == []
    assert "same path" in info["discarded"][0]["why"]


def test_decisions_kept_by_index_not_path():
    def script(cwd, prompt, n):
        write_fix(cwd, dec(path="opening.confirm_question", value="x", quote="these words are not in the brief zzq"),
                  dec())
    (spec, info), _, _ = run(script)
    assert spec["opening"]["confirm_question"] == A["opening"]["confirm_question"]
    assert spec["negotiation"]["first_ask"] == NEW
    assert [d["index"] for d in info["discarded"]] == [0]


def test_unquoted_short_or_unfound_quote_discarded():
    def script(cwd, prompt, n):
        write_fix(cwd, dec(quote=""),
                  dec(path="opening.confirm_question", quote="title plus last"),
                  dec(path="negotiation.debt_statement", quote="words that are not in the brief at all zzq"))
    (spec, info), _, _ = run(script)
    assert spec == A
    assert info["changes"] == [] and len(info["discarded"]) == 3


def test_top_level_paths_rejected_except_outcomes():
    def script(cwd, prompt, n):
        write_fix(cwd, dec(path="bank_name", value="Other"), dec(path="outcomes", value=A["outcomes"]))
    (spec, info), _, _ = run(script)
    assert spec["bank_name"] == A["bank_name"]
    assert [c["path"] for c in info["changes"]] == ["outcomes"]
    assert "top-level" in info["discarded"][0]["why"]


def test_cap_six_decisions():
    paths = ["opening.confirm_accept", "opening.confirm_reject", "opening.banned_before_verify",
             "negotiation.first_ask", "negotiation.debt_statement", "opening.confirm_question", "opening.first_sentence"]

    def script(cwd, prompt, n):
        write_fix(cwd, *[dec(path=p, value=copy.deepcopy(fix.resolve_path(A, p)[1])) for p in paths])
    (spec, info), _, _ = run(script)
    assert len(info["changes"]) == fix.MAX_DECISIONS
    assert info["discarded"][-1]["index"] == 6 and "cap" in info["discarded"][-1]["why"]


def test_literal_missing_from_quote_warns():
    def script(cwd, prompt, n):
        write_fix(cwd, dec(path="opening.max_asks", value=7, quote=Q2),
                  dec(path="opening.confirm_accept", value=["yes", "twice"], quote=Q2))
    (spec, info), _, _ = run(script)
    assert spec["opening"]["max_asks"] == 7
    lits = {(w["path"], w["literal"]) for w in info["warnings"]}
    assert ("opening.max_asks", "7") in lits
    assert not any(w["literal"] == "twice" for w in info["warnings"])      # in the quote


def test_change_that_breaks_validation_is_repaired_or_dropped():
    def script(cwd, prompt, n):
        write_fix(cwd, dec(path="outcomes", value="not-a-list") if n == 0 else dec())
    (spec, info), log, _ = run(script)
    assert len([q for q in log if q[0] == "query"]) == 2
    assert spec["negotiation"]["first_ask"] == NEW and spec["outcomes"] == A["outcomes"]
    assert info["errors"] == 0


def test_still_invalid_after_repair_keeps_original():
    def script(cwd, prompt, n):
        write_fix(cwd, dec(path="outcomes", value="bad"))
    (spec, info), _, _ = run(script)
    assert spec == A and info.get("kept_original")


def test_get_call_failure_tolerated():
    def boom(cid):
        raise RuntimeError("down")

    def script(cwd, prompt, n):
        f = json.loads((cwd / "failures.json").read_text())
        assert f[0]["transcript"] == []
        write_fix(cwd)
    (spec, info), _, _ = run(script, get_call=boom)
    assert spec == A and info["changes"] == []


def test_sandbox_reports_other_files_and_ignores_symlinked_fix(tmp_path):
    outside = tmp_path / "elsewhere.json"
    outside.write_text(json.dumps({"decisions": [dec()]}))

    def script(cwd, prompt, n):
        if n == 0:
            (cwd / "spec.json").write_text("{}")
            (cwd / "new.txt").write_text("x")
            (cwd / "fix.json").symlink_to(outside)
    log, trace = [], sdk.NullTrace()
    spec, info = asyncio.run(fix.fix_async(A, RESULT, CASES, BRIEF, OA, trace,
                                           client_factory=lambda o: FakeClient(o, script, log),
                                           get_call=lambda cid: {}))
    assert spec == A and info["changes"] == []
    assert {(c["path"], c["change"]) for c in info["sandbox"]} == {("spec.json", "modified"), ("new.txt", "created")}
    assert [k for k, _ in trace.events if k == "fix_sandbox"]
    assert any("not a plain file" in str(d.get("why")) for d in info["discarded"])


def test_confine_hook_denies_outside_paths(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    hook = sdk.confine_hook(work)
    call = lambda name, ti: asyncio.run(hook({"tool_name": name, "tool_input": ti}, "t", None))
    deny = lambda r: (r.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"
    assert not deny(call("Write", {"file_path": "fix.json"}))
    assert not deny(call("Read", {"file_path": str(work / "spec.json")}))
    assert deny(call("Read", {"file_path": "../env.sh"}))
    assert deny(call("Read", {"file_path": "/etc/passwd"}))
    assert deny(call("Read", {"file_path": "~/.ssh/id_rsa"}))
    assert deny(call("Glob", {"pattern": "../**/*"}))
    assert deny(call("Glob", {"pattern": "*", "path": "/"}))
    (work / "link").symlink_to(tmp_path)
    assert deny(call("Read", {"file_path": "link/x"}))


def test_extractor_sessions_not_confined(tmp_path):
    assert getattr(sdk.options_for(tmp_path, "m"), "hooks", None) in (None, {})
