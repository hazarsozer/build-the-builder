"""S3: every helper that cuts text scrubs first, so a secret straddling the cut leaves no prefix behind (probe_redact.py)."""
import json
import os

import pytest

from builder import fix, sdk
from builder.redact import is_secret_name
from builder.trace import Trace, _cell

SECRET = "plainTOKENvalue123456"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in list(os.environ):
        if is_secret_name(k):
            monkeypatch.delenv(k)
    monkeypatch.setenv("PLAIN_TOKEN", SECRET)


def fragments(text):
    """Count of any 6+ character prefix of the secret in text (0 means no form survived)."""
    return sum(text.count(SECRET[:k]) for k in range(6, len(SECRET) + 1))


def test_encoded_forms_scrubbed_in_trace_lines(tmp_path, monkeypatch):
    """probe_redact.py: ASCII-escaped, double-JSON, URL-quoted and bytes-repr forms of a secret."""
    import urllib.parse
    odd = 'Ab9"q\\Zé-Long/Secret'
    monkeypatch.setenv("X_TOKEN", odd)
    t = Trace(tmp_path / "t.jsonl")
    for v in (json.dumps(odd, ensure_ascii=True), json.dumps(json.dumps({"k": odd})), urllib.parse.quote(odd),
              repr(odd.encode())):
        t.event("probe", v=v)
    t.close()
    raw = (tmp_path / "t.jsonl").read_text()
    for frag in ("Long/Secret", "Long\\/Secret", "\\u00e9-Long", "\\xc3\\xa9-Long", "Long%2FSecret"):
        assert raw.count(frag) == 0
    assert raw.count("[REDACTED:X_TOKEN]") == 4


@pytest.mark.parametrize("pad", [fix.MESSAGE_CHARS - 10, fix.MESSAGE_CHARS - 5, fix.MESSAGE_CHARS - 1])
def test_trim_transcript_cut_boundary(pad):
    out = fix.trim_transcript([{"role": "user", "message": "x" * pad + SECRET}])
    assert fragments(json.dumps(out)) == 0


def test_trim_transcript_non_message_fields():
    out = fix.trim_transcript([{"role": "tool", "content": "y" * (fix.MESSAGE_CHARS - 20) + SECRET}])
    assert fragments(json.dumps(out)) == 0


@pytest.mark.parametrize("pad", [390, 395, 399])
def test_sdk_short_cut_boundary(pad):
    assert fragments(sdk._short("y" * pad + SECRET, 400)) == 0
    assert fragments(sdk._short({"k": "y" * pad + SECRET}, 400)) == 0


@pytest.mark.parametrize("pad", [150, 155, 159])
def test_trace_cell_and_markdown_cut_boundary(tmp_path, pad):
    assert fragments(_cell("z" * pad + SECRET)) == 0
    t = Trace(tmp_path / "t.jsonl")
    t.event("start", briefs=["z" * pad + SECRET], slot="s")
    t.model_call("m", "w" * pad + SECRET)
    md = t.write_markdown().read_text()
    t.close()
    assert fragments(md) == 0
    assert fragments((tmp_path / "t.jsonl").read_text()) == 0
