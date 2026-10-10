import os

import pytest

from builder.redact import is_secret_name, scrub


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in list(os.environ):
        if is_secret_name(k):
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


def test_secret_and_password_names(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "secretsecret42")
    monkeypatch.setenv("DB_PASSWORD", "passwordpw4242")
    out = scrub("x secretsecret42 y passwordpw4242")
    assert out == "x [REDACTED:APP_SECRET] y [REDACTED:DB_PASSWORD]"
    assert is_secret_name("APP_SECRET") and is_secret_name("DB_PASSWORD") and not is_secret_name("PATH")


def test_json_escaped_form_scrubbed(monkeypatch):
    import json
    monkeypatch.setenv("WEIRD_KEY", 'ab"cd\\ef/gh')
    text = json.dumps({"v": "pre " + 'ab"cd\\ef/gh' + " post"})
    out = scrub(text)
    assert out.count("[REDACTED:WEIRD_KEY]") == 1
    assert out.count("cd") == 0


def test_fak_with_dash_and_underscore():
    out = scrub("key=fak_ab-cd_EF12-gh tail")
    assert out == "key=[REDACTED] tail"
    assert "fak_" not in out
    assert out.count("[REDACTED]") == 1
