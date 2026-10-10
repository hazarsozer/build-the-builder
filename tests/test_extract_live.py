"""Live extractor gate (Claude login, real model time and money). Run with:

    RUN_LIVE=1 python -m pytest -m live tests/test_extract_live.py
"""
import json
import os
from pathlib import Path

import pytest

from bench.grade_spec import grade
from builder.extract import extract
from builder.sdk import NullTrace

ROOT = Path(__file__).resolve().parent.parent
OA = json.load(open(ROOT / "tools/openapi.json"))

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(not os.environ.get("RUN_LIVE"), reason="live: set RUN_LIVE=1")]


@pytest.mark.parametrize("briefs,ref", [
    (["briefs/brief-a.md"], "specs/reference/spec-a.json"),
    (["briefs/brief-a.md", "briefs/brief-b.md"], "specs/reference/spec-b.json"),
])
def test_extract_matches_reference(briefs, ref):
    spec, errs, info = extract([str(ROOT / b) for b in briefs], OA, NullTrace())
    assert errs == []
    assert grade(spec, json.load(open(ROOT / ref))) == []
    assert info["seconds"] <= 600


def test_raw_material_memo_override():
    raw = ROOT / "bench/raw-a"
    spec, errs, info = extract([str(raw)], json.load(open(raw / "openapi.json")), NullTrace())
    assert errs == []
    late = max(spec["bands"], key=lambda b: b["days_late"][0])
    assert late["deadline"]["n"] == 5
    assert any("bands" in c["field"] for c in spec["conflicts"])
