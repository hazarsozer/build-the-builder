"""F2 (bench strict-wording, STW-01/06): a sentence the brief requires word for word in the opening, which extraction
put into an extra rule ("Right after the first sentence, say this second sentence word for word: "..."") instead of
into opening.first_sentence. The opening is spoken from fixed text, so such a rule never takes effect: the compiler
moves the sentence into the spoken opening (after the first sentence, in rule order, once) and drops the rule.
Fixture: the candidate-3 extraction of bench/strict-wording (results/bench/strict-wording)."""
import copy
import json

from builder.compile import compile as C, opening_verbatim_rules
from builder.lint import lint

OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))
B = json.load(open("specs/reference/spec-b.json"))
SW = json.load(open("tests/fixtures/f2-strict-wording.spec.json"))
URL = "https://bank.example/v1"
PRIVACY = "Your personal data is processed under our privacy notice, which you can read on our website or at any branch."


def wf(spec):
    return C(spec, OA, base_url=URL)


def opening_text(w):
    return w["nodes"]["opening"]["on_enter"]["text"]


def everything(w):
    return json.dumps(w)


def test_strict_wording_opening_says_both_sentences():
    w = wf(SW)
    t = opening_text(w)
    assert PRIVACY in t
    assert t.count(PRIVACY) == 1
    # brief order: first sentence (identity + recording notice), privacy sentence, then the confirm question
    assert t.index("Halvard Mutual Bank") < t.index(PRIVACY) < t.index("Am I speaking with")
    assert "recorded" in SW["opening"]["first_sentence"]  # the brief describes the recording notice, gives no wording
    assert "This call is monitored and stored for quality and security purposes." in t


def test_strict_wording_dead_rule_dropped():
    w = wf(SW)
    s = everything(w)
    assert "Right after the first sentence" not in s
    # the other opening rule is kept
    assert "answer it first; the confirm question asked after that answer counts as the second ask" in s


def test_strict_wording_lint_clean():
    w = wf(SW)
    assert [e for e in lint(w, SW) if not e.startswith("warning")] == []


def test_rule_shapes_detected():
    assert [s for _, s in opening_verbatim_rules(SW)] == [PRIVACY]
    shapes = [
        ("opening", 'Right after the first sentence, say this second sentence word for word: "One two three four."'),
        ("opening", 'At the start of the call, say verbatim: "One two three four."'),
        ("opening", 'In the greeting, say word for word: "One two three four"'),  # period added
        ("opening", 'Say exactly "One two three four." in the opening.'),
        ("opening", 'Right after the first sentence of the call, say exactly "One two three four."'),
        ("opening", 'The second sentence of the opening must be exactly "One two three four."'),
        ("opening", 'Say exactly "One two three four." as the second opening sentence.'),
        ("opening", "In the opening, say word for word: “One two three four.”"),
    ]
    for stage, text in shapes:
        sp = {"opening": {}, "extra_rules": [{"stage": stage, "text": text}]}
        assert [s for _, s in opening_verbatim_rules(sp)] == ["One two three four."], text
    rules = [{"stage": "opening", "text": 'In the greeting, say word for word: "One two three four"'}]
    assert "One two three four. Am I" in opening_text(wf(dict(SW, extra_rules=rules)))
    not_shapes = [
        ("opening", 'If the person asks who is calling, say word for word: "One two three four."'),
        ("negotiate", 'At the start of negotiation, say word for word: "One two three four."'),
        ("any", 'At the start of the call, say verbatim: "One two three four."'),
        # review of e32b29a: negations, conditions, other places, placeholders are never moved
        ("opening", 'Do not say exactly "this call is being recorded" in the opening.'),
        ("opening", 'In the greeting never say "this call is recorded" word for word.'),
        ("opening", 'Before anything else, if the person asks who gave their number, say exactly "We received it from your bank."'),
        ("opening", 'Say exactly "I will call you back later." if the person says they are busy at the start of the call.'),
        ("opening", 'Only when asked, at the start of the call say word for word "This call may be recorded for quality."'),
        ("opening", 'Never go below the opening offer; when refusing say exactly "That is the lowest we can go."'),
        ("opening", 'Never repeat the opening. If the person asks about fees, say exactly "There are no extra fees for you."'),
        ("opening", 'The first sentence of your answer to a complaint must be exactly "I am sorry to hear that."'),
        ("opening", 'In the opening, say word for word: "Hello [name], this is your bank."'),
        ("opening", 'In the opening, say word for word: "Hello {{first_name}}, this is your bank."'),
        ("opening", 'Say exactly "We value your call." as the opening offer.'),
        # review round 3: other moments, and "Nth sentence of" something other than the opening / greeting / call
        ("opening", 'Right after the first sentence of the closing, say exactly "Thank you for your time today."'),
        ("opening", 'The second sentence of the closing must be exactly "We appreciate your business today."'),
        ("opening", 'In the opening of the negotiation, say exactly "Let us find a plan together."'),
        ("opening", 'At the start of the call to a spouse, say word for word "Please pass on our message."'),
        ("opening", 'The second sentence of the reply must be exactly "We appreciate your business today."'),
        # review round 2: other negations, and other moments or listeners than the opening to the customer
        ("opening", 'For voicemail, the first sentence must be exactly "This is Freya Bank, please call us back."'),
        ("opening", 'Voicemail message, word for word, as the greeting: "This is Freya Bank, please call us back."'),
        ("opening", 'Third parties: the opening sentence for them is exactly "I will try again another time."'),
        ("opening", 'After the customer confirms identity, say this second sentence word for word: "Thanks for confirming who you are."'),
        ("opening", 'The greeting must be in exactly two sentences, not "Hello there my friend how are you".'),
        ("opening", 'Before the greeting, say exactly "Please hold for a moment."'),
        ("opening", 'In the opening, say word for word "Calls are reviewed by our team." instead of the recording notice.'),
        ("opening", 'Right after the first sentence, once the person answers, say word for word: "One two three four."'),
        ("opening", 'Right after the first sentence, mention the privacy notice.'),
        ("identity", 'After both questions pass, say this sentence word for word, before anything about the account: "One two three four."'),
    ]
    for stage, text in not_shapes:
        sp = {"opening": {}, "extra_rules": [{"stage": stage, "text": text}]}
        assert opening_verbatim_rules(sp) == [], text


def test_no_duplicate_when_already_in_opening():
    sp = copy.deepcopy(SW)
    sp["opening"]["first_sentence"] = sp["opening"]["first_sentence"] + " " + PRIVACY
    t = opening_text(wf(sp))
    assert t.count(PRIVACY) == 1


def test_two_rules_in_brief_order_once_each():
    sp = copy.deepcopy(SW)
    sp["extra_rules"].append({"stage": "opening", "text": 'As the third opening sentence, say word for word: "Calls may be reviewed by our compliance team."'})
    sp["extra_rules"].append({"stage": "opening", "text": 'In the opening, say word for word: "Your personal data is processed under our privacy notice, which you can read on our website or at any branch."'})
    t = opening_text(wf(sp))
    assert t.count(PRIVACY) == 1
    assert t.index(PRIVACY) < t.index("Calls may be reviewed by our compliance team.") < t.index("Am I speaking with")


def test_moved_sentence_is_exempt_from_opening_word_checks_like_a_verbatim_first_sentence():
    sp = copy.deepcopy(SW)
    legal = "Calls are recorded and kept for five years under banking law."
    sp["extra_rules"].insert(1, {"stage": "opening", "text": 'Then say word for word as the third sentence: "%s"' % legal})
    w = wf(sp)
    assert legal in opening_text(w)
    assert [e for e in lint(w, sp) if not e.startswith("warning")] == []


def test_spec_without_such_rules_unchanged():
    for spec in (A, B):
        assert opening_verbatim_rules(spec) == []
        assert json.dumps(wf(spec), sort_keys=True) == json.dumps(wf(copy.deepcopy(spec)), sort_keys=True)


def test_record_exemption_is_opening_only():
    """A moved sentence is exempt from the no-record checks in the opening line only; the same words quoted in
    another step spoken before a promise is recorded are still flagged."""
    from builder.lint import SPOKEN_BEFORE_SUCCESS
    sp = copy.deepcopy(SW)
    legal = "Your recorded call is kept for five years under banking law."
    sp["extra_rules"].insert(1, {"stage": "opening", "text": 'Then say word for word as the third sentence: "%s"' % legal})
    w = wf(sp)
    assert legal in opening_text(w)
    assert [e for e in lint(w, sp) if not e.startswith("warning")] == []
    other = [k for k in SPOKEN_BEFORE_SUCCESS if k != "opening" and isinstance(w["nodes"].get(k, {}).get("task_message"), str)]
    assert other
    k = other[0]
    w["nodes"][k]["task_message"] += ' Say "%s"' % legal
    assert any(("node %s" % k) in e and "record" in e for e in lint(w, sp))
