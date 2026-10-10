import os

import pytest

from builder.redact import scrub


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("FREYA_") or k.endswith("_KEY") or k.endswith("_TOKEN"):
            monkeypatch.delenv(k)


def test_env_values_replaced(monkeypatch):
    monkeypatch.setenv("FREYA_FAKE", "abcdefgh1234")
    monkeypatch.setenv("SOME_KEY", "zzzzzzzz9999")
    monkeypatch.setenv("MY_TOKEN", "tokentoken1")
    out = scrub("a abcdefgh1234 b zzzzzzzz9999 c tokentoken1")
    assert out == "a [REDACTED:FREYA_FAKE] b [REDACTED:SOME_KEY] c [REDACTED:MY_TOKEN]"


def test_short_values_ignored(monkeypatch):
    monkeypatch.setenv("SHORT_KEY", "abc")
    assert scrub("abc") == "abc"


def test_patterns():
    out = scrub("k fak_AbC123 and sk-ant-api03-x_y-Z end")
    assert out == "k [REDACTED] and [REDACTED] end"
    assert out.count("[REDACTED]") == 2


def test_unrelated_env_untouched(monkeypatch):
    monkeypatch.setenv("OTHER_VAR", "valuevalue1")
    assert scrub("valuevalue1") == "valuevalue1"


def test_fak_with_dash_and_underscore():
    out = scrub("key=fak_ab-cd_EF12-gh tail")
    assert out == "key=[REDACTED] tail"
    assert "fak_" not in out
    assert out.count("[REDACTED]") == 1
