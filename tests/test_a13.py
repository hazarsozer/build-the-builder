"""A13: banned_before_verify must not contradict the spec's own required pre-verification sentences."""
import copy
import json
from pathlib import Path

import pytest

from builder import cases
from builder import compile as comp
from builder.lint import lint, reconcile_banned
from tests.test_main_dry import env, run_main, trace_rows  # noqa: F401  (env fixture)

ROOT = Path(__file__).resolve().parent.parent
OA = json.load(open(ROOT / "tools/openapi.json"))
A = json.load(open(ROOT / "specs/reference/spec-a.json"))
B = json.load(open(ROOT / "specs/reference/spec-b.json"))
DRESS_B = json.load(open(ROOT / "tests/fixtures/a13-dress-B.spec.json"))


def test_dress_b_the_account_removed_and_reported():
    spec, removed = reconcile_banned(DRESS_B)
    assert "the account" not in spec["opening"]["banned_before_verify"]
    assert [r["term"] for r in removed] == ["the account"]
    assert removed[0]["found_in"] == "identity.decline_explanation"
    assert "account" in removed[0]["sentence"]
    assert "the account" in DRESS_B["opening"]["banned_before_verify"]      # input not mutated


def test_terms_absent_from_required_sentences_stay_banned():
    spec, _ = reconcile_banned(DRESS_B)
    assert spec["opening"]["banned_before_verify"] == [t for t in DRESS_B["opening"]["banned_before_verify"] if t != "the account"]
    s = copy.deepcopy(DRESS_B)
    s["opening"]["banned_before_verify"] = ["debt", "Account"]
    assert reconcile_banned(s)[0]["opening"]["banned_before_verify"] == ["debt"]    # case-insensitive


def test_word_boundary_and_recording_rewrite():
    s = copy.deepcopy(DRESS_B)
    s["identity"]["decline_explanation"] = "These questions protect your accounting."
    s["opening"]["banned_before_verify"] = ["account", "record"]
    out, removed = reconcile_banned(s)
    assert out["opening"]["banned_before_verify"] == ["account", "record"] and removed == []
    # "this call is recorded" is rewritten before scanning, so "record" is not in the spoken opening
    s["opening"]["first_sentence"] = "Hello, this is Freya Bank, and this call is recorded."
    assert reconcile_banned(s)[1] == []


@pytest.mark.parametrize("spec", [A, B], ids=["spec-a", "spec-b"])
def test_reference_specs_unchanged_and_compile_identical(spec):
    out, removed = reconcile_banned(spec)
    assert removed == [] and out is spec
    assert json.dumps(comp.compile(out, OA), sort_keys=True) == json.dumps(comp.compile(spec, OA), sort_keys=True)


def test_cases_and_prompt_use_cleaned_list():
    clean, _ = reconcile_banned(DRESS_B)
    before = comp.compile(DRESS_B, OA)
    after = comp.compile(clean, OA)
    assert json.dumps(before) != json.dumps(after)
    assert not [p for p in lint(after, clean) if not p.startswith("warning:")]
    cs = cases.generate(clean, OA, "2026-10-12T10:00:00", n=10, seed=1)
    text = json.dumps([c.get("checks") for c in cs])
    assert "the account" not in text and "debt" in text


def test_main_reports_and_applies(env):  # noqa: F811
    env.spec = copy.deepcopy(DRESS_B)
    assert run_main(env, "--no-selftest") == 0
    [ev] = [r for r in trace_rows(env) if r["kind"] == "banned_reconciled"]
    assert ev["stage"] == "extract" and [r["term"] for r in ev["removed"]] == ["the account"]
    saved = json.loads((env.out / (env.slot + ".spec.json")).read_text())
    assert "the account" not in saved["opening"]["banned_before_verify"]


def test_main_no_event_when_nothing_to_remove(env):  # noqa: F811
    assert run_main(env, "--no-selftest") == 0
    assert not [r for r in trace_rows(env) if r["kind"] == "banned_reconciled"]
