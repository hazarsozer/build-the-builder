"""Brief oracle: scenarios written from the raw brief by a session that never sees the spec.

oracle(brief_paths, openapi, trace) -> list[scenario]      (one Agent SDK session)
check(spec, scenarios) -> list[mismatch]                    (deterministic, builder.lib)

A scenario:
    {id, now, products, customer_says, inputs, expected, quote}
    inputs   (optional keys): refusals (int), too_much (bool), customer_date (YYYY-MM-DD),
             callback_at (YYYY-MM-DDTHH:MM)
    expected: outcome, reason, promises [{product_id, amount, date}], notes [{product_id, reason}],
              callbacks [{date, time}], transfers [queue], and optionally
              deadlines {product_id: date}, offers [{product_id, attempt, amount, date}]

check() recomputes only the policy-math fields with the spec and builder.lib; the dialogue
itself is not simulated. Each mismatch names the spec paths that produced the computed value,
so the settle session can fix the spec or reject the scenario, citing a quote.
"""
import datetime
import json

from builder.lib import dates

SCENARIO_KEYS = ("id", "now", "products", "customer_says", "expected", "quote")


# ------------------------------------------------------------------ spec helpers

def _band(spec, days_late):
    for i, b in enumerate(spec["bands"]):
        if b["days_late"][0] <= days_late <= b["days_late"][1]:
            return i, b
    return None, None


def _event(spec, eid):
    for e in spec["events"]:
        if e["id"] == eid:
            return e
    return None


def _pairs(spec):
    return {(r["outcome"], r["reason"]) for r in spec["outcomes"]}


def _queues(spec):
    return {e["queue"] for e in spec["events"] if e.get("queue")}


def _floor(spec, product):
    p = spec["products"].get(product.get("type"))
    if p is None:
        return None, None
    return p["floor_field"], product.get(p["floor_field"])


def _order(spec, products):
    order = spec["multi_product"]["order"]
    ps = list(products)
    if order == "highest_past_due":
        ps.sort(key=lambda p: -float(p.get("past_due") or 0))
    elif order == "most_days_late":
        ps.sort(key=lambda p: -int(p.get("days_late") or 0))
    return [p["product_id"] for p in ps]


def close_from_notes(spec, order, notes):
    """(outcome, reason) for a multi-product call that ends with notes and no promise.

    TODO-swap: replace with builder.lib close_reason (Task 5) once it lands; this local
    helper applies the spec's multi_product.no_promise_reason rule the same way.
    Returns None when the rule cannot be applied (no notes, or a `fixed` reason).
    """
    mp = spec["multi_product"]
    reasons = [notes[pid] for pid in order if pid in notes]
    if not reasons:
        return None
    if all(r == "paid" for r in reasons) and mp.get("all_paid_outcome"):
        return (mp["all_paid_outcome"], None)
    refused = _event(spec, "refused")
    outcome = refused["outcome"] if refused else None
    if outcome is None:
        return None
    rule = mp["no_promise_reason"]
    if rule == "first_non_paid_note":
        return (outcome, next(r for r in reasons if r != "paid"))
    if rule == "last_note":
        return (outcome, reasons[-1])
    return None


def _in_callback_window(spec, today, now_t, at):
    cb = spec["third_party"]["callback"]
    cal = spec["calendar"]
    d, t = at.date(), at.strftime("%H:%M")
    day_ok = False
    for k in cb["days"]:
        if k == "today" and d == today:
            day_ok = True
        elif k == "next_business_day" and d == dates.next_bd(today, cal):
            day_ok = True
        elif k == "next_day" and d == today + datetime.timedelta(days=1):
            day_ok = True
        elif k == "any_business_day" and dates.is_bd(d, cal) and d >= today:
            day_ok = True
    if not day_ok:
        return False
    if cb.get("inclusive", True):
        in_time = cb["from"] <= t <= cb["to"]
    else:
        in_time = cb["from"] < t < cb["to"]
    if not in_time:
        return False
    if cb.get("after_now") and at <= now_t:
        return False
    return True


# ------------------------------------------------------------------ check

def _mm(s, field, expected, computed, paths, note=""):
    return {"scenario": s.get("id"), "field": field, "expected": expected, "computed": computed,
            "spec_paths": paths, "quote": s.get("quote"), "customer_says": s.get("customer_says"),
            "note": note}


def _money_eq(a, b):
    try:
        return abs(float(a) - float(b)) < 0.005
    except (TypeError, ValueError):
        return False


def _check_one(spec, s):
    out = []
    cal = spec["calendar"]
    now_t = datetime.datetime.fromisoformat(s["now"])
    today = now_t.date()
    exp = s.get("expected") or {}
    inp = s.get("inputs") or {}
    prods = {p["product_id"]: p for p in s.get("products") or []}

    # outcome pair and transfer queues
    pair = (exp.get("outcome"), exp.get("reason"))
    if exp.get("outcome") is not None and pair not in _pairs(spec):
        out.append(_mm(s, "outcome", list(pair), sorted([list(p) for p in _pairs(spec)], key=str),
                       ["outcomes"], "pair not in the spec's outcome table"))
    for q in exp.get("transfers") or []:
        if q not in _queues(spec):
            out.append(_mm(s, "transfer queue", q, sorted(_queues(spec)), ["events"]))

    # per-product deadline
    deadlines = {}
    bands = {}
    for pid, p in prods.items():
        i, b = _band(spec, int(p["days_late"]))
        if b is None:
            out.append(_mm(s, "band", p["days_late"], None, ["bands"], "no band covers days_late"))
            continue
        bands[pid] = (i, b)
        deadlines[pid] = dates.deadline(today, b["deadline"], cal)
    for pid, want in (exp.get("deadlines") or {}).items():
        if pid in deadlines and deadlines[pid].isoformat() != want:
            out.append(_mm(s, "deadline", want, deadlines[pid].isoformat(),
                           ["bands[%d].deadline" % bands[pid][0]]))

    # persuasion offers
    offers = exp.get("offers") or []
    by_prod = {}
    for o in offers:
        by_prod.setdefault(o.get("product_id"), []).append(o)
    for pid, os_ in by_prod.items():
        if pid not in bands:
            continue
        i, b = bands[pid]
        p = prods[pid]
        if len(os_) > len(b["attempts"]):
            out.append(_mm(s, "attempt count", len(os_), len(b["attempts"]), ["bands[%d].attempts" % i],
                           "more offers than attempts"))
        for o in os_:
            k = int(o.get("attempt") or 0)
            if k < 1 or k > len(b["attempts"]):
                continue
            att = b["attempts"][k - 1]
            ff, floor = _floor(spec, p)
            if att["amount"] == "floor" or att["amount"] == "current" and inp.get("too_much"):
                amount = floor
            else:
                amount = p.get("past_due")
            if "amount" in o and not _money_eq(o["amount"], amount):
                out.append(_mm(s, "offer amount", o["amount"], amount,
                               ["bands[%d].attempts[%d].amount" % (i, k - 1),
                                "products.%s.floor_field" % p.get("type")]))
            d = dates.attempt_date(today, att["date"], deadlines[pid], cal).isoformat()
            if o.get("date") and o["date"] != d:
                out.append(_mm(s, "offer date", o["date"], d, ["bands[%d].attempts[%d].date" % (i, k - 1)]))
    refused = _event(spec, "refused")
    if (refused and pair == (refused["outcome"], refused["reason"]) and not exp.get("promises")
            and not exp.get("transfers") and len(prods) == 1 and offers
            and int(inp.get("refusals") or 0) > 0):
        pid = next(iter(prods))
        if pid in bands:
            i, b = bands[pid]
            n = len(by_prod.get(pid, []))
            if int(inp["refusals"]) > len(b["attempts"]) and n < len(b["attempts"]):
                out.append(_mm(s, "attempt count", n, len(b["attempts"]), ["bands[%d].attempts" % i],
                               "fewer offers than attempts before the refusal close"))

    # promises: amount bounds and the customer-named date
    neg = spec["negotiation"]
    for pr in exp.get("promises") or []:
        p = prods.get(pr.get("product_id"))
        if p is None:
            continue
        ff, floor = _floor(spec, p)
        amt = pr.get("amount")
        if amt is not None and floor is not None and float(amt) < float(floor) - 0.005:
            out.append(_mm(s, "promise amount", amt, floor, ["products.%s.floor_field" % p.get("type")],
                           "below the floor"))
        cd = inp.get("customer_date")
        if cd and pr.get("date") and p["product_id"] in deadlines:
            d = datetime.date.fromisoformat(cd)
            dl = deadlines[p["product_id"]]
            want, path = None, None
            if d > dl:
                if neg["after_deadline"] == "offer_deadline":
                    want, path = dl, "negotiation.after_deadline"
            elif not dates.is_bd(d, cal):
                if neg["non_business_day"] == "next_business_day":
                    want, path = min(dates.next_bd(d, cal), dl), "negotiation.non_business_day"
                elif neg["non_business_day"] == "previous_business_day":
                    want, path = dates.prev_bd(d, cal), "negotiation.non_business_day"
            elif d >= today:
                want, path = d, "calendar"
            if want is not None and pr["date"] != want.isoformat():
                out.append(_mm(s, "promise date", pr["date"], want.isoformat(),
                               [path, "calendar", "bands[%d].deadline" % bands[p["product_id"]][0]]))

    # callbacks
    if inp.get("callback_at"):
        at = datetime.datetime.fromisoformat(inp["callback_at"])
        inside = _in_callback_window(spec, today, now_t, at)
        if inside != bool(exp.get("callbacks")):
            out.append(_mm(s, "callback window", bool(exp.get("callbacks")), inside,
                           ["third_party.callback"]))

    # notes and the multi-product close
    notes = {n.get("product_id"): n.get("reason") for n in exp.get("notes") or []}
    mp = spec["multi_product"]
    if notes:
        if not mp["notes"]["enabled"]:
            out.append(_mm(s, "notes", sorted(notes.values()), [], ["multi_product.notes.enabled"],
                           "the spec writes no notes"))
        else:
            bad = sorted(set(notes.values()) - set(mp["notes"]["reasons"]))
            if bad:
                out.append(_mm(s, "notes", bad, mp["notes"]["reasons"], ["multi_product.notes.reasons"]))
            if not exp.get("promises") and not exp.get("transfers") and exp.get("outcome") is not None:
                got = close_from_notes(spec, _order(spec, s.get("products") or []), notes)
                if got is not None and tuple(got) != pair:
                    out.append(_mm(s, "close reason", list(pair), list(got),
                                   ["multi_product.no_promise_reason", "multi_product.all_paid_outcome"]))
    return out


def check(spec, scenarios):
    out = []
    for s in scenarios:
        try:
            out += _check_one(spec, s)
        except Exception as exc:  # a malformed scenario is reported, never fatal
            out.append(_mm(s if isinstance(s, dict) else {}, "scenario", None, None, [],
                           "could not check: %s: %s" % (type(exc).__name__, exc)))
    return out


def scenario_errors(scenarios):
    """Shape problems in an oracle's scenarios.json, fed back to the oracle session."""
    if not isinstance(scenarios, list) or not scenarios:
        return ["scenarios.json must be a non-empty JSON list"]
    errs = []
    for i, s in enumerate(scenarios):
        if not isinstance(s, dict):
            errs.append("scenario %d is not an object" % i)
            continue
        miss = [k for k in SCENARIO_KEYS if k not in s]
        if miss:
            errs.append("scenario %s: missing %s" % (s.get("id", i), ", ".join(miss)))
        try:
            datetime.datetime.fromisoformat(s.get("now", ""))
        except (TypeError, ValueError):
            errs.append("scenario %s: now %r is not YYYY-MM-DDTHH:MM" % (s.get("id", i), s.get("now")))
    return errs


# ------------------------------------------------------------------ oracle session

async def oracle_async(brief_paths, openapi, trace, *, workdir=None, client_factory=None, timeout_s=420):
    """Run the oracle session; returns (scenarios, info). Never sees a spec."""
    from builder import sdk

    work = workdir or sdk.make_workdir(brief_paths, openapi, "oracle", with_schema=False)
    prompt = sdk.load_prompt("oracle.md", briefs=sdk.brief_names(work))
    info = {"workdir": str(work)}
    async with sdk.Session(work, "oracle", trace, client_factory=client_factory) as sess:
        await sess.ask(prompt, timeout_s=timeout_s)
        scen, err = sdk.read_json(work / "scenarios.json")
        errs = [err] if err else scenario_errors(scen)
        if errs:
            await sess.ask("scenarios.json has these problems; fix the file:\n- " + "\n- ".join(errs),
                           timeout_s=120)
            scen, err = sdk.read_json(work / "scenarios.json")
            errs = [err] if err else scenario_errors(scen)
        info.update(sess.stats())
    if errs:
        trace.event("oracle_invalid", errors=errs[:20])
        return [], dict(info, errors=errs)
    trace.event("oracle_done", scenarios=len(scen))
    return scen, info


def oracle(brief_paths, openapi, trace, **kw):
    import asyncio
    return asyncio.run(oracle_async(brief_paths, openapi, trace, **kw))[0]


if __name__ == "__main__":  # pragma: no cover
    import sys
    spec = json.load(open(sys.argv[1]))
    scen = json.load(open(sys.argv[2]))
    print(json.dumps(check(spec, scen), indent=1))
