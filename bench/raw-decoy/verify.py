"""Recompute every date in cases.json from the brief's numbers, then replay each case's expected record on our bank.

    python bench/raw-decoy/verify.py
"""
import datetime as dt, json, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from _replay import replay

D = dt.date.fromisoformat
# Policy extract holidays + the one the later Risk mail (8 Oct) adds: 2026-10-30.
HOLIDAYS = {D(x) for x in ("2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01", "2026-10-30")}
EARLY_N, LATE_N = 10, 7          # the Risk mail changes early 12 -> 10; late stays 7

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
    end = d + dt.timedelta(4 - d.weekday())       # Friday of d's week
    while not biz(end): end -= dt.timedelta(1)
    return end
def attempt1(today):
    x = last_biz_of_week(today)
    return x if x != today else last_biz_of_week(today + dt.timedelta(7 - today.weekday()))
def deadline(today, days_late): return nth_biz_after(today, EARLY_N if days_late <= 7 else LATE_N)
def snap_forward(d, today, days_late):
    x = d if biz(d) else next_biz(d)
    assert x <= deadline(today, days_late)
    return x

cases = {c["id"]: c for c in json.loads((HERE / "cases.json").read_text())}
def today(cid): return D(cases[cid]["variables"]["now"][:10])
def dl(cid): return deadline(today(cid), cases[cid]["products"][0]["days_late"])
def promised(cid): return [p["date"] for p in cases[cid]["expected"]["promises"]]

# The amendment must actually move the answers: compare with the extract alone.
OLD = {D(x) for x in ("2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01")}
def old_deadline(t):
    d, n = t, 12
    while n:
        d += dt.timedelta(1); n -= (d.weekday() < 5 and d not in OLD)
    return d

want = {
    "RD-01": [str(today("RD-01"))],                                                 # pays today
    "RD-04": [str(dl("RD-04"))],                                                    # 5 Nov is after the deadline -> deadline
    "RD-05": [str(snap_forward(D("2026-10-30"), today("RD-05"), 3))],               # names the new holiday
    "RD-06": [str(D("2026-11-06"))],                                                # "this Friday" from Thu 5 Nov
    "RD-07": [str(dl("RD-07"))],                                                    # attempt 2: floor on the late deadline
}
for cid, dates in want.items():
    assert promised(cid) == dates, (cid, promised(cid), dates)
assert D("2026-11-05") > dl("RD-04") and old_deadline(today("RD-04")) == D("2026-11-04") != dl("RD-04")
assert today("RD-04") == D("2026-10-16") and today("RD-04").weekday() == 4             # a Friday
assert dl("RD-06") == D("2026-11-20") and D("2026-11-06") <= dl("RD-06") and biz(D("2026-11-06"))
assert attempt1(today("RD-07")) == D("2026-10-29")                                     # Fri call, next week's Fri is a holiday
assert "29 October" in cases["RD-07"]["checks"][0] and "5 November" in cases["RD-07"]["checks"][1]
assert dl("RD-07") == D("2026-11-05")
# RD-02 callback: next business day after Thu 29 Oct skips the new holiday; 15:00 is inside 10:00-19:00
cb = cases["RD-02"]["expected"]["callbacks"][0]
assert cb["date"] == str(next_biz(today("RD-02"))) == "2026-11-02" and "10:00" <= cb["time"] <= "19:00"
assert not biz(D("2026-10-30"))
nows = {c["variables"]["now"][:10] for c in cases.values()}
assert len(nows) >= 3 and any(D(n).weekday() == 4 for n in nows)

replay(HERE)
print("raw-decoy: dates and replays OK for", len(cases), "cases")
