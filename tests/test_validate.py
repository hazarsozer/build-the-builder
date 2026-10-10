import copy
import json
import re
from pathlib import Path

from builder.validate import validate, resolve_path

ROOT = Path(__file__).resolve().parent.parent
OA = json.load(open(ROOT / "tools/openapi.json"))
A = json.load(open(ROOT / "specs/reference/spec-a.json"))
B = json.load(open(ROOT / "specs/reference/spec-b.json"))


def _rules_cited(spec, prefix):
    nums = set()
    for v in spec["sources"].values():
        for s in v if isinstance(v, list) else [v]:
            m = re.match(prefix + r"(\d+)$", s)
            if m:
                nums.add(int(m.group(1)))
    return nums


# --- brief-given tests ---------------------------------------------------------

def test_reference_specs_valid():
    assert validate(A, OA) == []
    assert validate(B, OA) == []


def test_outcome_not_in_openapi_enum():
    s = copy.deepcopy(A); s["outcomes"][0]["outcome"] = "promised"
    assert any("not in openapi" in e for e in validate(s, OA))


def test_band_gap():
    s = copy.deepcopy(A); s["bands"][1]["days_late"] = [9, 45]
    assert any("gap" in e for e in validate(s, OA))


def test_identity_field_not_in_enum():
    s = copy.deepcopy(A); s["identity"]["questions"][1]["field"] = "postcode"
    assert any("postcode" in e for e in validate(s, OA))


def test_failing_example():
    s = copy.deepcopy(A)
    s["examples"].append({"kind": "deadline", "today": "2026-10-14", "days_late": 6, "expect": "2026-10-29"})
    assert any("example" in e for e in validate(s, OA))


def test_reason_enum_checked_for_notes():
    s = copy.deepcopy(A); s["multi_product"]["notes"]["reasons"].append("angry")
    assert any("angry" in e for e in validate(s, OA))


# --- extra checks --------------------------------------------------------------

def test_band_overlap():
    s = copy.deepcopy(A); s["bands"][1]["days_late"] = [7, 45]
    assert any("overlap" in e for e in validate(s, OA))


def test_schema_rejects_unknown_key():
    s = copy.deepcopy(A); s["opening"]["max_ask"] = 2
    assert any("schema" in e for e in validate(s, OA))


def test_missing_openapi_outcome():
    s = copy.deepcopy(A)
    s["outcomes"] = [o for o in s["outcomes"] if o["outcome"] != "paid"]
    s["events"] = [e for e in s["events"] if e["id"] != "paid"]
    del s["sources"]["events[paid]"]
    errs = validate(s, OA)
    assert any("'paid'" in e and "unused_outcomes" in e for e in errs)
    s["unused_outcomes"] = ["paid"]
    assert validate(s, OA) == []


def test_reason_required_except_promise_paid():
    s = copy.deepcopy(A)
    row = next(o for o in s["outcomes"] if o["outcome"] == "no_promise")
    row["reason"] = None
    assert any("reason" in e for e in validate(s, OA))


def test_outcome_row_unknown_event():
    s = copy.deepcopy(A); s["outcomes"][0]["event"] = "nope"
    assert any("nope" in e for e in validate(s, OA))


def test_transfer_queue_enum():
    s = copy.deepcopy(A)
    next(e for e in s["events"] if e["id"] == "restructuring")["queue"] = "billing"
    assert any("billing" in e for e in validate(s, OA))


def test_failing_attempt_example():
    s = copy.deepcopy(A)
    s["examples"].append({"kind": "attempt", "today": "2026-10-14", "days_late": 20,
                          "attempt_index": 2, "expect": "2026-10-16"})
    assert any("example" in e for e in validate(s, OA))


def test_attempt_index_out_of_range():
    s = copy.deepcopy(A)
    s["examples"].append({"kind": "attempt", "today": "2026-10-14", "days_late": 3,
                          "attempt_index": 2, "expect": "2026-10-16"})
    assert any("example" in e for e in validate(s, OA))


def test_unknown_placeholder():
    s = copy.deepcopy(A); s["confirmation"]["success_sentence"] = "Thanks {nickname}."
    assert any("nickname" in e for e in validate(s, OA))


def test_double_brace_placeholder_rejected():
    s = copy.deepcopy(A); s["opening"]["confirm_question"] = "Am I speaking with {{title}} {{last_name}}?"
    assert any("double brace" in e for e in validate(s, OA))


def test_product_type_not_in_openapi():
    s = copy.deepcopy(A); s["products"]["mortgage"] = {"words": "mortgage", "floor_field": "instalment_amount"}
    assert any("mortgage" in e for e in validate(s, OA))


def test_floor_field_not_in_openapi():
    s = copy.deepcopy(A); s["products"]["car_loan"]["floor_field"] = "monthly"
    assert any("monthly" in e for e in validate(s, OA))


def test_source_path_must_resolve():
    s = copy.deepcopy(A); s["sources"]["negotiation.no_such"] = "rule 1"
    assert any("no_such" in e for e in validate(s, OA))


# --- fact pass -----------------------------------------------------------------

def test_resolve_path():
    assert resolve_path(A, "bands[1].deadline.n") == (True, 7)
    assert resolve_path(A, "bands[5].deadline.n")[0] is False
    assert resolve_path(A, "extra_rules[0]")[0] is True
    assert resolve_path(A, "opening.nope")[0] is False
    assert resolve_path(A, "events[paid].outcome") == (True, "paid")
    assert resolve_path(A, "events[nope]")[0] is False
    assert resolve_path(A, "bands..name")[0] is False


def test_facts_valid_and_unmapped():
    s = copy.deepcopy(A)
    s["facts"] = [{"id": "f1", "quote": "8 to 45 | late | the 7th business day after today", "maps_to": "bands[1].deadline.n"},
                  {"id": "f2", "quote": "Ask once more.", "maps_to": "opening.max_asks"}]
    assert validate(s, OA) == []
    s["facts"].append({"id": "f3", "quote": "x", "maps_to": ""})
    s["facts"].append({"id": "f4", "quote": "y", "maps_to": "bands[9].deadline"})
    errs = validate(s, OA)
    assert any("f3" in e for e in errs)
    assert any("f4" in e for e in errs)


def test_fact_missing_maps_to_key():
    s = copy.deepcopy(A)
    s["facts"] = [{"id": "f1", "quote": "x"}]
    assert validate(s, OA) != []


# --- reference specs cover the briefs -----------------------------------------

def test_spec_a_cites_all_47_rules():
    assert _rules_cited(A, r"rule ") == set(range(1, 48))


def test_spec_b_cites_all_a_and_b_rules():
    assert _rules_cited(B, r"rule ") == set(range(1, 48))
    assert _rules_cited(B, r"B rule ") == set(range(1, 12))


def test_spec_b_is_spec_a_plus_multi_product():
    assert A["multi_product"]["notes"]["enabled"] is False
    assert B["multi_product"]["notes"]["enabled"] is True
    assert B["multi_product"]["notes"]["on_transfer"] == "none"
    assert B["bands"] == A["bands"] and B["calendar"] == A["calendar"]


# --- fix round 1 ---------------------------------------------------------------

from builder.validate import SCHEMA


def test_every_top_level_property_has_description():
    missing = [k for k, v in SCHEMA["properties"].items() if not v.get("description")]
    assert missing == []


def test_key_nested_properties_have_descriptions():
    ev = SCHEMA["properties"]["events"]["items"]["properties"]
    for k in ("say", "instruction", "action", "queue", "else_outcome", "else_reason", "times",
              "overrides_persuasion", "per_product_note"):
        assert ev[k].get("description"), k
    tp = SCHEMA["properties"]["third_party"]["properties"]["callback"]["properties"]
    assert "default" in tp["inclusive"]["description"].lower()
    assert "default" in SCHEMA["properties"]["say_rules"]["items"]["properties"]["stages"]["description"].lower()
    assert "events[paid]" in SCHEMA["properties"]["sources"]["description"]
    assert "events[paid]" in SCHEMA["properties"]["facts"]["items"]["properties"]["maps_to"]["description"]
    assert "at_close" in SCHEMA["properties"]["multi_product"]["properties"]["notes"]["properties"]["timing"]["description"]


def test_opening_does_not_end_on_recorded():
    want = ("Hello, this is a digital assistant calling from Freya Bank. "
            "This call is being recorded for quality and security purposes.")
    assert A["opening"]["first_sentence"] == want
    assert B["opening"]["first_sentence"] == want


def test_spec_b_multi_product_wording():
    n = B["negotiation"]
    assert "{product_words}" in n["debt_statement_per_product"]
    assert n["first_ask_all"].endswith("?")
    assert B["multi_product"]["notes"]["timing"] == "at_close"
    fields = {c["field"] for c in B["conflicts"]}
    assert "events[hardship]" in fields and "multi_product.notes" in fields
    for c in B["conflicts"]:
        assert resolve_path(B, c["field"])[0], c["field"]


def test_notes_timing_enum():
    s = copy.deepcopy(B); s["multi_product"]["notes"]["timing"] = "later"
    assert any("schema" in e for e in validate(s, OA))
