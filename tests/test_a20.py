"""A20: the exported workflow reads cleanly (organisers open it in the dashboard; the live model reads the prompts)."""
import json
import re

import pytest

from builder.compile import compile as C
from builder.triggers import bare_trigger, split_trigger, subj, trigger_rest

OA = json.load(open("tools/openapi.json"))
SPEC_FILES = {"a": "specs/reference/spec-a.json", "b": "specs/reference/spec-b.json",
              "dress-A": "tests/fixtures/a10-dress-A.spec.json", "dress-B": "tests/fixtures/a13-dress-B.spec.json",
              "insurance": "tests/fixtures/bench-insurance.spec.json", "ids": "tests/fixtures/bench-ids.spec.json"}
SPECS = {k: json.load(open(p)) for k, p in SPEC_FILES.items()}


@pytest.fixture(scope="module", params=sorted(SPECS))
def compiled(request):
    s = SPECS[request.param]
    return request.param, s, C(s, OA, base_url="https://bank.example/v1")


def texts(wf):
    """Every string in the workflow."""
    out = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            out.append(o)
    walk(wf)
    return out


# ---------------------------------------------------------------- 1. trigger templates

@pytest.mark.parametrize("trigger, who, clause, rest", [
    ("The customer asks whether you are a robot or an AI.", "the person", "the customer asks whether you are a robot or an AI", ""),
    ("The person says the customer has died.", "the customer", "the person says the customer has died", ""),
    ("asks for a human or a person", "the person", "the person asks for a human or a person", ""),
    ("After identity is verified, the customer says that they do not want to talk.", "the customer",
     "after identity is verified, the customer says that they do not want to talk", ""),
    ("Asked \"Is this a scam?\".", "the person", "asked \"Is this a scam?\"", ""),
    ("The customer asks a question the tools cannot answer. Do not invent figures, dates or reasons.", "the person",
     "the customer asks a question the tools cannot answer", "Do not invent figures, dates or reasons."),
    ("create_promise fails again after one retry.", "the customer", "create_promise fails again after one retry", ""),
    ("A question you cannot answer from the tools.", "the person", "a question you cannot answer from the tools", ""),
])
def test_trigger_clause(trigger, who, clause, rest):
    assert subj(who, trigger) == clause
    assert split_trigger(trigger)[2] == rest
    assert trigger_rest(trigger) == ((" " + rest) if rest else "")
    assert not bare_trigger(trigger).endswith(".")
    assert not bare_trigger(trigger).lower().startswith(("the customer ", "the person "))


DOUBLED = re.compile(r"the (?:person|customer) (?:The|Asked|After|Someone)\b|\bIf (?:The|Asked|After|Someone|A) |"
                     r"when the customer [A-Z]|\bthey The |[a-z)]\.\.(?!\.)|[a-z)]\.:")


def test_no_doubled_subject_or_full_stop(compiled):
    name, _, wf = compiled
    hits = [m.group(0) for t in texts(wf) for m in DOUBLED.finditer(t)]
    assert hits == [], name


def test_trigger_extra_sentence_is_kept(compiled):
    """A trigger's second sentence ("Do not invent ...") moves after the rule instead of being dropped."""
    _, s, wf = compiled
    for r in s.get("say_rules", []):
        rest = split_trigger(r["trigger"])[2]
        if rest and "any" in (r.get("stages") or ["any"]):
            assert rest in wf["system_prompt"]


# ---------------------------------------------------------------- 2. internal labels

INTERNAL = re.compile(r"\bA\d{1,2}\b|probe|TODO|FIXME|hack")


@pytest.mark.parametrize("name", ["a", "b"])
@pytest.mark.parametrize("base_url", [None, "https://bank.example/v1"])
def test_no_internal_labels_in_export(name, base_url):
    """Task numbers, 'probe' and the like never reach the exported workflow (hooks, code nodes, descriptions)."""
    txt = json.dumps(C(SPECS[name], OA, base_url=base_url))
    assert sorted(set(INTERNAL.findall(txt))) == []


def test_no_internal_labels_in_any_fixture(compiled):
    name, _, wf = compiled
    assert sorted(set(INTERNAL.findall(json.dumps(wf)))) == [], name
