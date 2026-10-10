"""Scrub secrets from text before it is logged, printed or written.

Env vars named FREYA_* or *_KEY / *_TOKEN / *_SECRET / *_PASSWORD (values of 8+ characters) are replaced by
[REDACTED:NAME], both as written and in their JSON-escaped form (json.dumps(value)[1:-1]), so a value with a quote or
backslash is caught inside a JSON trace line too. Then fak_... and sk-ant-... patterns become [REDACTED].
"""
import json
import os
import re

_PATTERNS = [re.compile(r"fak_[\w-]+"), re.compile(r"sk-ant-[\w-]+")]
_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")


def is_secret_name(name):
    return name.startswith("FREYA_") or name.endswith(_SUFFIXES)


def _secret_vars():
    for name, value in os.environ.items():
        if is_secret_name(name) and len(value) >= 8:
            yield name, value
            escaped = json.dumps(value)[1:-1]
            if escaped != value:
                yield name, escaped


def scrub(s: str) -> str:
    # longest values first so a value containing another is replaced whole
    for name, value in sorted(_secret_vars(), key=lambda nv: -len(nv[1])):
        s = s.replace(value, "[REDACTED:" + name + "]")
    for p in _PATTERNS:
        s = p.sub("[REDACTED]", s)
    return s
