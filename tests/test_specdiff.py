import copy
import json
from pathlib import Path

from builder.specdiff import diff_specs, diff_entries, set_path, auto_resolve, MISSING
from builder.validate import resolve_path

ROOT = Path(__file__).resolve().parent.parent
A = json.load(open(ROOT / "specs/reference/spec-a.json"))
B = json.load(open(ROOT / "specs/reference/spec-b.json"))


def test_only_band_deadline_differs():
    b = copy.deepcopy(A)
    b["bands"][1]["deadline"]["n"] = 5
    assert diff_specs(A, b) == ["bands[1].deadline.n"]


def test_identical_specs_no_diff():
    assert diff_specs(A, copy.deepcopy(A)) == []


def test_narrative_fields_ignored():
    b = copy.deepcopy(A)
    b["events"][1]["trigger"] = "something else entirely"
    b["events"][1]["instruction"] = "other words"
    b["sources"] = {}
    b["facts"] = [{"id": "f1", "quote": "x", "maps_to": "bank_name"}]
    b["extra_rules"] = []
    b["examples"] = []
    b["conflicts"] = [{"field": "bank_name", "kept": "a", "overridden": "b"}]
    assert diff_specs(A, b) == []


def test_events_keyed_by_id_not_position():
    b = copy.deepcopy(A)
    b["events"].reverse()
    assert diff_specs(A, b) == []
    del b["events"][0]  # was the last event of A: unclear
    assert diff_specs(A, b) == ["events[unclear]"]


def test_event_field_path_uses_id():
    b = copy.deepcopy(A)
    [e for e in b["events"] if e["id"] == "human"][0]["queue"] = "support"
    assert diff_specs(A, b) == ["events[human].queue"]


def test_outcomes_compared_as_a_set():
    b = copy.deepcopy(A)
    b["outcomes"].reverse()
    assert diff_specs(A, b) == []
    b["outcomes"][0]["reason"] = "other"
    assert diff_specs(A, b) == ["outcomes"]


def test_defaults_equal_absent():
    b = copy.deepcopy(A)
    b["third_party"]["callback"].pop("inclusive")       # default true
    b["bands"][0]["deadline"]["snap"] = None             # absent == null
    [e for e in b["events"] if e["id"] == "paid"][0]["times"] = 1  # default 1
    assert diff_specs(A, b) == []


def test_holidays_order_insensitive():
    b = copy.deepcopy(A)
    b["calendar"]["holidays"].reverse()
    assert diff_specs(A, b) == []


def test_sentence_whitespace_and_case_normalised():
    b = copy.deepcopy(A)
    b["confirmation"]["question"] = "  do you   confirm "
    assert diff_specs(A, b) == []


def test_a_vs_b_lists_multi_product_paths():
    paths = diff_specs(A, B)
    assert "multi_product.notes.enabled" in paths
    assert "negotiation.first_ask_all" in paths
    assert not any(p.startswith("bands") for p in paths)


def test_set_path_applies_every_diff():
    base = copy.deepcopy(A)
    other = copy.deepcopy(B)
    other["bands"][1]["deadline"]["n"] = 5
    del [e for e in other["events"] if e["id"] == "not_talk"][0]["times"]
    other["events"] = [e for e in other["events"] if e["id"] != "unclear"]
    for path, _va, vb in diff_entries(base, other):
        set_path(base, path, vb)
    assert diff_specs(base, other) == []


def test_set_path_appends_and_removes_by_id():
    s = copy.deepcopy(A)
    set_path(s, "events[newone]", {"id": "newone", "trigger": "t", "stages": ["any"], "say": None,
                                   "action": "close", "outcome": "paid", "reason": None})
    assert resolve_path(s, "events[newone].action") == (True, "close")
    set_path(s, "events[newone]", MISSING)
    assert not resolve_path(s, "events[newone]")[0]
    set_path(s, "negotiation.first_ask_all", "Pay all?")
    assert s["negotiation"]["first_ask_all"] == "Pay all?"
    set_path(s, "negotiation.first_ask_all", MISSING)
    assert "first_ask_all" not in s["negotiation"]


BRIEF = 'Ask "Do you confirm?". Say this sentence: "You will receive a text message."'


def test_auto_resolve_prefers_verbatim_sentence():
    v = auto_resolve("confirmation.question", "Do you confirm?", "Can you confirm that?", BRIEF)
    assert v is not None and v[0] == "Do you confirm?"
    v = auto_resolve("confirmation.question", "Can you confirm that?", "Do you confirm?", BRIEF)
    assert v[0] == "Do you confirm?"


def test_auto_resolve_paraphrase_keeps_first():
    v = auto_resolve("identity.decline_explanation", "These protect you.", "They keep you safe.", BRIEF)
    assert v[0] == "These protect you."


def test_auto_resolve_union_lists():
    v = auto_resolve("opening.banned_before_verify", ["debt", "payment"], ["Debt", "collections"], BRIEF)
    assert v[0] == ["debt", "payment", "collections"]


def test_auto_resolve_policy_values_need_settle():
    assert auto_resolve("bands[1].deadline.n", 7, 5, BRIEF) is None
    assert auto_resolve("events[human].queue", "general", "support", BRIEF) is None
    assert auto_resolve("events[x]", {"id": "x"}, MISSING, BRIEF) is None
