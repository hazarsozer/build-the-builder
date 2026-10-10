"""Recompute every date in cases.json from the brief's numbers, then replay each case's expected record on our bank.

    python bench/sme/verify.py
"""
import datetime as dt, json, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from _replay import replay

D = dt.date.fromisoformat
HOLIDAYS = {D(x) for x in ("2026-10-24", "2026-10-31", "2026-11-11", "2026-12-25", "2026-12-26", "2027-01-01")}

def biz(d): return d.weekday() < 6 and d not in HOLIDAYS          # Monday to Saturday
def back(d):
    while not biz(d): d -= dt.timedelta(1)
    return d
def next_biz(d):
    d += dt.timedelta(1)
    while not biz(d): d += dt.timedelta(1)
    return d
def deadline(today): return back(today + dt.timedelta(10))
def attempt1(today):
    x = back(today + dt.timedelta(5 - today.weekday()))           # Saturday of this week, snapped back
    return x if x != today else back(today + dt.timedelta(12 - today.weekday()))
def named(d, today):
    dl = deadline(today)
    if d > dl: return dl
    return min(d if biz(d) else next_biz(d), dl)
def window(d, t): return ("09:00" <= t <= "13:00") if d.weekday() == 5 else ("09:00" <= t <= "18:00")

cases = {c["id"]: c for c in json.loads((HERE / "cases.json").read_text())}
def T(cid): return D(cases[cid]["variables"]["now"][:10])
def got(cid): return [p["date"] for p in cases[cid]["expected"]["promises"]]

assert T("SM-01").weekday() == 5 and deadline(T("SM-01")) == D("2026-10-27")
assert got("SM-01") == [str(named(D("2026-10-20"), T("SM-01")))]
assert deadline(T("SM-02")) == D("2026-10-23")                      # +10 = Sat 24 Oct, a holiday
assert got("SM-02") == [str(named(D("2026-10-17"), T("SM-02")))] == ["2026-10-17"]
assert T("SM-03").weekday() == 4 and deadline(T("SM-03")) == D("2026-10-26")
assert got("SM-03") == [str(named(D("2026-10-25"), T("SM-03")))] == ["2026-10-26"]
assert deadline(T("SM-04")) == D("2026-10-30")                      # +10 = Sat 31 Oct, a holiday
assert got("SM-04") == [str(named(D("2026-11-02"), T("SM-04")))] == ["2026-10-30"]
a1, a2 = attempt1(T("SM-05")), deadline(T("SM-05"))                  # +10 = Sun 1 Nov -> Sat 31 Oct (holiday) -> Fri 30 Oct
assert (a1, a2) == (D("2026-10-23"), D("2026-10-30"))
assert "23 October" in cases["SM-05"]["checks"][0] and "30 October" in cases["SM-05"]["checks"][1]
assert cases["SM-05"]["products"][0]["days_late"] >= 21             # escalated band: two attempts
cb = cases["SM-07"]["expected"]["callbacks"][0]
assert T("SM-07").weekday() == 4 and cb["date"] == str(next_biz(T("SM-07"))) == "2026-10-26" and window(D(cb["date"]), cb["time"])
assert not biz(D("2026-10-24"))                                     # the Saturday the manager offers is a holiday
nows = {c["variables"]["now"][:10] for c in cases.values()}
assert len(nows) >= 3 and any(D(n).weekday() == 4 for n in nows)
for c in cases.values(): assert c["variables"]["company_name"]

replay(HERE)
print("sme: dates and replays OK for", len(cases), "cases")
