"""A21: cut-off fragments and small talk (talkative / pieces styles, results/diag-styles.md).

Offline only. Where the platform decides by extraction and routing, the diag transcripts' turn sequences are replayed
through the compiled graph (code nodes run for real, conversation transitions evaluated in order, as in test_a10)."""
import json

from builder.compile import CUT_OFF, NEG_CUT_OFF, compile as C
from tests.test_a10 import _code, holds, route  # noqa: F401  (offline engine helpers)

OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))
URL = "https://bank.example/v1"


def wf(spec=A):
    return C(spec, OA, base_url=URL)


# ---------------------------------------------------------------- 2 + 6. negotiate: small talk, "Go on." once

def test_negotiate_small_talk_is_no_refusal_and_reasks_without_a_figure():
    for spec in (A, B):
        t = wf(spec)["nodes"]["negotiate"]["task_message"]
        assert NEG_CUT_OFF in t
        assert "small talk with nothing about paying is not an answer or a refusal and uses no attempt" in t
        assert 'no amount or date ("So, can you do that?")' in t
        # the refusal rule no longer counts any non-answer (PUB-05: small talk used up the plan)
        assert "non-answer" not in t
        assert 'an evasive reply about paying' in t and '"I can\'t", "not now"' in t


def test_negotiate_go_on_never_twice_in_a_row():
    for spec in (A, B):
        t = wf(spec)["nodes"]["negotiate"]["task_message"]
        assert 'say only "Go on."' in t and "never twice in a row" in t


def test_negotiate_keeps_a9_a12_rules():
    t = wf(A)["nodes"]["negotiate"]["task_message"]
    assert '"no" to paying today with no date and no amount' in t and "Never repeat an offer the customer already refused." in t
    assert "Their own date, even after a \"no\", is never a refusal." in t and "never ask them to name an amount" in t
    m = wf(B)["nodes"]["negotiate"]["task_message"]
    assert "call set_product with outcome refused at once, before you speak" in m
    assert 'A "no" to the today question uses no attempt' in m


def test_negotiate_small_talk_is_never_cannot_pay():
    for spec in (A, B):
        d = wf(spec)["nodes"]["negotiate"]["extraction_schema"][0]["description"]
        assert "or only small talk is never cannot_pay_or_too_much: it is continue." in d


def test_negotiate_sizes():
    for bu in (None, URL):
        assert len(C(A, OA, base_url=bu)["nodes"]["negotiate"]["task_message"]) <= 4800
        assert len(C(B, OA, base_url=bu)["nodes"]["negotiate"]["task_message"]) <= 5800


def test_generic_cut_off_reasks_after_one_go_on():
    assert 'say only "Go on."' in CUT_OFF and 'Never twice in a row: after your own "Go on.", ask your last question again' in CUT_OFF


# ---------------------------------------------------------------- offline engine

class Engine:
    """Conversation-node transitions in order (tool-call transitions only when the model calls them), code nodes run
    for real, `<node>.total_turns` counted per node over the call, the node's turn cap applied."""

    def __init__(self, spec=A, at="opening", v=None):
        self.w = wf(spec)
        self.n = self.w["nodes"]
        self.v = dict(v or {})
        self.at = at
        self.turns = {}

    def flow(self, k):
        guard = 0
        while k in self.n and self.n[k]["node_type"] == "code" and not k.startswith("close_") and guard < 20:
            guard += 1
            exec(_code(self.n[k]), {"vars": self.v})
            k = route(self.n[k], self.v)
        return k

    def user(self, **extracted):
        node = self.n[self.at]
        self.turns[self.at] = self.turns.get(self.at, 0) + 1
        self.v[self.at + ".total_turns"] = self.turns[self.at]
        self.v.update(extracted)
        t = route(node, self.v)
        if t is None and self.turns[self.at] >= node.get("max_node_turns", 10):
            t = node["max_turn_transition"]
        if t is not None:
            self.at = self.flow(t)
        return self.at


def _verify(e, field, answer, left):
    t = e.n["identity"]["tools"][0]
    cfg = {"url": "x", "body": {"field": field, "answer": answer}}
    exec(t["pre_process_code"], {"vars": e.v, "config": cfg})
    r = {"status": "success", "data": {"ok": True, "verified": False, "wrong_answers_left": left, "all_verified": False}}
    exec(t["post_process_code"], {"vars": e.v, "result": r})
    e.v["id_left"] = str(left)


# ---------------------------------------------------------------- 1. identity: fragments and small talk never decline

def test_identity_prompt_and_extraction_rules():
    for spec in (A, B):
        n = wf(spec)["nodes"]["identity"]
        t = n["task_message"]
        assert CUT_OFF in t
        assert "Small talk is not a refusal: never explain for it; ask your question again in a few words." in t
        d = n["extraction_schema"][0]["description"]
        assert "A message cut off mid-phrase, or only small talk with no refusal words, is continue, never declined." in d
        ex = [x for x in n["extraction_schema"] if x["name"] == "id_explain_said"]
        assert ex and '"These questions protect your account."' in ex[0]["description"] and ex[0]["enum_values"] == ["no", "yes"]


def test_pub07_replay_fragment_read_as_decline_before_any_explanation_goes_back():
    """43c11304: DOB answered, then eight fragments; the eighth was extracted as declined (0.99) and closed the call.
    No explanation had been spoken, so the gate now sends it back to identity."""
    e = Engine(at="identity")
    for _ in range(7):
        assert e.user(id_event="continue", id_explain_said="no") == "identity"
    assert e.user(id_event="declined", id_explain_said="no") == "identity"
    assert e.v["id_event"] == "continue" and e.v["id_gate"] == "explain"
    # the agent explains; a real refusal after it closes as declined
    assert e.user(id_event="declined", id_explain_said="yes") == "close_declined"


def test_pub03_replay_third_wrong_answer_closes_as_failed_not_declined():
    """e2199549: two wrong dates, then four fragments; the fourth was extracted as declined and closed the call before
    the third answer. Now it goes back; the third wrong answer closes as failed."""
    e = Engine(at="identity")
    e.v.update(id_partial="twelve July nineteen ninety-one", id_partial_field="date_of_birth")
    _verify(e, "date_of_birth", "1991-07-12", 2)
    assert e.user(id_event="continue", id_explain_said="no") == "identity"
    e.v.update(id_partial="twenty-one August nineteen ninety-two")
    _verify(e, "date_of_birth", "1992-08-21", 1)
    for _ in range(3):
        assert e.user(id_event="continue", id_explain_said="no") == "identity"
    assert e.user(id_event="declined", id_explain_said="no") == "identity"
    _verify(e, "date_of_birth", "1990-01-01", 0)
    assert e.user(id_event="continue", id_explain_said="no") == "close_failed"


def test_identity_explanation_flag_is_kept_once_seen():
    assert Engine(at="identity").user(id_event="declined", id_explain_said="yes", id_partial="") == "close_declined"
    e = Engine(at="identity")
    assert e.user(id_event="declined", id_explain_said="no", id_partial="") == "identity"
    assert e.user(id_event="declined", id_explain_said="yes") == "close_declined" and e.v["id_explained"] == "yes"
    # an extraction that later misses it does not undo the flag
    e2 = Engine(at="identity", v={"id_explained": "yes"})
    assert e2.user(id_event="declined", id_explain_said="no", id_partial="") == "close_declined"


def test_identity_decline_without_explanation_is_bounded_by_the_gate():
    """A return to identity may restart the node's turn cap, so the gate bounds its own bounces (3)."""
    e = Engine(at="identity")
    for _ in range(3):
        assert e.user(id_event="declined", id_explain_said="no", id_partial="") == "identity"
    assert e.user(id_event="declined", id_explain_said="no", id_partial="") == "close_declined"
    assert wf()["nodes"]["identity"]["max_node_turns"] == 20


def test_no_explanation_flag_without_a_declined_event():
    import copy
    s = copy.deepcopy(A)
    s["events"] = [ev for ev in s["events"] if ev["id"] != "declined"]
    n = wf(s)["nodes"]
    assert "id_decline_gate" not in n
    assert not [x for x in n["identity"]["extraction_schema"] if x["name"] == "id_explain_said"]
