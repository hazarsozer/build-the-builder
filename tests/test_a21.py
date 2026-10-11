"""A21: cut-off fragments and small talk (talkative / pieces styles, results/diag-styles.md).

Offline only. Where the platform decides by extraction and routing, the diag transcripts' turn sequences are replayed
through the compiled graph (code nodes run for real, conversation transitions evaluated in order, as in test_a10)."""
import json

from builder.compile import CUT_OFF, NEG_CUT_OFF, NOT_SENT_URL, compile as C
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


def _verify(e, field, answer, left, verified=False):
    t = e.n["identity"]["tools"][0]
    cfg = {"url": "x", "body": {"field": field, "answer": answer}}
    exec(t["pre_process_code"], {"vars": e.v, "config": cfg})
    r = {"status": "success", "data": {"ok": True, "verified": verified, "wrong_answers_left": left, "all_verified": False}}
    exec(t["post_process_code"], {"vars": e.v, "result": r})
    e.v["id_left"] = str(left)


# ---------------------------------------------------------------- 1. identity: fragments and small talk never decline

def test_identity_prompt_and_extraction_rules():
    for spec in (A, B):
        n = wf(spec)["nodes"]["identity"]
        t = n["task_message"]
        # fix round 1: the cut-off rule is a bullet with the other situations
        assert "\n- " + CUT_OFF + "\n" in t
        assert "Small talk is not a refusal: never explain for it; ask your question again in a few words." in t
        d = n["extraction_schema"][0]["description"]
        assert "A message cut off mid-phrase, or only small talk with no refusal words, is continue, never declined." in d
        ex = [x for x in n["extraction_schema"] if x["name"] == "id_explain_said"]
        assert ex and '"These questions protect your account."' in ex[0]["description"] and ex[0]["enum_values"] == ["no", "yes"]


def test_explain_step_speaks_the_explanation_and_the_open_question():
    """Fix round 1: the one explanation is spoken by a step (tts), so the code flag never depends on extraction."""
    for spec in (A, B):
        n = wf(spec)["nodes"]["id_explain"]
        assert n["on_enter"] == {"type": "tts", "text": "These questions protect your account. {{id_ask_text}}"}
        assert n["max_turn_transition"] == "identity" and n["transitions"][0]["target_node"] == "identity"
        assert "Never offer" not in n["task_message"]   # a pre-verification step


def test_pub07_replay_fragment_read_as_decline_before_any_explanation_goes_back():
    """43c11304: DOB verified, then eight fragments; the eighth was extracted as declined (0.99) and closed the call.
    No explanation had been given, so the gate now sends it to id_explain, which says it and asks the phone question."""
    e = Engine(at="identity")
    e.v.update(id_field_cur="date_of_birth")
    _verify(e, "date_of_birth", "1975-12-05", 2, verified=True)
    for _ in range(7):
        assert e.user(id_event="continue", id_explain_said="no") == "identity"
    assert e.user(id_event="declined", id_explain_said="no") == "id_explain"
    assert e.v["id_explained"] == "yes" and e.v["id_event"] == "continue"
    assert e.v["id_ask_text"] == "What are the last four digits of the phone number on file?"
    assert e.user(id_explain_reply="replied") == "identity"
    # a refusal after the explanation closes as declined, even if the extractor missed the explanation
    assert e.user(id_event="declined", id_explain_said="no") == "close_declined"


def test_pub03_replay_third_wrong_answer_closes_as_failed_not_declined():
    """e2199549: two wrong dates, then four fragments; the fourth was extracted as declined and closed the call before
    the third answer. Now it goes to the explanation; the third wrong answer closes as failed."""
    e = Engine(at="identity")
    e.v.update(id_partial="twelve July nineteen ninety-one", id_partial_field="date_of_birth")
    _verify(e, "date_of_birth", "1991-07-12", 2)
    assert e.user(id_event="continue", id_explain_said="no") == "identity"
    e.v.update(id_partial="twenty-one August nineteen ninety-two")
    _verify(e, "date_of_birth", "1992-08-21", 1)
    for _ in range(3):
        assert e.user(id_event="continue", id_explain_said="no") == "identity"
    assert e.user(id_event="declined", id_explain_said="no") == "id_explain"
    assert e.v["id_ask_text"] == "What is your date of birth?"
    assert e.user(id_explain_reply="replied") == "identity"
    _verify(e, "date_of_birth", "1990-01-01", 0)
    assert e.user(id_event="continue", id_explain_said="no") == "close_failed"


def test_identity_explanation_flag_set_by_extraction_or_code():
    # the agent explained on its own and the extractor saw it: the decline closes
    assert Engine(at="identity").user(id_event="declined", id_explain_said="yes", id_partial="") == "close_declined"
    # flag set in code by the explain step, kept even if extraction says no
    e2 = Engine(at="identity", v={"id_explained": "yes"})
    assert e2.user(id_event="declined", id_explain_said="no", id_partial="") == "close_declined"
    # the explain step's own turn cap returns to identity
    e = Engine(at="identity")
    assert e.user(id_event="declined", id_explain_said="no", id_partial="") == "id_explain"
    e.user()
    assert e.user() == "identity"


def test_identity_decline_is_explained_once_then_closes():
    e = Engine(at="identity")
    assert e.user(id_event="declined", id_partial="") == "id_explain"
    assert e.user(id_explain_reply="replied") == "identity"
    assert e.user(id_event="declined", id_partial="") == "close_declined"
    assert wf()["nodes"]["identity"]["max_node_turns"] == 20


def test_no_explanation_flag_without_a_declined_event():
    import copy
    s = copy.deepcopy(A)
    s["events"] = [ev for ev in s["events"] if ev["id"] != "declined"]
    n = wf(s)["nodes"]
    assert "id_decline_gate" not in n
    assert not [x for x in n["identity"]["extraction_schema"] if x["name"] == "id_explain_said"]


# ---------------------------------------------------------------- 3. opening: chatter is no ask

def test_opening_chatter_value_and_prompt():
    n = wf()["nodes"]["opening"]
    x = n["extraction_schema"][0]
    assert "chatter" in x["enum_values"] and x["enum_values"][-1] == "not_confirmed"
    assert "chatter: only small talk, or a message cut off mid-phrase, with no answer to the question and no question back." in x["description"]
    # chatter routes nowhere: the node stays and asks again
    assert not [t for t in n["transitions"] if "chatter" in json.dumps(t["condition"])]
    assert CUT_OFF in n["task_message"] and "Small talk with no answer uses no ask: reply in a few words, then ask again." in n["task_message"]
    assert n["max_node_turns"] == 2 + 1 + 5 and n["max_turn_transition"] == "close_unclear"


def test_pub02_replay_two_small_talk_turns_do_not_close():
    """9a7b8c8e: "I've had such a long day at work today", then "The traffic was absolutely terrible ... it took me",
    both not_confirmed, closed as unclear after 2 turns. As chatter they use no ask; the confirmation then goes on."""
    e = Engine()
    assert e.user(line_status="chatter") == "opening"
    assert e.user(line_status="chatter") == "opening"
    assert e.user(line_status="confirmed") == "identity"


def test_opening_two_unanswered_asks_still_close_as_unclear():
    e = Engine()
    assert e.user(line_status="not_confirmed") == "opening"
    assert e.v["line_status"] == "pending" and e.v["op_asks"] == "1"
    assert e.user(line_status="chatter") == "opening"
    assert e.user(line_status="chatter") == "opening"
    assert e.user(line_status="not_confirmed") == "close_unclear"
    v = {}
    exec(_code(e.n["close_unclear"]), {"vars": v})
    assert (v["outcome"], v["reason"]) == ("not_reached", "unclear")


def test_opening_extraction_miss_after_an_ask_is_not_counted_twice():
    e = Engine()
    e.user(line_status="not_confirmed")
    assert e.user() == "opening" and e.v["op_asks"] == "1"   # a failed extraction keeps the reset value


def test_opening_chatter_is_bounded_by_the_turn_cap():
    e = Engine()
    for _ in range(7):
        assert e.user(line_status="chatter") == "opening"
    assert e.user(line_status="chatter") == "close_unclear"


def test_third_party_cut_off_rule_and_larger_cap():
    for spec in (A, B):
        n = wf(spec)["nodes"]["third_party"]
        assert "- " + CUT_OFF in n["task_message"]
        assert n["max_node_turns"] == 20 and n["max_turn_transition"] == "close_no_callback"


def test_pub02_replay_twelve_fragments_no_longer_end_the_third_party_step():
    """14bc0a61: the husband answered; twelve fragments ("My cousin's wedding is on the" x5 ...), all continue, hit the
    12-turn cap and closed as no_callback. Now the step goes on; a time inside the window books the callback."""
    e = Engine(at="third_party")
    for _ in range(12):
        assert e.user(tp_status="continue") == "third_party"
    e.v["cb_status"] = "booked"     # schedule_callback's post-hook result
    assert e.user(tp_status="continue") == "close_callback"


def test_third_party_cap_still_ends_the_step():
    e = Engine(at="third_party")
    for _ in range(19):
        e.user(tp_status="continue")
    assert e.user(tp_status="continue") == "close_no_callback"


def test_after_record_steps_have_readable_names():
    n = wf(B)["nodes"]
    lab = {(k, t["target_node"]): t["description"] for k, nd in n.items() for t in nd.get("transitions") or []}
    assert lab[("hardship_offer_after_record", "hardship_hold_after_record")] == "Next: hardship reply, after-recording step"
    assert lab[("human_ask_after_record", "human_hold_after_record")] == "Next: human reply, after-recording step"
    assert lab[("human_back_after_record", "after_record")] == "Next: after-recording step"
    assert lab[("after_record", "after_record_done")] == "Otherwise: notes and close after recording"
    d = n["human_back_after_record"]["pre_actions"][0]["function"]["description"]
    assert d == "Clears the reply and returns to the after-recording step."
    import re
    assert not [v for v in lab.values() if re.search(r"\bafter record\b|\bhold\b", v)]


def test_opening_max_asks_follows_the_spec():
    import copy
    s = copy.deepcopy(A)
    s["opening"]["max_asks"] = 3
    e = Engine(s)
    assert e.user(line_status="not_confirmed") == "opening"
    assert e.user(line_status="not_confirmed") == "opening"
    assert e.user(line_status="not_confirmed") == "close_unclear"


# ---------------------------------------------------------------- 7. close_call: code-set outcome, closed until a close ran

def _close_tool(w):
    return [t for t in w["tools"] if t["function"]["name"] == "close_call"][0]


def _close_req(w, v, model_guess=("not_verified", "failed")):
    """The end_of_call run: unset {{outcome}}/{{reason}} are filled by one model call (probe 3a362703), then the
    pre-hook runs and may rewrite url and body."""
    t = _close_tool(w)
    props = t["function"]["parameters"]["properties"]
    body = {"customer_id": v.get("customer_id", "c1"),
            "outcome": v.get("outcome") or model_guess[0], "reason": v.get("reason") or model_guess[1]}
    cfg = {"url": props["url"]["default"], "body": body}
    exec(t["pre_process_code"], {"config": cfg, "vars": dict(v)})
    return cfg["url"], cfg["body"]


def test_close_call_tool_is_system_run_and_fails_closed():
    for spec in (A, B):
        t = _close_tool(wf(spec))
        assert t["config"]["alwaysRunsAt"] == ["end_of_call"]
        d = t["function"]["description"]
        assert "Run by the system at the end of the call; the assistant must never call it." in d
        assert t["function"]["parameters"]["properties"]["url"]["default"] == NOT_SENT_URL
        assert "{{" not in t["pre_process_code"] and "{{" not in t["post_process_code"]


def test_pub03_replay_hangup_in_identity_records_no_guessed_close():
    """ac424c99: after "It is 12 July 1991." the agent stalled; the customer hung up at 64 s and the end_of_call run
    sent a model-guessed not_verified/failed. Now no close node ran, so nothing reaches close_call."""
    w = wf()
    e = Engine(at="identity", v={"customer_id": "c_8d14cf10"})
    e.user(id_event="continue", id_explain_said="no", id_partial="12 July 1991", id_partial_field="date_of_birth")
    url, body = _close_req(w, e.v)
    assert url == NOT_SENT_URL and body == {"customer_id": "c_8d14cf10"}


def test_model_call_before_any_close_is_answered_not_now():
    w = wf()
    v = {"customer_id": "c1"}
    url, body = _close_req(w, v, ("promise", ""))
    assert url == NOT_SENT_URL and body == {"customer_id": "c1"}
    r = {"status": "success", "data": {"products": []}, "header": {}}
    exec(_close_tool(w)["post_process_code"], {"vars": v, "result": r})
    assert r["data"] == {"ok": False, "what_to_do_next": "Not now: the call is not finished; continue the conversation."}
    # after a close ran the post-hook leaves the bank's answer alone
    v["close_ready"] = "yes"
    r = {"status": "success", "data": {"ok": True}}
    exec(_close_tool(w)["post_process_code"], {"vars": v, "result": r})
    assert r["data"] == {"ok": True}


def test_every_close_node_sends_its_own_outcome_never_the_body():
    for spec in (A, B):
        w = wf(spec)
        for k, node in w["nodes"].items():
            if not k.startswith("close_") or node["node_type"] != "code":
                continue
            v = {"customer_id": "c1", "title": "Mr", "last_name": "Novak"}
            exec(_code(node), {"vars": v})
            assert v["close_ready"] == "yes", k
            url, body = _close_req(w, v, ("transferred", "human"))
            assert url == URL + "/close_call", k
            assert body["outcome"] == v["outcome"] and body.get("reason", "") in ("", v["reason"]), (k, body)
            if v["reason"] and v["outcome"] not in ("promise", "paid"):
                assert body["reason"] == v["reason"], k


def test_pub10_create_promise_retry_closes_with_the_right_outcome():
    """PUB-10: the first create_promise fails with an HTTP error -> retry_promise; worked -> promise, failed again ->
    the service-failure close. close_call sends what the close node wrote."""
    for ok, want in ((True, ("promise", None)), (False, ("no_promise", "service_error"))):
        w = wf()
        n = w["nodes"]
        v = {"customer_id": "c1", "title": "Mr", "last_name": "Novak",
             "create_promise_failure_count": "1", "create_promise_response.status": "error"}
        assert route(n["negotiate"], v) == "retry_promise"
        v["retry_ok"] = "true" if ok else "false"
        if not ok:
            v["create_promise_failure_count"] = "2"
        k = route(n["retry_promise"], v)
        assert k.startswith("close_"), k
        exec(_code(n[k]), {"vars": v})
        url, body = _close_req(w, v)
        assert url.endswith("/close_call") and (body["outcome"], body.get("reason")) == want, (ok, k, body)


def test_multi_wraps_close_with_the_wrap_outcome():
    from tests.test_multi import Sim
    s = Sim("PUB-12")
    s.say(product_id="p1", outcome="agreed", amount="510", date="2026-10-15")
    s.refuse("p2", 1)
    s.refuse("p2", 1)
    s.refuse("p3", 1)
    s.confirm()
    *_, close, outcome, reason = s.chain()
    s.run(close)
    url, body = _close_req(s.w, s.v)
    assert url.endswith("/close_call") and body["outcome"] == "promise" and "reason" not in body
    s = Sim("PUB-13")
    s.say(product_id="p1", outcome="paid")
    s.refuse("p2", 2)
    s.end = "wrap_done"
    *_, close, outcome, reason = s.chain()
    s.run(close)
    url, body = _close_req(s.w, s.v, ("promise", ""))
    assert (body["outcome"], body["reason"]) == ("no_promise", "refused")


def test_multi_hangup_at_the_readback_sends_no_guessed_close():
    """PUB-12 f242b760: a hang-up at the read-back sent a model-guessed promise with nothing recorded. Now nothing."""
    from tests.test_multi import Sim
    s = Sim("PUB-12")
    s.say(product_id="p1", outcome="agreed", amount="510", date="2026-10-15")
    url, body = _close_req(s.w, s.v, ("promise", ""))
    assert url == NOT_SENT_URL and set(body) == {"customer_id"}


# ---------------------------------------------------------------- fix round 1: no ledger record before a close

def test_unready_close_never_reaches_any_bank_route():
    """The bank ledger records every request to a tool route, get_account included, and graded checks count those
    ("verify_identity and get_account never called", "both security questions passed before get_account"). An
    unready close goes to a reserved .invalid host (probe 525bc2e7: fails at DNS in 0.02 s, ledger stays empty)."""
    from urllib.parse import urlparse
    host = urlparse(NOT_SENT_URL).hostname
    assert host.endswith(".invalid") and host != urlparse(URL).hostname
    for spec in (A, B):
        w = wf(spec)
        for v in ({"customer_id": "c1"}, {"customer_id": "c1", "outcome": "promise"}, {"customer_id": "c1", "close_ready": "no"}):
            url, body = _close_req(w, v, ("promise", ""))
            assert url == NOT_SENT_URL and body == {"customer_id": "c1"}, v


def test_promise_guards_get_account_route_only_after_verification():
    """A12's create_promise / check_date / set_product fail-closed route is get_account. Those tools live only in
    steps reached through load_account, whose only way in is identity's "All answers verified" edge, so a get_account
    there is after verification (graded checks allow it)."""
    from tests.test_outage import reach
    for spec in (A, B):
        w = wf(spec)
        n = w["nodes"]
        routed = {k for k, x in n.items() for t in (x.get("tools") or []) + (x.get("pre_actions") or [])
                  if str(t["function"]["parameters"]["properties"].get("url", {}).get("default", "")).endswith("/get_account")}
        assert routed >= {"load_account"} and len(routed) >= 2
        ins = [(k, t["description"]) for k, x in n.items() for t in x.get("transitions") or [] if t["target_node"] == "load_account"]
        ins += [(k, "default") for k, x in n.items() if x.get("passthrough_default_transition") == "load_account"
                or x.get("max_turn_transition") == "load_account"]
        assert ins == [("identity", "All answers verified")], ins
        before = reach(w, "setup", without={"load_account"})
        assert not (routed & before), routed & before
