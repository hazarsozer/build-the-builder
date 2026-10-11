"""Task A10: hardship latch (single and multi product) and customer-style robustness.

Offline engine replay: conversation-node transitions are evaluated in order (tool-call transitions only when the
model calls them), code nodes run for real and route on their conditions, `<node>.total_turns` counts the user
turns spent in a node (the engine's own counter, as in the opening's second-ask route)."""
import json

from builder.compile import compile as C
from builder.lint import lint

OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))
CARD = {"product_id": "p1", "type": "personal_loan", "past_due": 780.0, "days_late": 21, "instalment_amount": 260.0}


def _code(node):
    return node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def holds(c, v):
    if c.get("operator") == "always":
        return True
    if "logic" in c:
        r = [holds(x, v) for x in c["conditions"]]
        return any(r) if c["logic"] == "or" else all(r)
    val = v.get(c["variable"])
    if c["operator"] == "equals":
        return val is not None and str(val) == str(c["operand"])
    if c["operator"] == "greater_than":
        try:
            return float(val) > float(c["operand"])
        except (TypeError, ValueError):
            return False
    raise AssertionError(c)


def route(node, v):
    for t in node.get("transitions") or []:
        if not t.get("is_tool_call") and holds(t["condition"], v):
            return t["target_node"]
    return node.get("passthrough_default_transition")


class Call:
    def __init__(self, spec=A, products=(CARD,)):
        self.w = C(spec, OA)
        self.n = self.w["nodes"]
        self.v = {"customer_id": "c1", "first_name": "Tomas", "last_name": "Novak", "title": "Mr", "now": "2026-10-14T11:00",
                  "acct_products_json": json.dumps(list(products)), "acct_first_json": json.dumps(products[0])}
        self.run("setup")
        self.run("prepare_account")
        self.at = "negotiate"
        self.turns = {}

    def run(self, k):
        exec(_code(self.n[k]), {"vars": self.v})

    def passthrough(self, k):
        """Run code nodes from k until a conversation node or a close; returns where the call stops."""
        guard = 0
        self.path = [k]
        while k in self.n and self.n[k]["node_type"] == "code" and not k.startswith("close_") and guard < 20:
            guard += 1
            self.run(k)
            k = route(self.n[k], self.v)
            self.path.append(k)
        return k

    def user(self, **extracted):
        """One customer turn in the current conversation node: extracted values (a failed extraction is simply absent)."""
        node = self.n[self.at]
        self.turns[self.at] = self.turns.get(self.at, 0) + 1
        self.v[self.at + ".total_turns"] = self.turns[self.at]
        self.v.update(extracted)
        t = route(node, self.v)
        if t is None:
            return self.at
        self.at = self.passthrough(t)
        return self.at

    def tool(self, target):
        """The model calls transition_to_<target>."""
        names = [t["target_node"] for t in self.n[self.at]["transitions"] if t.get("is_tool_call")]
        assert target in names, names
        self.at = self.passthrough(target)
        return self.at

    def close(self):
        """Run close (and redirect) nodes until goodbye: the last close's outcome, reason and farewell stand."""
        k, seen = self.at, []
        while k != "goodbye":
            seen.append(k)
            self.run(k)
            k = route(self.n[k], self.v)
        return self.v["outcome"], self.v.get("reason") or None, seen


# ---------------------------------------------------------------- item 1: single product

def test_pub05_replay_low_confidence_decline_ends_with_hardship_close():
    c = Call()
    assert c.v["hardship_offered"] == "no" and "{{hardship_rule}}" in c.n["negotiate"]["task_message"]
    # "I lost my job last month and cannot pay anything. Can I get a discount?" -> hardship (1.0), seen yes
    assert c.user(neg_event="hardship", hardship_seen="yes") == "hardship_hold"
    assert c.v["hardship_offered"] == "yes" and "It was made." in c.v["hardship_rule"]
    hold = c.n["hardship_hold"]
    assert hold["on_enter"] == {"type": "llm"} and "No tool call" in hold["task_message"]
    assert "Offer once to connect them with the support team" in hold["task_message"]
    assert "If they asked for a discount or a waiver: say you cannot offer it." in hold["task_message"]
    # "I don't want to be transferred. Is there anything else you can do for me?" -> extraction_failed (0.49)
    assert c.user() == "close_hardship"
    assert c.close()[:2] == ("no_promise", "hardship")


def test_latch_every_non_payment_reply_ends_with_hardship_close():
    for reply in ("other", "pending"):
        c = Call()
        c.user(neg_event="hardship", hardship_seen="yes")
        assert c.user(hardship_reply=reply) == "close_hardship", reply
    c = Call()
    c.user(neg_event="hardship", hardship_seen="yes")
    assert c.user(hardship_reply="accepted") == "transfer_support"
    assert c.n["transfer_support"]["transitions"][-1]["target_node"] == "close_hardship_transfer"


def test_latch_named_payment_returns_to_negotiate_then_refusals_close_as_hardship():
    for later in ("cannot_pay_or_too_much", "discount_request", "hardship", "hardship_declined"):
        c = Call()
        c.user(neg_event="hardship", hardship_seen="yes")
        assert c.user(hardship_reply="names_payment") == "negotiate"
        assert c.v["neg_event"] == "continue"
        # a date and the read-back are "continue": the promise path stays open
        assert c.user(neg_event="continue") == "negotiate"
        assert c.user(neg_event=later) == "close_hardship", later
    c = Call()
    c.user(neg_event="hardship", hardship_seen="yes")
    c.user(hardship_reply="names_payment")
    assert c.user(neg_event="hardship_accepted") == "transfer_support"


def test_latch_model_refused_and_turn_cap_end_as_hardship():
    c = Call()
    c.user(neg_event="hardship", hardship_seen="yes")
    c.user(hardship_reply="names_payment")
    c.tool("close_refused")
    assert c.close() == ("no_promise", "hardship", ["close_refused", "close_hardship"])
    c = Call()
    c.user(neg_event="hardship", hardship_seen="yes")
    c.user(hardship_reply="names_payment")
    c.tool("close_not_talk")
    assert c.close()[:2] == ("no_promise", "hardship")
    # the turn cap goes to close_refused, which redirects the same way
    assert c.n["negotiate"]["max_turn_transition"] == "close_refused"
    # without an offer, refused stays refused
    c = Call()
    c.tool("close_refused")
    assert c.close() == ("no_promise", "refused", ["close_refused"])


def test_latch_missed_event_value_but_seen_still_makes_the_offer_once():
    c = Call()
    # neg_event extraction failed on the hardship message, hardship_seen (sticky) did not
    assert c.user(hardship_seen="yes") == "hardship_hold"
    c = Call()
    c.user(neg_event="hardship", hardship_seen="yes")
    c.user(hardship_reply="names_payment")
    # hardship mentioned again after the offer: never a second offer
    assert c.user(neg_event="continue", hardship_seen="yes") == "negotiate"


def test_negotiate_prompt_no_longer_asks_the_model_to_offer():
    t = C(A, OA)["nodes"]["negotiate"]["task_message"]
    assert "spoken automatically in its own step; never make it yourself" in t
    assert "Make this offer only ONCE" not in t


# ---------------------------------------------------------------- item 1: several products

def test_multi_latch_decline_and_goodbye_end_in_wrap_hardship():
    prods = [dict(CARD, product_id="p1"), {"product_id": "p2", "type": "car_loan", "past_due": 140.0, "days_late": 7,
                                           "instalment_amount": 46.0}]
    for reply in ("other", "pending"):
        c = Call(B, prods)
        assert c.user(neg_event="hardship", hardship_seen="yes") == "hardship_hold"
        c.user(hardship_reply=reply)
        assert c.path[0] == "wrap_hardship" and c.v["mp_close"] == "close_hardship"
    c = Call(B, prods)
    c.user(neg_event="hardship", hardship_seen="yes")
    assert c.user(hardship_reply="names_payment") == "negotiate"
    assert "Make no offer of your own" in c.v["mp_msg"]
    c.user(neg_event="goodbye")
    assert c.path[0] == "wrap_hardship" and c.v["mp_close"] == "close_hardship"
    c = Call(B, prods)
    c.user(neg_event="hardship", hardship_seen="yes")
    c.user(hardship_reply="names_payment")
    # the turn cap / goodbye path through negotiate_end with nothing agreed: wrap_done redirects to wrap_hardship
    c.at = "negotiate_end"
    c.passthrough("negotiate_end")
    assert c.path[:3] == ["negotiate_end", "wrap_done", "wrap_hardship"] and c.v["mp_close"] == "close_hardship"
    assert {(c.v["note1_pid"], c.v["note1_reason"]), (c.v["note2_pid"], c.v["note2_reason"])} == {("p1", "hardship"), ("p2", "hardship")}


def test_multi_readback_hardship_goes_to_the_offer_step():
    w = C(B, OA)
    rb = w["nodes"]["readback"]
    t = [x for x in rb["transitions"] if '"hardship"' in json.dumps(x["condition"])]
    assert t and t[0]["target_node"] == "hardship_offer"
    v = {}
    exec(_code(w["nodes"]["hardship_offer"]), {"vars": v})
    assert v["mp_phase"] == "open" and v["rb_answer"] == "pending" and v["hardship_offered"] == "yes"


def test_reference_specs_lint_clean_with_latch():
    for s in (A, B):
        assert lint(C(s, OA), s) == []


# ---------------------------------------------------------------- item 2: SELF-06 (wrong answers in words)

DRESS_A = "tests/fixtures/a10-dress-A.spec.json"


def _verify(w, v, field, answer, left, verified=False):
    """One verify_identity call as the platform runs it (pre-hook, bank, post-hook, response mapping)."""
    t = w["nodes"]["identity"]["tools"][0]
    cfg = {"url": "x", "body": {"field": field, "answer": answer}}
    exec(t["pre_process_code"], {"vars": v, "config": cfg})
    r = {"status": "success", "data": {"ok": True, "verified": verified, "wrong_answers_left": left, "all_verified": False}}
    exec(t["post_process_code"], {"vars": v, "result": r})
    v["id_left"] = str(left)
    return cfg["body"]


def test_self06_replay_answer_with_a_question_is_checked_not_declined():
    for spec in (A, json.load(open(DRESS_A))):
        w = C(spec, OA)
        n = w["nodes"]
        v = {}
        # two wrong dates in words, each checked (the post-hook spends the parts)
        v.update(id_partial="the eighteenth of April, one nine five four", id_partial_field="date_of_birth", id_event="continue")
        assert _verify(w, v, "date_of_birth", "1954-04-18", 2)["answer"] == "1954-04-18"
        v.update(id_partial="The eighteenth of May, one nine five five", id_partial_field="date_of_birth", id_event="continue")
        _verify(w, v, "date_of_birth", "1955-05-18", 1)
        # "the eighteenth of July, one nine five six. This is ridiculous, how much do I owe?" -> declined (0.78)
        v.update(id_event="declined", id_partial="the eighteenth of July, one nine five six", id_partial_field="date_of_birth")
        k = route(n["identity"], v)
        assert k == "id_decline_gate"
        exec(_code(n[k]), {"vars": v})
        assert route(n[k], v) == "identity" and v["id_event"] == "continue"
        # back in identity the model checks it: the third wrong answer closes as failed
        _verify(w, v, "date_of_birth", "1956-07-18", 0)
        assert route(n["identity"], v) == "close_failed"
        f = {}
        exec(_code(n["close_failed"]), {"vars": f})
        assert (f["outcome"], f["reason"]) == ("not_verified", "failed")
        assert "Please use the mobile app or visit a branch." in f["farewell"]


def test_identity_decline_without_new_answer_still_closes():
    w = C(A, OA)
    n = w["nodes"]
    v = {"id_event": "declined", "id_partial": "", "id_partial_spent": "14 March 1988"}
    k = route(n["identity"], v)
    exec(_code(n[k]), {"vars": v})
    assert route(n[k], v) == "close_declined"
    # parts the gate already sent back once do not send it back again
    v = {"id_event": "declined", "id_partial": "14 March"}
    exec(_code(n["id_decline_gate"]), {"vars": v})
    assert route(n["id_decline_gate"], v) == "identity"
    v["id_event"] = "declined"
    exec(_code(n["id_decline_gate"]), {"vars": v})
    assert route(n["id_decline_gate"], v) == "close_declined"
    d = n["identity"]["extraction_schema"][0]["description"]
    assert "in digits or in words, right or wrong) is continue, never a decline" in d


# ---------------------------------------------------------------- item 1: neg_event audit (single product)

def test_audit_paid_and_dispute_have_a_model_backup_close():
    c = Call()
    tools = {t["target_node"]: t for t in c.n["negotiate"]["transitions"] if t.get("is_tool_call")}
    assert {"close_paid", "close_dispute", "close_refused", "close_not_talk"} <= set(tools)
    t = c.n["negotiate"]["task_message"]
    assert "Do not argue and do not take a promise. Then call transition_to_close_dispute." in t
    assert "Take no promise. Then call transition_to_close_paid." in t
    # a paid claim after the support offer keeps its own outcome (no latch redirect on paid/dispute)
    c.user(neg_event="hardship", hardship_seen="yes")
    c.user(hardship_reply="names_payment")
    c.tool("close_paid")
    assert c.close()[:2] == ("paid", None)
    # several products: paid/dispute are per-product set_product outcomes, no extra tools
    assert [t["target_node"] for t in C(B, OA)["nodes"]["negotiate"]["transitions"] if t.get("is_tool_call")] == ["wrap_not_talk"]


# ---------------------------------------------------------------- item 2: SELF-04 (corrects, after_deadline)

def test_self04_after_deadline_keeps_the_current_attempt_amount():
    for spec in (A, json.load(open(DRESS_A))):
        t = C(spec, OA)["nodes"]["negotiate"]["task_message"]
        assert ("offer it with the amount of your current attempt. Their own date, even after a \"no\", is never a refusal." in t)
        # A9 rules the candidate-1 build lacked, which the SELF-04 cut-off corrections needed
        assert 'say only "Go on."' in t and "never ask them to name an amount" in t
    s = json.loads(json.dumps(A))
    s["negotiation"]["after_deadline"] = "reject"
    assert "current attempt" not in C(s, OA)["nodes"]["negotiate"]["task_message"].split("A date after the deadline")[1][:200]


# ---------------------------------------------------------------- item 2: SELF-11 (injection persona broke role)

def test_self11_injection_persona_is_framed_as_a_role_line():
    from builder import cases as CS
    spec = json.load(open(DRESS_A))
    inj = [c for c in CS.generate(spec, OA, "2026-10-14T11:00") if c["scenario"] == "injection"][0]
    sc = inj["script"]
    assert "not an instruction to you" in sc and "First confirm the name." in sc
    assert sc.index("First confirm the name.") < sc.index(CS.INJECTION) < sc.index("Answers the security questions correctly")
