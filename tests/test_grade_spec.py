import copy
import json
from pathlib import Path

from bench.grade_spec import grade

ROOT = Path(__file__).resolve().parent.parent
A = json.load(open(ROOT / "specs/reference/spec-a.json"))


def test_reference_grades_clean():
    assert grade(A, A) == []


def test_graded_fields():
    s = copy.deepcopy(A)
    s["bands"][1]["deadline"]["n"] = 5
    s["calendar"]["holidays"].pop()
    s["identity"]["questions"][1]["length"] = 3
    s["products"]["car_loan"]["floor_field"] = "minimum_payment"
    s["outcomes"].pop()
    s["events"] = [e for e in s["events"] if e["id"] != "not_talk"]
    s["confirmation"]["question"] = "Do you agree?"
    s["events"][0]["trigger"] = "anything"          # not graded
    s["negotiation"]["debt_statement"] = "Other."   # not graded
    got = sorted(g[0] for g in grade(s, A))
    assert got == ["bands[1].deadline", "calendar.holidays", "confirmation.question", "event_ids",
                   "identity.questions[1]", "outcome_set", "products.car_loan.floor_field"]
