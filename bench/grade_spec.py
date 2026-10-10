"""Grade an extracted spec against a hand-checked reference, field by field.

Graded: bands (range, deadline rule, attempts), calendar, identity questions (field, format,
date order, length) and limits, product floor fields, the (outcome, reason) set, the event
ids, and the required verbatim sentences. Sentence wording elsewhere is not graded.

    python -m bench.grade_spec out/spec.json specs/reference/spec-a.json
"""
import json
import re
import sys

VERBATIM = [
    ("events[deceased].say", lambda s: _event(s, "deceased", "say")),
    ("negotiation.first_ask", lambda s: s["negotiation"].get("first_ask")),
    ("confirmation.question", lambda s: s["confirmation"].get("question")),
    ("confirmation.success_sentence", lambda s: s["confirmation"].get("success_sentence")),
]


def _event(s, eid, key):
    for e in s.get("events", []):
        if e.get("id") == eid:
            return e.get(key)
    return None


def _ws(x):
    return re.sub(r"\s+", " ", x).strip() if isinstance(x, str) else x


def _band(b):
    d = dict(b["deadline"])
    d.setdefault("snap", None)
    return {"days_late": b["days_late"], "deadline": d, "attempts": b["attempts"]}


def grade(spec, ref):
    out = []

    def cmp(label, got, want):
        if got != want:
            out.append((label, got, want))

    gb = sorted(spec.get("bands", []), key=lambda b: b["days_late"][0])
    rb = sorted(ref["bands"], key=lambda b: b["days_late"][0])
    if len(gb) != len(rb):
        cmp("bands", len(gb), len(rb))
    else:
        for i, (g, r) in enumerate(zip(gb, rb)):
            g, r = _band(g), _band(r)
            for k in ("days_late", "deadline", "attempts"):
                cmp("bands[%d].%s" % (i, k), g[k], r[k])
    cmp("calendar.weekdays", sorted(spec["calendar"]["weekdays"]), sorted(ref["calendar"]["weekdays"]))
    cmp("calendar.holidays", sorted(spec["calendar"]["holidays"]), sorted(ref["calendar"]["holidays"]))

    keys = ("field", "format", "date_order", "length")
    gq = [{k: q.get(k) for k in keys} for q in spec["identity"]["questions"]]
    rq = [{k: q.get(k) for k in keys} for q in ref["identity"]["questions"]]
    if len(gq) != len(rq):
        cmp("identity.questions", gq, rq)
    else:
        for i, (g, r) in enumerate(zip(gq, rq)):
            cmp("identity.questions[%d]" % i, g, r)
    for k in ("max_wrong", "wrong_scope", "decline_limit"):
        cmp("identity." + k, spec["identity"].get(k), ref["identity"].get(k))

    for t, p in ref["products"].items():
        cmp("products.%s.floor_field" % t, (spec["products"].get(t) or {}).get("floor_field"), p["floor_field"])

    def pairs(s):
        return sorted({(r["outcome"], r["reason"]) for r in s["outcomes"]}, key=str)
    cmp("outcome_set", pairs(spec), pairs(ref))
    cmp("event_ids", sorted(e["id"] for e in spec["events"]), sorted(e["id"] for e in ref["events"]))

    for label, get in VERBATIM:
        want = _ws(get(ref))
        if want is None:
            continue
        try:
            got = _ws(get(spec))
        except (KeyError, TypeError):
            got = None
        cmp(label, got, want)
    return out


if __name__ == "__main__":
    s = json.load(open(sys.argv[1]))
    r = json.load(open(sys.argv[2]))
    diffs = grade(s, r)
    for label, got, want in diffs:
        print("%-32s got  %s\n%-32s want %s" % (label, json.dumps(got), "", json.dumps(want)))
    print("%d graded differences" % len(diffs))
    sys.exit(1 if diffs else 0)
