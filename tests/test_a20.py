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
     "the customer says that they do not want to talk (after identity is verified)", ""),
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

# work words, any case, whole words only; task labels (A11, IDS-08) case-sensitive, so a code variable a2 is not one
INTERNAL_WORDS = re.compile(r"\b(?:probe|todo|fixme|hack)\b", re.I)
INTERNAL_LABELS = re.compile(r"\bA\d{1,2}\b|\b[A-Z]{2,4}-\d{2}\b")


def internal(txt):
    return sorted({m.group(0) for p in (INTERNAL_WORDS, INTERNAL_LABELS) for m in p.finditer(txt)})


def test_internal_patterns_catch_labels():
    assert internal("x (A11 probe) y") == ["A11", "probe"]
    assert internal("# Todo: HACK around IDS-08, PUB-13") == ["HACK", "IDS-08", "PUB-13", "Todo"]
    assert internal("a2 = probes + todos; hacked; SELF-05x") == []


@pytest.mark.parametrize("name", ["a", "b"])
@pytest.mark.parametrize("base_url", [None, "https://bank.example/v1"])
def test_no_internal_labels_in_export(name, base_url):
    """Task numbers, 'probe' and the like never reach the exported workflow (hooks, code nodes, descriptions)."""
    assert internal(json.dumps(C(SPECS[name], OA, base_url=base_url))) == []


def test_no_internal_labels_in_any_fixture(compiled):
    name, _, wf = compiled
    assert internal(json.dumps(wf)) == [], name


# ---------------------------------------------------------------- 3. no "brief" the live model never sees

def test_prompts_never_mention_the_brief(compiled):
    name, _, wf = compiled
    hits = [t[max(0, m.start() - 40):m.end() + 20] for t in texts(wf)
            for m in re.finditer(r"\bbrief\b(?!ly)", t, re.I)]
    assert hits == [], name


def test_rules_heading_is_plain(compiled):
    _, _, wf = compiled
    neg = wf["nodes"]["negotiate"]["task_message"]
    assert "RULES FROM THE BRIEF" not in neg
    assert all("Rules from the brief" not in t for t in texts(wf))


def test_banned_offer_clause_not_doubled(compiled):
    """'any term that is not in this brief, or any term not given in your instructions': said once."""
    _, _, wf = compiled
    line = next(l for l in wf["system_prompt"].split("\n") if l.startswith("- Never offer"))
    assert line.count("your instructions") == 1, line


def test_not_talk_edge_reads_cleanly():
    wf = C(SPECS["dress-B"], OA, base_url="https://bank.example/v1")
    tr = [t for t in wf["nodes"]["negotiate"]["transitions"] if t.get("target_node") == "wrap_not_talk"]
    assert tr and tr[0]["description"] == ("Call this when the customer says for the second time that they do not want to talk "
                                           "(after identity is verified). The goodbye is spoken automatically.")
    assert "If the customer says for the second time that they do not want to talk (after identity is verified): call transition_to_wrap_not_talk." \
        in wf["nodes"]["negotiate"]["task_message"]


def test_spec_a_negotiate_within_budget():
    for bu in (None, "https://bank.example/v1"):
        assert len(C(SPECS["a"], OA, base_url=bu)["nodes"]["negotiate"]["task_message"]) <= 4800


# ---------------------------------------------------------------- 4/5. edge labels, step descriptions

def test_no_bare_always_label(compiled):
    name, _, wf = compiled
    for g in ("nodes", "global_nodes"):
        for n, nd in (wf.get(g) or {}).items():
            for t in nd.get("transitions") or []:
                assert t.get("description") != "always", (name, n)
                if (t.get("condition") or {}).get("operator") == "always":
                    assert t["description"].startswith(("Next: ", "Otherwise: ")), (name, n, t)


def test_fallback_edge_says_otherwise():
    wf = C(SPECS["b"], OA, base_url="https://bank.example/v1")
    last = wf["nodes"]["finish"]["transitions"][-1]
    assert last["condition"] == {"operator": "always"}
    assert last["description"] == "Otherwise: close the call (service error)"
    assert wf["nodes"]["load_account_retry"]["transitions"][-1]["description"] == "Next: prepare account"  # A24


def test_every_step_has_a_description(compiled):
    name, _, wf = compiled
    for n, nd in wf["nodes"].items():
        for pa in nd.get("pre_actions") or []:
            fn = pa["function"]
            d = fn.get("description") or ""
            assert d and d != fn["name"] and not d.startswith("Code step:"), (name, n, d)
            assert "{{" not in d and "}}" not in d


def test_labels_are_presentation_only(compiled):
    """describe() touches only labels: running it again changes nothing, and transitions keep their own keys."""
    import copy
    from builder import labels
    _, _, wf = compiled
    w = copy.deepcopy(wf)
    labels.describe(w)
    assert w == wf
    for nd in wf["nodes"].values():
        for t in nd.get("transitions") or []:
            assert set(t) <= {"target_node", "description", "condition", "is_tool_call"}


# ---------------------------------------------------------------- 6. read-only tools say so

def _tool(wf, name):
    return next(t for t in wf["nodes"]["negotiate"].get("tools") or [] if t["function"]["name"] == name)


def test_read_only_tools_say_they_change_no_record():
    single = C(SPECS["a"], OA, base_url="https://bank.example/v1")
    d = _tool(single, "check_date")
    assert d["function"]["parameters"]["properties"]["url"]["default"].endswith("/get_account")
    assert "changes no bank record" in d["function"]["description"]
    multi = C(SPECS["dress-B"], OA, base_url="https://bank.example/v1")
    sp = _tool(multi, "set_product")
    assert sp["function"]["parameters"]["properties"]["url"]["default"].endswith("/get_account")
    assert sp["function"]["description"].endswith("the call only reads the account and changes no bank record.")


def test_set_product_code_mode_says_no_record():
    import builder.compile as comp
    c = comp._Compiler(SPECS["dress-B"], OA, "https://bank.example/v1")
    c.build()
    t = c.multi.set_tool(mode="code")
    assert t["function"]["description"].endswith("The answer is kept in the call and changes no bank record.")


# ---------------------------------------------------------------- 7. trace summary

def _trace_md(tmp_path, monkeypatch):
    from builder import trace
    from builder.redact import is_secret_name
    import os
    for k in list(os.environ):
        if is_secret_name(k):
            monkeypatch.delenv(k)
    t = trace.Trace(tmp_path / "20261011T013308Z-dress-B.jsonl")
    t.event("start", briefs=[str(trace.REPO / "briefs" / "brief_b.md")], openapi=str(trace.REPO / "tools" / "openapi.json"))
    for k in (1, 2):
        t.event("saved", slot="dress-B-selftest", workflow_id="w-self", agent_id="a-self", url_kind="tunnel", round=k)
    t.event("saved", slot="dress-B", workflow_id="w-final", agent_id="a-final", url_kind="official", spec="extract")
    t.event("export", path=str(trace.REPO / "submission" / "dress-B.workflow.json"))
    t.event("note", where=str(trace.Path.home() / "elsewhere" / "x.json"))
    t.event("end", duration_s=1, ok=True)
    return t.write_markdown().read_text()


def test_trace_has_no_absolute_local_paths(tmp_path, monkeypatch):
    from builder import trace
    text = _trace_md(tmp_path, monkeypatch)
    assert str(trace.REPO) not in text and str(trace.Path.home()) not in text
    assert "/home/" not in text
    assert "- submission/dress-B.workflow.json" in text
    assert "briefs/brief_b.md" in text


def test_trace_saved_rows_once_and_plain_words(tmp_path, monkeypatch):
    text = _trace_md(tmp_path, monkeypatch)
    assert text.count("| dress-B-selftest | w-self | a-self |") == 1
    assert "| dress-B-selftest | w-self | a-self | local mock bank (self-test) | self-test rounds 1, 2 |" in text
    assert "| dress-B | w-final | a-final | official mock bank | final agent |" in text
    assert "tunnel" not in text.lower()
    assert text.startswith("# Build trace: dress-B (run 20261011T013308Z)")


def test_relative_paths_leaves_lookalike_paths():
    from builder import trace
    base = str(trace.REPO)
    assert trace.relative_paths(base + "-other/x").endswith("-other/x")       # a sibling directory is not cut
    assert not trace.relative_paths(base + "-other/x").startswith(".")
    assert trace.relative_paths(base + "/a/b.json") == "a/b.json"



# ---------------------------------------------------------------- fix round 1

@pytest.mark.parametrize("trigger, first, rest", [
    ("The customer says Dr. Smith told them to wait. Do not argue.", "says Dr. Smith told them to wait", "Do not argue."),
    ("The person says Mrs. Reyes is away, e.g. Friday.", "says Mrs. Reyes is away, e.g. Friday", ""),
    ("The customer names St. Mary's hospital.", "names St. Mary's hospital", ""),
    ("The customer gives No. 12 as the house number. Ask once.", "gives No. 12 as the house number", "Ask once."),
])
def test_sentence_split_skips_abbreviations(trigger, first, rest):
    assert split_trigger(trigger)[1:] == (first, rest)


@pytest.mark.parametrize("trigger, clause", [
    ("Fees are disputed.", "fees are disputed"),                  # not a verb: left as it is, no subject added
    ("Payments stop for two months.", "payments stop for two months"),
    ("Bills arrive late.", "bills arrive late"),
    ("asks for a human", "the customer asks for a human"),
    ("still refuses after the floor", "the customer still refuses after the floor"),
    ("refuses to pay", "the customer refuses to pay"),
])
def test_subject_only_before_a_known_verb(trigger, clause):
    assert subj("the customer", trigger) == clause


# every shape a trigger can take that once doubled a subject: a time clause, a non-customer subject, "They"
HOSTILE = [
    "After identity is verified, the customer says {x}.",
    "Before identity is verified, the person says {x}.",
    "The person (not the customer) says {x}.",
    "They say {x}.",
    "Asked whether {x}.",
    "The customer says {x}. Do not argue.",
]
DOUBLE_ANY = re.compile(r"(?i:\b(?:the customer|the person|they|customer:)\s+(?:the customer|the person|they|after|before|asked)\b)|"
                        r"\bIf (?:The|After|Before|Asked|They) |[a-z)]\.\.(?!\.)|[a-z)]\.:|\.\s+\.")


@pytest.mark.parametrize("spec_name", ["a", "dress-A", "dress-B", "insurance"])
@pytest.mark.parametrize("shape", range(len(HOSTILE)))
def test_hostile_trigger_shapes_never_double(spec_name, shape):
    """Every embedding site (prompts, extraction, tool and edge descriptions, matches_intent conditions) with every
    trigger rewritten into a shape that once doubled the subject."""
    import copy
    s = copy.deepcopy(SPECS[spec_name])
    for i, e in enumerate(s["events"] + s.get("say_rules", [])):
        e["trigger"] = HOSTILE[shape].format(x="case %d happens" % i)
    wf = C(s, OA, base_url="https://bank.example/v1")
    hits = sorted({t[max(0, m.start() - 50):m.end() + 20] for t in texts(wf) for m in DOUBLE_ANY.finditer(t)
                   if "case " in t[max(0, m.start() - 80):m.end() + 80]})
    assert hits == [], (spec_name, HOSTILE[shape])
    # the matches_intent fields carry the clause too
    intents = [t["condition"]["description"] for nd in wf["nodes"].values() for t in nd.get("transitions") or []
               if (t.get("condition") or {}).get("operator") == "matches_intent" and "case " in t["condition"]["description"]]
    for d in intents:
        assert d[:1].isupper() and not DOUBLE_ANY.search(d), d


def test_backup_close_intent_keeps_own_subject():
    import copy
    s = copy.deepcopy(SPECS["a"])
    ev = next(e for e in s["events"] if e["id"] == "dispute")
    ev["trigger"] = "After identity is verified, the customer says the debt is not theirs."
    wf = C(s, OA, base_url="https://bank.example/v1")
    d = [t["condition"]["description"] for t in wf["nodes"]["negotiate"]["transitions"]
         if t.get("is_tool_call") and t["target_node"] == "close_dispute"]
    assert d == ["The customer says the debt is not theirs (after identity is verified)"]
    ev["trigger"] = "The person (not the customer) says the debt is not theirs."
    wf = C(s, OA, base_url="https://bank.example/v1")
    d = [t["condition"]["description"] for t in wf["nodes"]["negotiate"]["transitions"]
         if t.get("is_tool_call") and t["target_node"] == "close_dispute"]
    assert d == ["The person (not the customer) says the debt is not theirs"]


def test_refused_intent_is_normalised():
    wf = C(SPECS["a"], OA, base_url="https://bank.example/v1")
    d = [t["condition"]["description"] for t in wf["nodes"]["negotiate"]["transitions"]
         if t.get("is_tool_call") and t["target_node"] == "close_refused"]
    assert d == ["The persuasion attempts are used up after flat refusals or non-answers."]
