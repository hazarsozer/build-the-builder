"""Task A18: verbatim opening vs reconcile, one sympathy sentence, multi-product restructuring, never-say words.
Fixtures are the 11 Oct bench run's extracted specs (outcomes, multi-raw, strict-wording)."""
import copy
import json

from builder import cases
from builder.compile import compile as C
from builder.lint import lint, reconcile_banned, required_pre_verify_sentences

INS = json.load(open("tests/fixtures/bench-insurance.spec.json"))
INS_OA = json.load(open("bench/insurance/openapi.json"))
OUT = json.load(open("tests/fixtures/bench-outcomes.spec.json"))
OUT_OA = json.load(open("bench/outcomes/openapi.json"))
MRW = json.load(open("tests/fixtures/bench-multi-raw.spec.json"))
MRW_OA = json.load(open("bench/multi-raw/openapi.json"))
OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))
URL = "https://bank.example/v1"


# ---- 1. A13 reconcile x A16 verbatim

def _ins_recorded(verbatim):
    s = copy.deepcopy(INS)
    s["opening"]["verbatim"] = verbatim
    s["opening"]["banned_before_verify"] = s["opening"]["banned_before_verify"] + ["recorded"]
    return s


def test_reconcile_reads_the_verbatim_first_sentence_unchanged():
    s = _ins_recorded(True)
    first = dict(required_pre_verify_sentences(s))["opening.first_sentence"]
    assert first == s["opening"]["first_sentence"]
    new, removed = reconcile_banned(s)
    assert [r["term"] for r in removed] == ["recorded"]
    assert "recorded" not in new["opening"]["banned_before_verify"]


def test_reconcile_reads_the_rephrased_sentence_without_verbatim():
    s = _ins_recorded(False)
    first = dict(required_pre_verify_sentences(s))["opening.first_sentence"]
    assert "recorded" not in first and "monitored and stored" in first
    _, removed = reconcile_banned(s)
    assert removed == []


def _ctx(spec):
    c = object.__new__(cases.Ctx)
    c.spec = spec
    return c


def test_case_check_never_bans_a_word_of_the_verbatim_opening():
    s = _ins_recorded(True)
    (line,) = _ctx(s).banned()
    assert "recorded" not in line and "premium" in line
    (line,) = _ctx(_ins_recorded(False)).banned()
    assert "recorded" in line


def test_case_check_unchanged_for_reference_specs():
    for s in (A, B):
        b = s["opening"]["banned_before_verify"]
        assert _ctx(s).banned() == ([f"none of these is said before identity is verified: {', '.join(b)}"] if b else [])


# ---- 2. one sentence of sympathy

def _hold(spec, oa, ev="hardship"):
    return C(spec, oa, base_url=URL)["nodes"][ev + "_hold"]["task_message"]


def test_fixed_sympathy_sentence_is_the_only_one():
    t = _hold(OUT, OUT_OA)
    assert "I am sorry to hear that." in t
    assert "Exactly one short sentence of sympathy" not in t
    assert "This is your one sentence of sympathy" in t
    # the fixed sentence already asks the yes/no question: no second offer step
    assert "as a yes/no question" not in t


def test_without_a_fixed_sentence_the_prompt_still_asks_for_one_sympathy_sentence():
    s = copy.deepcopy(OUT)
    for e in s["events"]:
        if e["id"] == "hardship":
            e["say"] = None
    t = _hold(s, OUT_OA)
    assert "Exactly one short sentence of sympathy." in t and "as a yes/no question" in t
    assert "This is your one sentence of sympathy" not in t


def test_fixed_sentence_without_sympathy_keeps_the_sympathy_step():
    s = copy.deepcopy(OUT)
    for e in s["events"]:
        if e["id"] == "hardship":
            e["say"] = "Would you like me to connect you with our customer support team?"
    t = _hold(s, OUT_OA)
    assert "Exactly one short sentence of sympathy." in t and "This is your one sentence" not in t


def test_multi_fixed_sympathy_sentence_without_question_keeps_the_offer_step():
    t = _hold(MRW, MRW_OA)
    assert "I am sorry to hear that." in t and "Exactly one short sentence of sympathy" not in t
    assert "as a yes/no question" in t


# ---- 3. multi-product restructuring

def _neg_desc(spec, oa):
    return C(spec, oa, base_url=URL)["nodes"]["negotiate"]["extraction_schema"][0]["description"]


def test_accepting_an_offered_amount_is_not_a_transfer_request():
    d = _neg_desc(MRW, MRW_OA)
    assert "Accepting an amount or a date the agent offered" in d and "is NOT restructuring: it is continue." in d


def _code(node):
    return node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def _route(node, v):
    """First transition of a node whose condition holds (equals / and / or), else its passthrough default."""
    def ok(c):
        if "logic" in c:
            r = [ok(x) for x in c["conditions"]]
            return all(r) if c["logic"] == "and" else any(r)
        return c["operator"] == "equals" and str(v.get(c["variable"])) == c["operand"]
    for t in node["transitions"]:
        if ok(t["condition"]):
            return t["target_node"]
    return node.get("passthrough_default_transition")


MRW03 = [{"product_id": "p1", "type": "credit_card", "card_last4": "5590", "past_due": 400.0, "days_late": 2, "minimum_payment": 120.0},
         {"product_id": "p2", "type": "personal_loan", "past_due": 1100.0, "days_late": 15, "instalment_amount": 275.0}]


def _mrw03_after_wrap(recorded=True):
    w = C(MRW, MRW_OA, base_url=URL)
    v = {"now": "2026-11-20T13:30", "acct_products_json": json.dumps(MRW03)}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    st = json.loads(v["mp_state"])
    st["p"]["p1"].update(status="agreed", amount=400.0, date="2026-11-27")
    st["p"]["p2"]["status"] = "declined"
    v["mp_state"] = json.dumps(st)
    exec(_code(w["nodes"]["record_plan"]), {"vars": v})
    if recorded:
        v["rec1_status"] = "recorded"
    exec(_code(w["nodes"]["wrap_recorded"]), {"vars": v})
    return w, v


def test_mrw03_recorded_promise_then_restructuring_transfers_without_notes():
    """MRW-03: the recorded sentence and the goodbye were one engine turn, so the request made after "recorded"
    was never heard. Now: recorded -> after_record (a turn) -> restructuring -> transfer, before any note."""
    w, v = _mrw03_after_wrap()
    n = w["nodes"]
    assert v["mp_recorded"] == "yes" and v["note_has_1"] == "yes"
    assert _route(n["wrap_recorded"], v) == "after_record"
    ar = n["after_record_step"]
    assert ar["on_enter"]["text"].startswith("{{mp_said}} ")
    assert "credit card ending 5590 is recorded" in v["mp_said"]
    t = _route(ar, dict(v, ar_answer="restructuring"))
    assert t.startswith("transfer_") and n[t]["passthrough_default_transition"] == "close_restructuring"
    # anything else: the recorded sentence is not repeated by the close, the notes are then written
    assert _route(ar, dict(v, ar_answer="nothing")) == "after_record_done"
    assert _route(ar, dict(v, ar_answer="other")) is None  # stays: answers, asks again (no close on an unread reply)
    vv = dict(v)
    exec(_code(n["after_record_done"]), {"vars": vv})
    assert vv["mp_said"] == "" and _route(n["after_record_done"], vv) == "note_1"
    assert ar["max_turn_transition"] == "after_record_done"
    assert [p for p in lint(w, MRW) if not p.startswith("warning:") and "after_record" in p] == []


def test_nothing_recorded_skips_the_after_record_turn():
    w, v = _mrw03_after_wrap(recorded=False)
    assert v["mp_recorded"] == "no" and _route(w["nodes"]["wrap_recorded"], v) != "after_record"


STW = json.load(open("tests/fixtures/bench-strict-wording.spec.json"))
STW_OA = json.load(open("bench/strict-wording/openapi.json"))
WORDS = ["guarantee", "lawsuit", "credit score", "final notice"]


def _stw(words=WORDS):
    s = copy.deepcopy(STW)
    if words is not None:
        s["never_say"] = list(words)
    return s


def test_never_say_words_in_every_conversation_step_and_the_global_prompt():
    s = _stw()
    w = C(s, STW_OA, base_url=URL)
    conv = {k: n for k, n in w["nodes"].items() if n["node_type"] == "conversation"}
    assert {"negotiate", "opening", "identity"} <= set(conv)
    for k, n in conv.items():
        assert 'Never say "guarantee", "lawsuit", "credit score" or "final notice", in any form' in n["task_message"], k
    assert 'Never say "guarantee"' in w["system_prompt"]
    # banned offers: every step after verification, none before it
    post = [k for k in conv if k not in ("opening", "identity", "third_party") and not k.endswith(("_opening", "_identity"))]
    assert "negotiate" in post and len(post) >= 2
    for k in post:
        t = conv[k]["task_message"]
        assert "Never offer a discount" in t or "cannot offer" in t, k
    assert any("Never offer a discount" in conv[k]["task_message"] for k in post)
    assert "Never offer" not in conv["opening"]["task_message"] and "Never offer" not in conv["identity"]["task_message"]
    assert [e for e in lint(w, s) if not e.startswith("warning:")] == []


def test_never_say_in_multi_hold_and_after_record_steps():
    s = copy.deepcopy(MRW)
    s["never_say"] = ["guarantee"]
    n = C(s, MRW_OA, base_url=URL)["nodes"]
    for k in ("negotiate", "readback", "hardship_hold", "after_record_step"):
        assert 'Never say "guarantee"' in n[k]["task_message"], k


def test_lint_flags_a_never_say_word_in_fixed_text():
    s = _stw()
    for r in s["say_rules"]:
        r["say"] = "I can guarantee nothing about that."
        break
    errs = lint(C(s, STW_OA, base_url=URL), s)
    assert any("never-say 'guarantee'" in e for e in errs)
    s = _stw()
    s["confirmation"]["success_sentence"] = "This is your final notices reminder."
    errs = lint(C(s, STW_OA, base_url=URL), s)
    assert any("never-say 'final notices'" in e and "close_" in e for e in errs)


def test_without_never_say_nothing_changes_but_offers():
    w = C(_stw(None), STW_OA, base_url=URL)
    assert "in any form (plural, past tense)" not in json.dumps(w)
    for s in (A, B):
        assert "never_say" not in s
        w = C(s, OA)
        assert "in any form (plural, past tense)" not in json.dumps(w) and lint(w, s) == []


def test_never_say_schema_validates_and_specdiff_unions():
    from builder.validate import validate
    from builder.specdiff import diff_specs
    assert validate(_stw(), STW_OA) == validate(_stw(None), STW_OA)
    bad = _stw()
    bad["never_say"] = "guarantee"
    assert validate(bad, STW_OA) != validate(_stw(None), STW_OA)
    assert diff_specs(_stw(None), _stw([])) == diff_specs(_stw(None), _stw(None))


def test_self_test_cases_check_never_say():
    assert _ctx(_stw()).never_say() == [
        "the agent never says any of these, in any form, even after the customer does: guarantee, lawsuit, credit score, final notice"]
    assert _ctx(_stw(None)).never_say() == [] and _ctx(A).never_say() == []


# ---- fix round 1

TRANSFERISH = ("transfer", "insist_once_then_transfer", "offer_transfer_else_close")


def test_after_record_only_with_a_transfer_ask_once_or_support_event():
    s = copy.deepcopy(MRW)
    s["events"] = [e for e in s["events"] if e["action"] not in TRANSFERISH]
    s["outcomes"] = [o for o in s["outcomes"] if o["event"] in {e["id"] for e in s["events"]}]
    assert "after_record" not in C(s, MRW_OA, base_url=URL)["nodes"]
    for keep in TRANSFERISH:
        s = copy.deepcopy(MRW)
        s["events"] = [e for e in s["events"] if e["action"] not in TRANSFERISH or e["action"] == keep]
        s["outcomes"] = [o for o in s["outcomes"] if o["event"] in {e["id"] for e in s["events"]}]
        assert "after_record" in C(s, MRW_OA, base_url=URL)["nodes"], keep


def test_human_request_after_recording_asks_once_then_transfers_without_notes():
    w, v = _mrw03_after_wrap()
    n = w["nodes"]
    ar = n["after_record_step"]
    assert {"human_first_request", "human_insists", "hardship"} <= set(ar["extraction_schema"][0]["enum_values"])
    ask = _route(ar, dict(v, ar_answer="human_first_request"))
    assert ask == "human_ask_after_record"
    vv = dict(v)
    exec(_code(n[ask]), {"vars": vv})
    hold = _route(n[ask], vv)
    assert hold == "human_hold_after_record" and vv["human_asked"] == "yes"
    t = _route(n[hold], dict(vv, human_reply="insists"))
    assert t.startswith("transfer_") and n[t]["passthrough_default_transition"] == "close_human"
    # accepts help: back to after_record (the recorded sentence is not spoken again)
    back = _route(n[hold], dict(vv, human_reply="other"))
    assert back == "human_back_after_record" and _route(n[back], vv) == "after_record"
    assert ar["on_enter_first_entry_only"] is True
    # insists straight away after being asked once: transfer
    assert _route(ar, dict(vv, ar_answer="human_insists")).startswith("transfer_")
    assert "note_1" not in (t, back)


def test_support_event_after_recording_offers_then_transfers_or_ends_as_nothing_else():
    w, v = _mrw03_after_wrap()
    n = w["nodes"]
    offer = _route(n["after_record_step"], dict(v, ar_answer="hardship"))
    assert offer == "hardship_offer_after_record"
    vv = dict(v)
    exec(_code(n[offer]), {"vars": vv})
    assert "mp_phase" not in _code(n[offer])  # the negotiation is not reopened
    hold = _route(n[offer], vv)
    assert hold == "hardship_hold_after_record"
    t = _route(n[hold], dict(vv, hardship_reply="accepted"))
    assert t.startswith("transfer_") and n[t]["passthrough_default_transition"] == "close_hardship_transfer"
    for r in ("declined", "names_payment", "other"):
        assert _route(n[hold], dict(vv, hardship_reply=r)) == "after_record_done", r
    assert n[hold]["max_turn_transition"] == "after_record_done"
    # the negotiate offer is untouched
    assert n["hardship_hold"]["max_turn_transition"] == "wrap_hardship"


def test_safe_pre_success_rephrases_record_words():
    from builder.lint import safe_pre_success as f
    assert f("A recorded promise cannot be changed in this call; you can change it in the mobile app.") == (
        "A promise cannot be changed in this call; you can change it in the mobile app.", True)
    assert f("We have recorded your promise.")[0] == "We have taken your promise."
    assert f("Record the payment date now.")[0] == "Take the payment date now."
    assert f("Your promise is already recorded.")[0] == "Your promise is already noted."
    assert f("The payment date has been recorded.")[0] == "The payment date has been noted."
    assert f("You can change it in the mobile app.") == ("You can change it in the mobile app.", False)


def test_safe_pre_success_keeps_every_other_record_word():
    """Fix round 2: nouns and non-promise uses keep their meaning; lint flags them instead."""
    from builder.lint import safe_pre_success as f
    for t in ("Our records show a balance.", "Our records show payments.", "For the record, I am a digital assistant.",
              "The payment record is old.", "This call is being recorded.", "We record every call.", "A recorded line.",
              "Your records are up to date."):
        assert f(t) == (t, False), t


def test_change_note_before_success_is_rephrased_and_lints_clean():
    for spec, oa in ((MRW, MRW_OA), (STW, STW_OA)):
        s = copy.deepcopy(spec)
        s["confirmation"]["change_note"] = "A recorded promise cannot be changed in this call; you can change it in the mobile app."
        w = C(s, oa, base_url=URL)
        assert '"A promise cannot be changed in this call; you can change it in the mobile app."' in w["nodes"]["negotiate"]["task_message"]
        assert [e for e in lint(w, s) if not e.startswith("warning:")] == []
    # after the promise is recorded the brief's sentence is said as written
    s = copy.deepcopy(MRW)
    s["confirmation"]["change_note"] = "A recorded promise cannot be changed in this call."
    assert '"A recorded promise cannot be changed in this call."' in C(s, MRW_OA, base_url=URL)["nodes"]["after_record_step"]["task_message"]


def test_rephrase_skips_the_verbatim_first_sentence_and_leaves_reference_specs_alone(monkeypatch):
    from builder import compile as comp
    s = copy.deepcopy(INS)
    s["opening"]["verbatim"] = True
    w = C(s, INS_OA, base_url=URL)
    assert w["nodes"]["opening"]["on_enter"]["text"].startswith(s["opening"]["first_sentence"] + " ")
    base = {k: C(x, OA) for k, x in (("a", A), ("b", B))}
    monkeypatch.setattr(comp._Compiler, "safe_before_success", lambda self, nodes: None)
    for k, x in (("a", A), ("b", B)):
        assert json.dumps(C(x, OA), sort_keys=True) == json.dumps(base[k], sort_keys=True)


def test_after_record_visits_are_bounded_in_code():
    """Fix round 2: wrap -> after_record (counter) -> after_record_step; an ask-once step that returns comes back
    through the counter; the 4th entry ends as "nothing else" whatever max_node_turns does."""
    w, v = _mrw03_after_wrap()
    n = w["nodes"]
    assert n["after_record"]["node_type"] == "code" and n["after_record_step"]["node_type"] == "conversation"
    assert n["human_back_after_record"]["passthrough_default_transition"] == "after_record"
    seen = []
    for _ in range(5):
        exec(_code(n["after_record"]), {"vars": v})
        seen.append(_route(n["after_record"], v))
    assert seen == ["after_record_step"] * 3 + ["after_record_done"] * 2
    # a failed counter (no write) ends the step: the default is after_record_done
    assert _route(n["after_record"], {}) == "after_record_done"


def test_support_already_offered_in_negotiate_is_not_offered_again_after_recording():
    w, v = _mrw03_after_wrap()
    step = w["nodes"]["after_record_step"]
    assert v["hardship_offered"] == "no"
    assert _route(step, dict(v, ar_answer="hardship")) == "hardship_offer_after_record"
    assert _route(step, dict(v, ar_answer="hardship", hardship_offered="yes")) == "after_record_done"


def test_after_record_support_hold_has_no_take_their_payment_rule():
    n = C(MRW, MRW_OA, base_url=URL)["nodes"]
    after, neg = n["hardship_hold_after_record"]["task_message"], n["hardship_hold"]["task_message"]
    assert "The brief's rule for this situation" in neg and "date or amount" in neg
    assert "The brief's rule for this situation" not in after and "no new promise is taken" in after
    assert "take their payment" not in after


def test_question_offer_sentence_ends_the_turn():
    """The cannot-offer step comes before a fixed sentence that is the offer question."""
    t = _hold(OUT, OUT_OA)
    steps = [l for l in t.splitlines() if l[:2] in ("1.", "2.", "3.")]
    assert steps[0].startswith("1. Only if they asked for") and steps[1].startswith('2. Say "I am sorry to hear that. Would you like')
    t = _hold(MRW, MRW_OA)  # no question in the sentence: say, cannot-offer, offer
    steps = [l for l in t.splitlines() if l[:2] in ("1.", "2.", "3.")]
    assert steps[0].startswith('1. Say "I am sorry') and steps[2].startswith("3. Offer to connect")


