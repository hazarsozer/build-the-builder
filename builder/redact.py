"""Scrub secrets from text before it is logged, printed or written."""
import os
import re

_PATTERNS = [re.compile(r"fak_[\w-]+"), re.compile(r"sk-ant-[\w-]+")]


def _secret_vars():
    for name, value in os.environ.items():
        if (name.startswith("FREYA_") or name.endswith("_KEY") or name.endswith("_TOKEN")) and len(value) >= 8:
            yield name, value


def scrub(s: str) -> str:
    # longest values first so a value containing another is replaced whole
    for name, value in sorted(_secret_vars(), key=lambda nv: -len(nv[1])):
        s = s.replace(value, "[REDACTED:" + name + "]")
    for p in _PATTERNS:
        s = p.sub("[REDACTED]", s)
    return s
