import json
from pathlib import Path

from builder.spec_guide import render

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "builder/spec_schema.json").read_text())


def test_guide_is_generated_from_current_schema():
    assert (ROOT / "docs/spec-guide.md").read_text() == render(SCHEMA)


def test_guide_lists_every_top_level_field_with_description():
    text = render(SCHEMA)
    for name, prop in SCHEMA["properties"].items():
        assert "`%s`" % name in text
    assert SCHEMA["properties"]["bands"]["description"] in text


def test_guide_shows_nested_paths_enums_and_defs():
    text = render(SCHEMA)
    assert "`bands[].deadline.rule`" in text
    assert "nth_business_day" in text
    assert "`events[].action`" in text
    assert "`multi_product.notes.timing`" in text
    assert "required" in text


BRIEF_WORDING = ["Freya", "credit card", "card_last4", "Can you", "{title}", "collections team", "2026-"]


def _texts(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("description", "title") and isinstance(v, str):
                yield v
            yield from _texts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _texts(v)


def test_schema_descriptions_carry_no_brief_wording():
    hits = [(w, t) for t in _texts(SCHEMA) for w in BRIEF_WORDING if w in t]
    assert hits == []
    guide = render(SCHEMA)
    assert [w for w in BRIEF_WORDING if w in guide] == []
