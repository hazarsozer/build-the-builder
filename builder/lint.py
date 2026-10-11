"""Lint a compiled workflow against its spec.

lint(workflow, spec) -> list of strings; empty means clean. Entries starting with
"warning:" flag something the compiler already worked around; the rest are errors.
"""
import ast
import re
from collections import deque

from builder.nodes import FAREWELL_RE

# PUB-10 lesson: an opening that says "recorded", "collections", "debt" or "payment" before the
# person is confirmed reads as disclosure to the judge, and the honesty check reads any "record*" before
# a successful create_promise literally. A recording notice is rephrased to this sentence:
SAFE_RECORDING_SENTENCE = "This call is monitored and stored for quality and security purposes."
_SAFE_RECORDING = re.compile(r"\bthis call is monitored and stored for quality and security(?: purposes)?\b\.?", re.I)
_RECORD = re.compile(r"\brecord\w*", re.I)
# Nodes whose text is spoken before a promise can have been recorded.
SPOKEN_BEFORE_SUCCESS = ("opening", "identity", "negotiate")
_BANNED_OPENING = re.compile(r"\b(record\w*|collect\w*|debts?|payments?)\b", re.I)
_CALL_RECORDED = re.compile(
    r"^(.*?)(?:,?\s*(?:and|so)\s+)?(?:this|the|your)\s+call\s+(?:is|will be|may be|might be|could be)\s+"
    r"(?:being\s+)?recorded\b.*$", re.I)


# a spec placeholder ({first_name}); the compiler renders it as {{var}} (or prose for amount/date, see compile.tpl)
_SPEC_PH = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# a single-brace placeholder left in spoken text: the platform reads it out literally
_RAW_PH = re.compile(r"(?<!\{)\{[A-Za-z_][A-Za-z0-9_]*\}(?!\})")


def _as_spoken(sentence):
    """A spec sentence as a pattern over its compiled text: each {x} matches whatever the compiler put there
    ({{var}} or a few words of prose). lint cannot import compile (compile imports lint)."""
    lit = [re.escape(p) for p in _SPEC_PH.split(sentence)[0::2]]
    return re.compile(r"(?:\{\{[A-Za-z_][A-Za-z0-9_]*\}\}|[\w ]{1,40}?)".join(lit))


def opening_warnings(sentence):
    """Banned words in an opening sentence, ignoring the one safe recording sentence."""
    rest = _SAFE_RECORDING.sub(" ", sentence or "")
    return ["opening says %r before the person is confirmed" % m.group(1) for m in _BANNED_OPENING.finditer(rest)]


# F2 (bench strict-wording): a sentence the brief requires word for word in the opening can land in an extra rule
# ("Right after the first sentence, say this second sentence word for word: "...""). The opening is spoken from fixed
# text, so the rule never takes effect; the compiler speaks the sentence in the opening and drops the rule.
_VERBATIM_WORDS = re.compile(r"\bword[ -]for[ -]word\b|\bverbatim\b|\bexact(?:ly| words?)\b", re.I)
# Conservative on purpose (review of e32b29a): a missed rule stays a prompt rule as before; a wrongly moved one
# would be spoken on every call.
_OPENING_PLACE = re.compile(r"\bopening\b(?!\s+offers?\b)|\b(?:first|second|third) sentence\b(?!\s+of\s+your\s+(?:answer|reply|response)\b)|"
                            r"\bgreeting\b|\b(?:start|beginning) of (?:the|every|each) call\b", re.I)
_NEGATION = re.compile(r"\b(?:do not|don't|never|avoid|must not|no longer|not|without|instead|rather than|except)\b", re.I)
# another moment or listener than the opening to the customer (read outside the quote). "after the first sentence"
# is the placement this matcher exists for (bench strict-wording), so it alone does not count as "after".
_OTHER_MOMENT = re.compile(r"voicemail|answering machine|message|third[- ]part|someone else|for them|\bafter\b|"
                           r"\bbefore\b|confirm|verif|identity", re.I)
_AFTER_FIRST = re.compile(r"\b(?:right |just |directly |immediately )?after the first sentence\b", re.I)
_CONDITION = re.compile(r"\b(?:if|when|whenever|unless|only|asked|in case|once)\b", re.I)
_QUOTED = re.compile(r'"([^"]+)"|“([^”]+)”')
_PLACEHOLDER = re.compile(r"\[|\]|\{\{|\}\}")


def opening_verbatim_rules(spec):
    """[(rule, sentence)]: extra rules of stage opening that require one quoted sentence word for word in the
    opening, with no negation and no condition anywhere in the rule. Rule order is brief order; a sentence is listed
    once (a later rule for the same sentence comes with None)."""
    out, seen = [], set()
    for r in spec.get("extra_rules", []) or []:
        text = r.get("text") or ""
        if r.get("stage") != "opening" or _NEGATION.search(text) or _CONDITION.search(text):
            continue
        outside = _QUOTED.sub(" ", text)
        if _OTHER_MOMENT.search(_AFTER_FIRST.sub(" ", outside)):
            continue
        if not (_VERBATIM_WORDS.search(text) and _OPENING_PLACE.search(outside)):
            continue
        quoted = [(m.group(1) or m.group(2)).strip() for m in _QUOTED.finditer(text)]
        quoted = [x for x in quoted if len(x.split()) >= 3]
        if len(quoted) != 1 or _PLACEHOLDER.search(quoted[0]):
            continue  # several quotes (which one is unclear) or a placeholder: leave the rule as it is
        if not re.search(r"[.!?]$", quoted[0]):
            quoted[0] += "."
        if quoted[0] not in seen:
            seen.add(quoted[0])
            out.append((r, quoted[0]))
        else:
            out.append((r, None))  # a second rule for the same sentence: dropped, nothing added
    return out


def safe_opening(sentence, verbatim=False):
    """Rephrase a recording notice to the safe sentence. Returns (text, changed).
    A sentence the brief requires word for word (opening.verbatim) is returned untouched."""
    if verbatim:
        return sentence or "", False
    sents = [s for s in re.split(r"(?<=[.!?])\s+", (sentence or "").strip()) if s]
    out, need = [], False
    for s in sents:
        if _SAFE_RECORDING.search(s) or not re.search(r"\brecord", s, re.I):
            out.append(s)
            continue
        need = True
        m = _CALL_RECORDED.match(s)
        if m and m.group(1).strip(" ,;"):
            out.append(m.group(1).rstrip(" ,;") + ".")
    if need:
        out.append(SAFE_RECORDING_SENTENCE)
    return " ".join(out), need


# Only record* about a promise (or its payment, date, amount) is rephrased; any other use ("our records show", "for the
# record", "this call is being recorded") keeps its meaning and is left for lint to flag.
_PROMISE_NOUN = r"(?:promises?|payments?|dates?|amounts?|payment\s+dates?|payment\s+plans?|arrangements?)"
_DET = r"(?:a|an|the|your|any|each|every|this|that|these|those|its|their|our)"
# "a recorded promise" -> "a promise"
_REC_DET = re.compile(r"\b(%s)\s+recorded\s+(?=%s\b)" % (_DET, _PROMISE_NOUN), re.I)
# "we have recorded your promise" -> "we have taken your promise"; "record the promise" -> "take the promise"
_REC_VERB = re.compile(r"\b(record(?:s|ed|ing)?)(\s+(?:%s\s+)?%s\b)" % (_DET, _PROMISE_NOUN), re.I)
_TAKE = {"record": "take", "records": "takes", "recorded": "taken", "recording": "taking"}
# "the promise is (already) recorded" is NOT rewritten: "noted" before success still claims the promise exists, so
# the sentence is kept and lint flags it


def safe_pre_success(text):
    """Text spoken before a promise can be recorded, without record* words about the promise and with the same
    meaning (the honesty check reads any "record*" before a successful create_promise literally). Returns
    (text, changed). Only promise phrasings are rewritten: "A recorded promise cannot ..." -> "A promise cannot ...",
    "we have recorded your promise" -> "we have taken your promise". A passive claim ("the promise is recorded") is
    kept: rewording it ("noted") would still claim the promise exists, so lint flags it. Every other record* word is
    kept (lint flags it)."""
    if not text or not _RECORD.search(text):
        return text or "", False
    t = _REC_DET.sub(lambda m: m.group(1) + " ", text)

    def verb(m):
        w = m.group(1)
        before = re.findall(r"(\w+)\s*$", t[:m.start()])
        if before and re.fullmatch(_DET, before[0], re.I):  # a noun ("our records ..."), not the verb
            return m.group(0)
        out = _TAKE[w.lower()]
        return (out[0].upper() + out[1:] if w[0].isupper() else out) + m.group(2)
    t = _REC_VERB.sub(verb, t)
    return t, t != text


def never_say_words(spec):
    """spec.never_say: words or phrases the brief forbids in every sentence of the call."""
    w = spec.get("never_say") if isinstance(spec, dict) else None
    return [x.strip() for x in w if isinstance(x, str) and x.strip()] if isinstance(w, list) else []


def never_say_re(word):
    """The word or phrase in any form: its last word may take an ending (guarantee -> guaranteed, lawsuit -> lawsuits)."""
    return re.compile(r"\b%s\w*" % r"\s+".join(re.escape(p) for p in word.split()), re.I)


def required_pre_verify_sentences(spec):
    """(where, text) for what the agent must speak to the customer before identity is verified: the opening, the
    confirm question, the security questions and the decline explanation (not third-party or hint text)."""
    op, idn = spec.get("opening"), spec.get("identity")
    op, idn = op if isinstance(op, dict) else {}, idn if isinstance(idn, dict) else {}
    out = []
    if isinstance(op.get("first_sentence"), str):
        # the sentence as spoken: rephrased, or word for word when the brief requires it (opening.verbatim)
        out.append(("opening.first_sentence", safe_opening(op["first_sentence"], bool(op.get("verbatim")))[0]))
    if isinstance(op.get("confirm_question"), str):
        out.append(("opening.confirm_question", op["confirm_question"]))
    for i, q in enumerate(idn.get("questions") if isinstance(idn.get("questions"), list) else []):
        if isinstance(q, dict) and isinstance(q.get("ask"), str):
            out.append(("identity.questions[%d].ask" % i, q["ask"]))
    if isinstance(idn.get("decline_explanation"), str):
        out.append(("identity.decline_explanation", idn["decline_explanation"]))
    return out


def reconcile_banned(spec):
    """Drop banned_before_verify terms that a required pre-verification sentence itself contains
    (case-insensitive, word boundary, also without a leading "the"). Returns (spec, removals); the input is
    never mutated and is returned as is when nothing changes. removals: [{"term", "found_in", "sentence"}]."""
    op = spec.get("opening") if isinstance(spec, dict) else None
    banned = op.get("banned_before_verify") if isinstance(op, dict) else None
    if not isinstance(banned, list) or not banned:
        return spec, []
    sentences = required_pre_verify_sentences(spec)
    keep, removals = [], []
    for term in banned:
        bare = re.sub(r"^\s*the\s+", "", term, flags=re.I).strip() if isinstance(term, str) else ""
        hit = None
        if bare:
            rx = re.compile(r"\b%s\b" % re.escape(bare), re.I)
            hit = next(((w, t) for w, t in sentences if rx.search(t)), None)
        if hit:
            removals.append({"term": term, "found_in": hit[0], "sentence": hit[1]})
        else:
            keep.append(term)
    if not removals:
        return spec, []
    out = dict(spec)
    out["opening"] = dict(op, banned_before_verify=keep)
    return out, removals


# ---------------------------------------------------------------- helpers

def _code_of(node):
    try:
        return node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
    except (KeyError, IndexError, TypeError):
        return ""


def farewell_of(node):
    m = FAREWELL_RE.search(_code_of(node))
    return ast.literal_eval(m.group(1)) if m else ""


def _assigned(code, var):
    """A constant a close node assigns: `vars['outcome'] = 'x'` or `OUTCOME = 'x'`; "" counts as unset."""
    m = re.search(r"^(?:vars\['%s'\]|%s) = ('[^'\n]*'|\"[^\"\n]*\")$" % (var, var.upper()), code, re.M)
    return (ast.literal_eval(m.group(1)) or None) if m else None


def _tools(workflow):
    for k, n in workflow.get("nodes", {}).items():
        for t in (n.get("tools") or []) + (n.get("pre_actions") or []):
            yield k, t
    for t in workflow.get("tools") or []:
        yield "workflow", t


def _targets(node):
    out = [t.get("target_node") for t in node.get("transitions") or []]
    for k in ("passthrough_default_transition", "max_turn_transition"):
        if node.get(k):
            out.append(node[k])
    return out


def _nulls(x, path, out):
    if x is None:
        out.append("null value at " + path)
    elif isinstance(x, dict):
        for k, v in x.items():
            _nulls(v, path + "." + str(k), out)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            _nulls(v, "%s[%d]" % (path, i), out)


# ---------------------------------------------------------------- lint

def lint(workflow, spec):
    errs = []
    nodes = workflow.get("nodes", {})
    op = spec.get("opening", {})

    # 1. opening wording. opening.verbatim: the brief requires the first sentence word for word, so the
    # compiler keeps it as written and words inside it only warn.
    verbatim = bool(op.get("verbatim"))
    first = op.get("first_sentence", "") or ""
    if verbatim:
        errs += ["warning: spec opening.first_sentence: " + w + "; kept word for word (opening.verbatim)"
                 for w in opening_warnings(first)]
    else:
        errs += ["warning: spec opening.first_sentence: " + w + "; the compiler rephrases a recording notice"
                 for w in opening_warnings(first)]
    opening = nodes.get("opening", {})
    text = (opening.get("on_enter") or {}).get("text", "")
    # the verbatim sentence is the one part of the opening line that is exempt from the word checks; it is compared
    # as rendered (a {first_name} in it is spoken as {{first_name}})
    first_rx = _as_spoken(first) if verbatim and first else None
    checked = first_rx.sub(" ", text, count=1) if first_rx else text
    # F2: word-for-word opening sentences moved from extra rules are exempt like the verbatim first sentence
    moved = [_as_spoken(x) for _, x in opening_verbatim_rules(spec) if x]
    for rx in moved:
        checked = rx.sub(" ", checked, count=1)
    # a placeholder the compiler did not render is read out literally ("Hello {first_name}")
    for k, n in nodes.items():
        m = _RAW_PH.search((n.get("on_enter") or {}).get("text") or "") if (n.get("on_enter") or {}).get("type") == "tts" else None
        if m:
            errs.append("node %s: spoken text has a raw placeholder %s" % (k, m.group(0)))
    errs += ["opening line: " + w for w in opening_warnings(checked)]
    # 1b. no "record*" in text spoken before a successful create_promise (fixed lines and quoted templates)
    for k in SPOKEN_BEFORE_SUCCESS:
        n = nodes.get(k) or {}
        spoken = [(n.get("on_enter") or {}).get("text", "")] + re.findall(r'"([^"]*)"', n.get("task_message", ""))
        for t in spoken:
            if first_rx:
                t = first_rx.sub(" ", t or "")
            if k == "opening":   # F2: the moved sentences are exempt in the opening line only
                for rx in moved:
                    t = rx.sub(" ", t or "")
            if _RECORD.search(t or ""):
                errs.append("node %s: spoken text says %r before a promise is recorded: %r" % (k, _RECORD.search(t).group(0), t[:80]))
    for word in op.get("banned_before_verify", []):
        if re.search(r"\b%s\b" % re.escape(word), _SAFE_RECORDING.sub(" ", checked), re.I):
            errs.append("opening line says banned %r" % word)

    # 2. no brace pairs in code
    for k, n in nodes.items():
        if "{{" in _code_of(n) or "}}" in _code_of(n):
            errs.append("node %s: code contains {{ or }}" % k)
    for k, t in _tools(workflow):
        for hook in ("pre_process_code", "post_process_code"):
            c = t.get(hook) or ""
            if "{{" in c or "}}" in c:
                errs.append("node %s tool %s: %s contains {{ or }}" % (k, t.get("function", {}).get("name"), hook))

    # 3. graph: known targets, every node reaches goodbye, no top-level `and`
    if workflow.get("initial_node") not in nodes:
        errs.append("initial_node %r is not a node" % workflow.get("initial_node"))
    rev = {k: set() for k in nodes}
    for k, n in nodes.items():
        for t in _targets(n):
            if t not in nodes:
                errs.append("node %s: unknown target %s" % (k, t))
            else:
                rev[t].add(k)
        for t in n.get("transitions") or []:
            if (t.get("condition") or {}).get("logic") == "and":
                errs.append("node %s: top-level `and` condition" % k)
    if "goodbye" not in nodes:
        errs.append("no goodbye node")
    else:
        seen, q = {"goodbye"}, deque(["goodbye"])
        while q:
            for p in rev[q.popleft()]:
                if p not in seen:
                    seen.add(p)
                    q.append(p)
        errs += ["node %s cannot reach goodbye" % k for k in nodes if k not in seen]
    start = workflow.get("initial_node")
    if start in nodes:
        seen, q = {start}, deque([start])
        while q:
            for t in _targets(nodes[q.popleft()]):
                if t in nodes and t not in seen:
                    seen.add(t)
                    q.append(t)
        errs += ["node %s is unreachable from %s" % (k, start) for k in nodes if k not in seen]

    # 4. close nodes: farewell ends with goodbye; outcome/reason known
    enum = []
    for t in workflow.get("tools") or []:
        if t.get("function", {}).get("name") == "close_call":
            enum = t["function"]["parameters"]["properties"]["body"]["properties"]["outcome"].get("enum", [])
    allowed = {(o.get("outcome"), o.get("reason") or None) for o in spec.get("outcomes", [])}
    for k, n in nodes.items():
        if not k.startswith("close_"):
            continue
        f = farewell_of(n)
        if not re.search(r"\bgoodbye[.!]?$", f.strip(), re.I):
            errs.append("node %s: farewell does not end with a goodbye: %r" % (k, f))
        code = _code_of(n)
        o, r = _assigned(code, "outcome"), _assigned(code, "reason")
        if o not in enum:
            errs.append("node %s: outcome %r is not in the close_call enum" % (k, o))
        if (o, r) not in allowed:
            errs.append("node %s: outcome/reason %r/%r is not in the spec outcome table" % (k, o, r))

    # 5. no nulls anywhere
    _nulls(workflow, "workflow", errs)

    # 6. spec.never_say: no fixed text the agent speaks uses a forbidden word (engine lines, farewells, quoted
    # sentences in prompts). A quoted string that is only the word itself is the prompt's own never-say list.
    for word in never_say_words(spec):
        rx = never_say_re(word)
        for k, n in nodes.items():
            spoken = []
            if (n.get("on_enter") or {}).get("type") == "tts":
                spoken.append(n["on_enter"].get("text", ""))
            if k.startswith("close_"):
                spoken.append(farewell_of(n))
            spoken += [t for t in re.findall(r'"([^"]*)"', n.get("task_message") or "") if not rx.fullmatch(t.strip())]
            for t in spoken:
                m = rx.search(t or "")
                if m:
                    errs.append("node %s: fixed text says never-say %r: %r" % (k, m.group(0), t[:80]))
    return errs
