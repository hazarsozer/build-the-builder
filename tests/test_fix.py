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


def test_quoted_change_applied_and_failures_written():
    assert QUOTE in BRIEF_TEXT

    def script(cwd, prompt, n):
        failures = json.loads((cwd / "failures.json").read_text())
        assert [f["id"] for f in failures] == ["SELF-02"]
        assert failures[0]["expected"] == {"outcome": "no_promise"}
        assert len(json.dumps(failures[0]["transcript"])) < fix.TRANSCRIPT_CHARS + 500
        assert failures[0]["transcript"][-1] == "user: bye"
        (cwd / "fix.json").write_text(json.dumps({"decisions": [
            {"path": "bank_name", "value": "Fixed Bank", "quote": QUOTE, "why": "SELF-02"}]}))
    (spec, info), log, trace = run(script)
    assert spec["bank_name"] == "Fixed Bank"
    assert A["bank_name"] != "Fixed Bank"                       # input not mutated
    assert info["changes"] == [{"path": "bank_name", "quote": QUOTE, "why": "SELF-02"}]
    assert info["errors"] == 0
    assert trace.calls and trace.calls[0][1].startswith("fix")
    opts = log[0][1]
    assert opts.setting_sources == [] and opts.strict_mcp_config is True and "Bash" not in opts.allowed_tools


def test_unquoted_or_unfound_quote_discarded():
    def script(cwd, prompt, n):
        (cwd / "fix.json").write_text(json.dumps({"decisions": [
            {"path": "bank_name", "value": "X"},
            {"path": "assistant_identity", "value": "Y", "quote": "words that are not in the brief at all zzq"}]}))
    (spec, info), _, _ = run(script)
    assert spec == A
    assert info["changes"] == [] and len(info["discarded"]) == 2


def test_change_that_breaks_validation_is_repaired_or_dropped():
    def script(cwd, prompt, n):
        val = "not-a-list" if n == 0 else "Fixed Bank"
        path = "outcomes" if n == 0 else "bank_name"
        (cwd / "fix.json").write_text(json.dumps({"decisions": [{"path": path, "value": val, "quote": QUOTE}]}))
    (spec, info), log, _ = run(script)
    assert len([q for q in log if q[0] == "query"]) == 2
    assert spec["bank_name"] == "Fixed Bank" and spec["outcomes"] == A["outcomes"]
    assert info["errors"] == 0


def test_still_invalid_after_repair_keeps_original():
    def script(cwd, prompt, n):
        (cwd / "fix.json").write_text(json.dumps({"decisions": [{"path": "outcomes", "value": "bad", "quote": QUOTE}]}))
    (spec, info), _, _ = run(script)
    assert spec == A and info.get("kept_original")


def test_get_call_failure_tolerated():
    def boom(cid):
        raise RuntimeError("down")

    def script(cwd, prompt, n):
        f = json.loads((cwd / "failures.json").read_text())
        assert f[0]["transcript"] == []
        (cwd / "fix.json").write_text(json.dumps({"decisions": []}))
    (spec, info), _, _ = run(script, get_call=boom)
    assert spec == A and info["changes"] == []
