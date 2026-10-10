"""Scrub secrets from text before it is logged, printed or written.

Env vars named FREYA_* or *_KEY / *_TOKEN / *_SECRET / *_PASSWORD (values of 8+ characters) are replaced by
[REDACTED:NAME], as written and in the encodings a line can carry them in (JSON-escaped once or twice, UTF-8 or
ASCII; URL-quoted; bytes repr), so a value with a quote, backslash or non-ASCII character is caught too. Helpers that
cut text must scrub before cutting (a cut secret is no longer the value). Then fak_... and sk-ant-... patterns become [REDACTED].
"""
import json
import os
import re
import urllib.parse

_PATTERNS = [re.compile(r"fak_[\w-]+"), re.compile(r"sk-ant-[\w-]+")]
_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")


def is_secret_name(name):
    return name.startswith("FREYA_") or name.endswith(_SUFFIXES)


def _forms(value):
    """The value as written plus the encodings a trace line can carry it in: URL-quoted and Python bytes repr, each
    JSON-escaped up to three times (UTF-8 and ASCII). The trace scrubs a serialised line, so one more JSON level is
    always in play."""
    forms = {value, urllib.parse.quote(value), urllib.parse.quote(value, safe=""), repr(value.encode())[2:-1]}
    for _ in range(3):
        forms |= {json.dumps(f, ensure_ascii=a)[1:-1] for f in forms for a in (True, False)}
    return sorted(forms, key=len, reverse=True)


def _secret_vars():
    for name, value in os.environ.items():
        if is_secret_name(name) and len(value) >= 8:
            for form in _forms(value):
                yield name, form


def scrub(s: str) -> str:
    # longest values first so a value containing another is replaced whole
    for name, value in sorted(_secret_vars(), key=lambda nv: -len(nv[1])):
        s = s.replace(value, "[REDACTED:" + name + "]")
    for p in _PATTERNS:
        s = p.sub("[REDACTED]", s)
    return s
