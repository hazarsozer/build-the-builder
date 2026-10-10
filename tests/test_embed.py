import datetime as dt
import types

import pytest

from builder.lib import dates, money, embed


def test_source_embeds_and_runs():
    src = embed.source(dates, money)
    assert "def nth_bd" in src and "def fmt" in src
    assert "{{" not in src and "}}" not in src
    assert "import datetime" in src
    ns = {"vars": {}}
    exec(src, ns)
    cal = {"weekdays": [0, 1, 2, 3, 4], "holidays": []}
    assert ns["nth_bd"](dt.date(2026, 10, 14), 1, cal) == dt.date(2026, 10, 15)


def test_source_rejects_braces(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text("x = {'a': {'b': 1}}\n")
    bad = types.ModuleType("bad")
    bad.__file__ = str(p)
    with pytest.raises(AssertionError):
        embed.source(bad)
