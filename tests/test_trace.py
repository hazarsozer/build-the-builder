import json
import os

import pytest

from builder.trace import Trace


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("FREYA_") or k.endswith("_KEY") or k.endswith("_TOKEN"):
            monkeypatch.delenv(k)


def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_event_and_model_call_lines(tmp_path):
    t = Trace(tmp_path / "t.jsonl")
    t.event("start", briefs=["a.md"])
    t.model_call("claude-opus-5-5", "extract:a#1")
    t.close()
    rows = _lines(tmp_path / "t.jsonl")
    assert [r["kind"] for r in rows] == ["start", "model_call"]
    assert rows[1]["model"] == "claude-opus-5-5" and rows[1]["purpose"] == "extract:a#1"
    assert all("t" in r and "s" in r for r in rows)


def test_every_line_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("FREYA_API_KEY", "secretvalue12345")
    t = Trace(tmp_path / "t.jsonl")
    t.event("x", note="key secretvalue12345 and fak_ab-cd_9 and sk-ant-zz_1")
    t.close()
    raw = (tmp_path / "t.jsonl").read_text()
    assert raw.count("secretvalue12345") == 0
    assert raw.count("fak_") == 0 and raw.count("sk-ant-") == 0
    assert raw.count("[REDACTED") == 3


def test_non_string_fields_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("OTHER_KEY", "nestedsecret777")
    t = Trace(tmp_path / "t.jsonl")
    t.event("x", rows=[{"why": "nestedsecret777"}], n=3)
    t.close()
    raw = (tmp_path / "t.jsonl").read_text()
    assert raw.count("nestedsecret777") == 0
    assert _lines(tmp_path / "t.jsonl")[0]["n"] == 3


def test_markdown_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_KEY", "mdsecret99999")
    t = Trace(tmp_path / "t.jsonl")
    t.event("start", briefs=["b.md"], git_commit="abc", model="m")
    t.model_call("m", "fix round mdsecret99999")
    t.event("saved", slot="s", workflow_id="w1", agent_id="a1", url_kind="official")
    t.event("selftest_round", round=1, passed=9, total=11, failing=["SELF-02", "SELF-07"])
    t.event("export", path="submission/s.workflow.json")
    t.event("end", duration_s=12.5, ok=True)
    md = t.write_markdown()
    text = md.read_text()
    assert md.suffix == ".md"
    assert "w1" in text and "a1" in text and "9/11" in text and "SELF-02" in text
    assert "submission/s.workflow.json" in text
    assert text.count("mdsecret99999") == 0


def test_model_call_gets_one_line_description(tmp_path):
    t = Trace(tmp_path / "t.jsonl")
    for p in ("extract:a#1", "extract:b#2", "oracle#1", "settle#1", "fix:round1#1", "other"):
        t.model_call("m", p)
    t.close()
    about = [r["about"] for r in _lines(tmp_path / "t.jsonl")]
    assert "extraction a" in about[0] and "repair" in about[1]
    assert "scenarios" in about[2] and "settle" in about[3].lower() and "self-test" in about[4]
    assert about[5] == "other"
