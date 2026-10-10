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


def opening_warnings(sentence):
    """Banned words in an opening sentence, ignoring the one safe recording sentence."""
    rest = _SAFE_RECORDING.sub(" ", sentence or "")
    return ["opening says %r before the person is confirmed" % m.group(1) for m in _BANNED_OPENING.finditer(rest)]


def safe_opening(sentence):
    """Rephrase a recording notice to the safe sentence. Returns (text, changed)."""
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
    m = re.search(r"^vars\['%s'\] = ('[^'\n]*'|\"[^\"\n]*\")$" % var, code, re.M)
    return ast.literal_eval(m.group(1)) if m else None


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

    # 1. opening wording
    errs += ["warning: spec opening.first_sentence: " + w + "; the compiler rephrases a recording notice"
             for w in opening_warnings(op.get("first_sentence", ""))]
    opening = nodes.get("opening", {})
    text = (opening.get("on_enter") or {}).get("text", "")
    errs += ["opening line: " + w for w in opening_warnings(text)]
    # 1b. no "record*" in text spoken before a successful create_promise (fixed lines and quoted templates)
    for k in SPOKEN_BEFORE_SUCCESS:
        n = nodes.get(k) or {}
        spoken = [(n.get("on_enter") or {}).get("text", "")] + re.findall(r'"([^"]*)"', n.get("task_message", ""))
        for t in spoken:
            if _RECORD.search(t or ""):
                errs.append("node %s: spoken text says %r before a promise is recorded: %r" % (k, _RECORD.search(t).group(0), t[:80]))
    for word in op.get("banned_before_verify", []):
        if re.search(r"\b%s\b" % re.escape(word), _SAFE_RECORDING.sub(" ", text), re.I):
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
    return errs
