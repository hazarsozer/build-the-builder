"""Recompute every expected promise date, callback and attempt date in cases.json from the numbers in brief.md, and assert they match.

    python3 bench/<name>/verify.py

The calendar (business weekdays, holidays), the band table (days_late ranges, deadline rule, attempts), the ladder and the callback
window are parsed from brief.md in this folder. SCENARIOS below says, per case, what the customer does (in the brief's terms);
this script derives the record from it independently of the hand-written cases.json.
"""
import datetime as dt, json, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
BRIEF = (HERE / "brief.md").read_text()
CASES = json.loads((HERE / "cases.json").read_text())
D = dt.date.fromisoformat
WD = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# ---- calendar, from the brief -------------------------------------------------------------------------------------
cal_line = next(l for l in BRIEF.splitlines() if l.startswith("Business days are"))
LAST_WD = WD.index(re.search(r"Monday to (\w+)", cal_line)[1].lower())          # 4 = Friday, 5 = Saturday
HOLIDAYS = {D(x) for x in re.findall(r"\d{4}-\d{2}-\d{2}", cal_line)}


def is_bd(d): return d.weekday() <= LAST_WD and d not in HOLIDAYS
def next_bd(d):
    d += dt.timedelta(1)
    while not is_bd(d): d += dt.timedelta(1)
    return d
def nth_bd_after(d, n):
    for _ in range(n): d = next_bd(d)
    return d
def snap_back(d):
    while not is_bd(d): d -= dt.timedelta(1)
    return d
def last_bd_of_week(d):
    sun = d + dt.timedelta(6 - d.weekday())
    return snap_back(sun)
def this_weekday(today, name): return today - dt.timedelta(today.weekday()) + dt.timedelta(WD.index(name))
def next_weekday(today, name): return this_weekday(today, name) + dt.timedelta(7)

# ---- bands, from the brief's table --------------------------------------------------------------------------------
BANDS = []   # (lo, hi, deadline_fn, attempts)
for lo, hi, name, rule, attempts in re.findall(r"^\| (\d+) to (\d+) \| ([^|]+?) \| ([^|]+?) \| (\d+) \|$", BRIEF, re.M):
    if m := re.search(r"the (\d+)(?:st|nd|rd|th) business day after today", rule):
        fn = (lambda n: lambda today: nth_bd_after(today, n))(int(m[1]))
    elif m := re.search(r"(\d+) calendar days after today, snapped back", rule):
        fn = (lambda n: lambda today: snap_back(today + dt.timedelta(n)))(int(m[1]))
    else: raise SystemExit(f"cannot read deadline rule: {rule}")
    BANDS.append((int(lo), int(hi), fn, int(attempts)))
assert BANDS, "no band table found"
# the ladder's rungs, read from rule "Attempt k ...: propose ..."
LADDER = re.findall(r"- Attempt (\d)[^:]*: propose (.+)", BRIEF)
assert [int(k) for k, _ in LADDER] == list(range(1, len(LADDER) + 1)), LADDER

# ---- callback window -------------------------------------------------------------------------------------------------
cb = re.search(r"A callback is for today or the next business day, from (\d\d:\d\d) to (\d\d:\d\d)", BRIEF)
CB_FROM, CB_TO = cb[1], cb[2]


def band(days):
    return next(b for b in BANDS if b[0] <= days <= b[1])
def deadline(today, days): return band(days)[2](today)
def floor(p): return p.get("minimum_payment", p.get("instalment_amount"))


def rung(k, today, p):
    """Attempt k as (amount, date), per the brief's ladder text."""
    text = LADDER[k - 1][1]
    amount = floor(p) if text.startswith("the floor") else p["past_due"]
    if "last business day of this week" in text:
        d = last_bd_of_week(today)
        if d == today: d = last_bd_of_week(today + dt.timedelta(7))
    elif "on the deadline" in text: d = deadline(today, p["days_late"])
    elif "next business day after today" in text: d = next_bd(today)
    else: raise SystemExit(f"cannot read ladder rung: {text}")
    return amount, d


def named_day(today, p, d):
    """Rules 'promise date': a named day after the deadline -> deadline; a non-business day -> next business day."""
    dl = deadline(today, p["days_late"])
    if d > dl: return dl
    if d < today: raise SystemExit("named day in the past")
    return d if is_bd(d) else next_bd(d)


def callback_ok(now, d, t):
    today = now.date()
    return (d == today or d == next_bd(today)) and is_bd(d) and CB_FROM <= t <= CB_TO and dt.datetime.combine(d, dt.time.fromisoformat(t)) > now


def say(d): return f"{d.day} {d:%B}" + (f" {d.year}" if d.year != 2026 else "")


def derive(case, sc):
    """Expected (promises, callbacks, mentioned dates) for one scenario."""
    now = dt.datetime.fromisoformat(case["variables"]["now"]); today = now.date()
    p = case["products"][0]
    kind, arg = sc
    mention = []
    if kind == "none": return [], [], mention
    if kind == "today": return [(p["past_due"], today)], [], mention
    if kind in ("this", "next"):
        d = (this_weekday if kind == "this" else next_weekday)(today, arg)
        return [(p["past_due"], named_day(today, p, d))], [], mention
    if kind == "named": return [(p["past_due"], named_day(today, p, D(arg)))], [], mention
    if kind == "ladder":                       # arg = attempt the customer accepts (None: refuses all of them)
        n = band(p["days_late"])[3]
        rungs = [rung(k, today, p) for k in range(1, n + 1)]
        mention = [d for _, d in rungs]
        if arg is None: return [], [], mention
        assert arg <= n, f"accepts attempt {arg} but the band has {n}"
        return [rungs[arg - 1]], [], mention[:arg]           # attempts after the accepted one are never said
    if kind == "callback":                     # arg = the times offered, in order; first one inside the window is booked
        tries = []
        for when, t in arg:
            d = today + dt.timedelta(1) if when == "tomorrow" else this_weekday(today, when) if when in WD else D(when)
            if d < today: d += dt.timedelta(7)
            tries.append((d, t))
        ok = [x for x in tries[:2] if callback_ok(now, *x)]
        for bad in tries[:tries.index(ok[0])] if ok else tries: mention.append(bad[0])
        return [], [(ok[0][0], ok[0][1])] if ok else [], mention
    raise SystemExit(f"unknown scenario {kind}")


def main():
    by_id = {c["id"]: c for c in CASES}
    assert set(by_id) == set(SCENARIOS), (sorted(set(by_id) ^ set(SCENARIOS)))
    assert len({c["variables"]["now"][:10] for c in CASES}) >= 3, "fewer than 3 different now days"
    bad = 0
    for cid, sc in SCENARIOS.items():
        c = by_id[cid]; e = c["expected"]
        assert c["seed"]["variables"] == c["variables"] and c["seed"]["identity"] == c["identity"] and c["seed"]["products"] == c["products"], cid
        assert c["seed"]["reasons"].get(e["outcome"]) is not None, f"{cid}: outcome missing from the seed's reasons table"
        if e["reason"] is not None: assert e["reason"] in c["seed"]["reasons"][e["outcome"]], f"{cid}: reason {e['reason']} not in table"
        promises, callbacks, mention = derive(c, sc)
        want_p = [{"product_id": "p1", "amount": a, "date": d.isoformat()} for a, d in promises]
        want_c = [{"date": d.isoformat(), "time": t} for d, t in callbacks]
        checks = " | ".join(c["checks"])
        missing = [say(d) for d in mention if say(d) not in checks]
        ok = e["promises"] == want_p and e["callbacks"] == want_c and not missing
        bad += not ok
        dl = deadline(dt.date.fromisoformat(c["variables"]["now"][:10]), c["products"][0]["days_late"])
        print(("OK  " if ok else "FAIL"), cid, c["variables"]["now"], f"deadline {dl}", "promises", [(x["amount"], x["date"]) for x in want_p],
              "callbacks", [(x["date"], x["time"]) for x in want_c], "attempt/mentioned dates", [str(d) for d in mention])
        if not ok: print("     expected in cases.json:", e["promises"], e["callbacks"], "dates missing from checks:", missing)
    print(f"\n{len(SCENARIOS) - bad} / {len(SCENARIOS)} cases match the brief")
    sys.exit(1 if bad else 0)


# What the customer does in each case, in the brief's terms. The record is derived from this, not copied.
SCENARIOS = {
    "IDS-01": ("today", None),                                   # pays the full amount today
    "IDS-02": ("this", "friday"),                                # "this Friday"
    "IDS-03": ("none", None),                                    # identity fails
    "IDS-04": ("ladder", None),                                  # refuses every attempt
    "IDS-05": ("none", None),
    "IDS-06": ("none", None),
    "IDS-07": ("named", "2026-11-30"),                           # asks for 30 November, accepts the deadline
    "IDS-08": ("callback", [("tomorrow", "11:00"), ("thursday", "11:00")]),
}

if __name__ == "__main__":
    main()
