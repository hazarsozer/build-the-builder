"""Field-level diff and merge of two policy specs (dual extraction).

diff_specs(a, b) -> list of spec paths where the two extractions disagree on policy.
Paths use the validator's syntax: dotted keys, [index], or [id] for list items with an id.

Narrative fields (sources, facts, conflicts, examples, custom_code, extra_rules, event
trigger/instruction) are not compared: two readings always word them differently and
the compiler does not branch on them.
"""
import re

from builder.validate import resolve_path


class _Missing:
    def __repr__(self):
        return "MISSING"

    def __bool__(self):
        return False


MISSING = _Missing()

IGNORE_TOP = {"sources", "facts", "conflicts", "examples", "custom_code", "extra_rules"}
IGNORE_EVENT_KEYS = {"trigger", "instruction"}
# Lists whose disagreement is merged by union: extra items only make the agent stricter.
UNION_LISTS = {"opening.confirm_accept", "opening.confirm_reject",
               "opening.banned_before_verify", "negotiation.banned_offers"}
# Scalar lists whose order matters; every other scalar list is compared as a set.
ORDERED_SCALAR_LISTS = {"bands[*].days_late"}
# Value assumed when an optional key is absent (schema "Default when absent").
DEFAULTS = {
    "events[*].times": 1,
    "third_party.callback.inclusive": True,
    "multi_product.notes.timing": "immediate",
    "say_rules[*].stages": ["any"],
    "opening.verbatim": False,
}

_BRACKET = re.compile(r"\[[^\[\]]*\]")
_WS = re.compile(r"\s+")


def _pattern(path):
    return _BRACKET.sub("[*]", path)


def _norm_str(s):
    s = _WS.sub(" ", s).strip().strip("\"'`“”‘’").strip()
    s = s.rstrip(".!?").strip()
    return s.casefold()


def _norm(v):
    if isinstance(v, str):
        return _norm_str(v)
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v


def _default(path, v):
    if v is MISSING or v is None:
        d = DEFAULTS.get(_pattern(path), None)
        return d if v is MISSING or d is not None else v
    return v


def _scalar_list(v):
    return isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v)


def _key(x):
    return repr(x)


def _set_of(lst):
    return sorted({_key(_norm(x)) for x in lst})


def diff_entries(a, b):
    """[(path, value_in_a, value_in_b)] for every policy disagreement, in a stable order."""
    out = []
    _walk(a, b, "", out)
    return out


def diff_specs(a, b):
    return [p for p, _, _ in diff_entries(a, b)]


def _walk(a, b, path, out):
    a = _default(path, a)
    b = _default(path, b)
    pat = _pattern(path)
    if isinstance(a, dict) and isinstance(b, dict):
        for k in list(a) + [k for k in b if k not in a]:
            if path == "" and k in IGNORE_TOP:
                continue
            if pat == "events[*]" and k in IGNORE_EVENT_KEYS:
                continue
            p = "%s.%s" % (path, k) if path else k
            _walk(a.get(k, MISSING), b.get(k, MISSING), p, out)
        return
    if path == "events" and isinstance(a, list) and isinstance(b, list):
        ida = {e.get("id"): e for e in a if isinstance(e, dict)}
        idb = {e.get("id"): e for e in b if isinstance(e, dict)}
        for i in list(ida) + [i for i in idb if i not in ida]:
            p = "events[%s]" % i
            if i not in ida or i not in idb:
                out.append((p, ida.get(i, MISSING), idb.get(i, MISSING)))
            else:
                _walk(ida[i], idb[i], p, out)
        return
    if path == "outcomes" and isinstance(a, list) and isinstance(b, list):
        if _set_of(a) != _set_of(b):
            out.append((path, a, b))
        return
    if path == "say_rules" and isinstance(a, list) and isinstance(b, list):
        sa = sorted({_norm_str(r.get("say", "")) for r in a if isinstance(r, dict)})
        sb = sorted({_norm_str(r.get("say", "")) for r in b if isinstance(r, dict)})
        if sa != sb:
            out.append((path, a, b))
        return
    if isinstance(a, list) and isinstance(b, list):
        if _scalar_list(a) and _scalar_list(b):
            if pat in ORDERED_SCALAR_LISTS:
                same = _norm(a) == _norm(b)
            else:
                same = _set_of(a) == _set_of(b)
            if not same:
                out.append((path, a, b))
            return
        if len(a) != len(b):
            out.append((path, a, b))
            return
        for i, (x, y) in enumerate(zip(a, b)):
            _walk(x, y, "%s[%d]" % (path, i), out)
        return
    if _norm(a) != _norm(b) or type(a) is not type(b) and not (
            isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool)):
        out.append((path, a, b))


# ------------------------------------------------------------------ merge

_TOKEN = re.compile(r"([^.\[\]]+)|\[([^\[\]]+)\]")


def _tokens(path):
    toks = []
    for m in _TOKEN.finditer(path):
        if m.group(1) is not None:
            toks.append(("key", m.group(1)))
        else:
            v = m.group(2)
            toks.append(("idx", int(v)) if v.isdigit() else ("id", v))
    return toks


def set_path(spec, path, value):
    """Set (or, with MISSING, remove) the value at path. [id] on a list appends when absent."""
    toks = _tokens(path)
    cur = spec
    for kind, k in toks[:-1]:
        if kind == "key":
            if k not in cur or not isinstance(cur[k], (dict, list)):
                cur[k] = {}
            cur = cur[k]
        elif kind == "idx":
            cur = cur[k]
        else:
            hit = [x for x in cur if isinstance(x, dict) and x.get("id") == k]
            if not hit:
                raise KeyError("no list item with id %r in %s" % (k, path))
            cur = hit[0]
    kind, k = toks[-1]
    if kind == "key":
        if value is MISSING:
            cur.pop(k, None)
        else:
            cur[k] = value
    elif kind == "idx":
        if value is MISSING:
            del cur[k]
        else:
            cur[k] = value
    else:
        pos = [i for i, x in enumerate(cur) if isinstance(x, dict) and x.get("id") == k]
        if value is MISSING:
            for i in reversed(pos):
                del cur[i]
        elif pos:
            cur[pos[0]] = value
        else:
            cur.append(value)


def _in_brief(s, brief_norm):
    return _norm_str(s) in brief_norm


def normalise_brief(text):
    return _norm_str(text.replace("`", "").replace("“", '"').replace("”", '"'))


def auto_resolve(path, va, vb, brief_text):
    """Resolve a disagreement without a model call when the rule is mechanical.

    Returns (value, decision) or None when the settle session must decide.
    - union lists: the union (an extra banned word or accepted phrase is harmless);
    - say_rules: A's rules plus B's rules with a sentence A lacks;
    - two sentences: the one found word for word in the brief; if neither is, the first
      (a paraphrase; wording outside required sentences is not policy).
    """
    pat = _pattern(path)
    if pat in UNION_LISTS and isinstance(va, list) and isinstance(vb, list):
        seen, out = set(), []
        for x in va + vb:
            n = _norm(x)
            if _key(n) not in seen:
                seen.add(_key(n))
                out.append(x)
        return out, "union"
    if path == "say_rules" and isinstance(va, list) and isinstance(vb, list):
        have = {_norm_str(r.get("say", "")) for r in va}
        return va + [r for r in vb if _norm_str(r.get("say", "")) not in have], "union by sentence"
    if isinstance(va, str) and isinstance(vb, str) and " " in va.strip() and " " in vb.strip():
        bn = normalise_brief(brief_text)
        ia, ib = _in_brief(va, bn), _in_brief(vb, bn)
        if ia and not ib:
            return va, "first is verbatim in the brief"
        if ib and not ia:
            return vb, "second is verbatim in the brief"
        if not ia and not ib:
            return va, "paraphrase on both sides; kept the first"
    return None


def path_exists(spec, path):
    return resolve_path(spec, path)[0]
