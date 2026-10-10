"""Recompute every date in cases.json from the brief's numbers, then replay each case's expected record on our bank.

    python bench/insurance/verify.py
"""
import datetime as dt, json, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from _replay import replay

D = dt.date.fromisoformat
HOLIDAYS = {D(x) for x in ("2026-10-19", "2026-11-09", "2026-11-27", "2026-12-24", "2026-12-25", "2026-12-31", "2027-01-01")}

def biz(d): return d.weekday() < 5 and d not in HOLIDAYS
def nth_biz_after(d, n):
    while n:
        d += dt.timedelta(1)
        n -= biz(d)
    return d
def next_biz(d):
    d += dt.timedelta(1)
    while not biz(d): d += dt.timedelta(1)
    return d
def last_biz_of_week(d):
    end = d + dt.timedelta(4 - d.weekday())
    while not biz(end): end -= dt.timedelta(1)
    return end
def band(days): return "grace" if days <= 15 else "lapse" if days <= 60 else "lapsed"
def deadline(today, days): return nth_biz_after(today, {"grace": 5, "lapse": 10}[band(days)])
def ladder(today, days):
    if band(days) == "grace": return [deadline(today, days)]
    a1 = last_biz_of_week(today)
    if a1 == today: a1 = last_biz_of_week(today + dt.timedelta(7 - today.weekday()))
    return [a1, nth_biz_after(today, 5)]
def clamp(named, today, days):
    """A named date for one policy: the next business day if it is not one, the deadline if it is past it."""
    dl = deadline(today, days)
    if named > dl: return dl
    x = named if biz(named) else next_biz(named)
    return min(x, dl)

cases = {c["id"]: c for c in json.loads((HERE / "cases.json").read_text())}
def T(cid): return D(cases[cid]["variables"]["now"][:10])
def P(cid, i=0): return cases[cid]["products"][i]["days_overdue"]
def got(cid): return [(p["product_id"], p["date"]) for p in cases[cid]["expected"]["promises"]]

# IN-01: "tomorrow" (Thu -> Fri), grace band
assert got("IN-01") == [("p1", str(clamp(T("IN-01") + dt.timedelta(1), T("IN-01"), P("IN-01"))))]
assert deadline(T("IN-01"), P("IN-01")) == D("2026-10-23")
# IN-02: Friday call, "Monday" = 19 Oct, a holiday -> next business day
assert T("IN-02").weekday() == 4 and not biz(D("2026-10-19"))
assert got("IN-02") == [("p1", str(clamp(D("2026-10-19"), T("IN-02"), P("IN-02"))))] == [("p1", "2026-10-20")]
# IN-03: lapse-risk ladder; attempt 2 accepted
a1, a2 = ladder(T("IN-03"), P("IN-03"))
assert (a1, a2) == (D("2026-10-23"), D("2026-10-28")) and a2 <= deadline(T("IN-03"), P("IN-03"))
assert got("IN-03") == [("p1", str(a2))]
assert "23 October" in cases["IN-03"]["checks"][0] and "28 October" in cases["IN-03"]["checks"][1]
# IN-05: Friday call, next business day is Monday 26 Oct; 11:00 inside 09:00-18:00, 18:30 outside
cb = cases["IN-05"]["expected"]["callbacks"][0]
assert T("IN-05").weekday() == 4 and cb["date"] == str(next_biz(T("IN-05"))) and "09:00" <= cb["time"] <= "18:00" and not ("18:30" <= "18:00")
# IN-06: lapsed
assert band(P("IN-06")) == "lapsed" and cases["IN-06"]["expected"]["promises"] == []
# IN-07: "this Friday" from Tue 3 Nov
assert got("IN-07") == [("p1", str(clamp(D("2026-11-06"), T("IN-07"), P("IN-07"))))]
# IN-08: one named date (Fri 20 Nov) for both; the grace policy is clamped to its own deadline
named = D("2026-11-20")
assert T("IN-08").weekday() == 4
assert deadline(T("IN-08"), P("IN-08", 0)) == D("2026-11-16") and deadline(T("IN-08"), P("IN-08", 1)) == D("2026-11-23")
assert got("IN-08") == [("p1", str(clamp(named, T("IN-08"), P("IN-08", 0)))), ("p2", str(clamp(named, T("IN-08"), P("IN-08", 1))))]
nows = {c["variables"]["now"][:10] for c in cases.values()}
assert len(nows) >= 3 and any(D(n).weekday() == 4 for n in nows)

replay(HERE)
print("insurance: dates and replays OK for", len(cases), "cases")
