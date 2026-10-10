"""Recompute every expected value in cases.json from the brief's rules and assert it.

    python3 bench/plan2/verify.py
"""
import datetime as dt, json, pathlib, sys
from decimal import Decimal, ROUND_CEILING

HERE = pathlib.Path(__file__).resolve().parent
CASES = json.loads((HERE / "cases.json").read_text())
OPENAPI = json.loads((HERE / "openapi.json").read_text())
HOLIDAYS = {dt.date(2026, 10, 26), dt.date(2026, 11, 11), dt.date(2026, 11, 26), dt.date(2026, 12, 25), dt.date(2027, 1, 1)}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def is_bd(d): return d.weekday() < 5 and d not in HOLIDAYS
def iso(d): return d.isoformat()


class K:
    """Calendar and amount rules of the brief, evaluated for one case."""
    def __init__(self, case):
        self.case = case
        self.today = dt.date.fromisoformat(case["variables"]["now"][:10])
        self.p = {f"p{i}": p for i, p in enumerate(case["products"], 1)}

    def nth_bd(self, n):
        d = self.today
        while n:
            d += dt.timedelta(1)
            if is_bd(d): n -= 1
        return d

    def band(self, pid): return "early" if self.p[pid]["days_late"] <= 7 else "late"
    def deadline(self, pid): return self.nth_bd(12 if self.band(pid) == "early" else 7)
    def floor(self, pid):
        p = self.p[pid]; return p["minimum_payment"] if p["type"] == "credit_card" else p["instalment_amount"]
    def full(self, pid): return self.p[pid]["past_due"]

    def last_bd_of_week(self, d):
        f = d - dt.timedelta(d.weekday()) + dt.timedelta(4)
        while not is_bd(f): f -= dt.timedelta(1)
        return f

    def attempt1(self):
        a = self.last_bd_of_week(self.today)
        return self.last_bd_of_week(self.today + dt.timedelta(7)) if a == self.today else a

    def next_bd_from(self, d):
        """Rule 20: a named weekend day or holiday becomes the next business day."""
        while not is_bd(d): d += dt.timedelta(1)
        return d

    def next_weekday(self, name):
        """'next <weekday>' = that weekday in the following calendar week."""
        monday = self.today - dt.timedelta(self.today.weekday()) + dt.timedelta(7)
        return monday + dt.timedelta(DAYS.index(name))

    def this_weekday(self, name):
        return self.today - dt.timedelta(self.today.weekday()) + dt.timedelta(DAYS.index(name))

    def recordable(self, pid, d):
        """Sections 7/A rule 19-23: a customer-named date -> the date that gets recorded."""
        if d > self.deadline(pid): return self.deadline(pid)          # rule 23: offer the deadline (accepted in the script)
        d = self.next_bd_from(d)                                     # rule 20
        assert d <= self.deadline(pid)
        return d

    def date(self, s): return dt.date.fromisoformat(s)


def ceil_cent(x): return float((Decimal(str(x))).quantize(Decimal("0.01"), rounding=ROUND_CEILING))


def common_checks(brief_name, reasons_table):
    ids = [c["id"] for c in CASES]
    assert len(CASES) == 8 and len(set(ids)) == 8, ids
    nows = {c["variables"]["now"][:10] for c in CASES}
    assert len(nows) >= 3, nows
    outcomes = OPENAPI["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["outcome"]["enum"]
    for c in CASES:
        e, s = c["expected"], c["seed"]
        assert c["brief"] == brief_name, c["id"]
        assert s["variables"] == c["variables"] and s["identity"] == c["identity"] and s["products"] == c["products"], c["id"]
        assert s["reasons"] == reasons_table, c["id"]
        assert e["outcome"] in outcomes, (c["id"], e["outcome"])
        allowed = reasons_table[e["outcome"]]
        assert (e["reason"] is None) if not allowed else (e["reason"] in allowed), (c["id"], e["reason"])
        n = len(c["products"])
        for p in e["promises"]:
            assert p["product_id"] in {f"p{i}" for i in range(1, n + 1)}, c["id"]
            d = dt.date.fromisoformat(p["date"])
            assert is_bd(d), (c["id"], p)
        fails = {f["tool"]: list(range(1, f["fail_first_n"] + 1)) for f in c.get("faults", [])}
        assert s["faults"] == fails, c["id"]
        if e["outcome"] == "promise": assert e["promises"], c["id"]
    return sorted(nows)


def run(derive, extra=None):
    bad = 0
    for c in CASES:
        k = K(c)
        want = derive[c["id"]](k)
        got = c["expected"]
        for key in want:
            if key == "promises":
                g = [(p["product_id"], round(p["amount"], 2), p["date"]) + ((p["channel"],) if "channel" in p else ()) for p in got["promises"]]
                w = [tuple(x) for x in want["promises"]]
                ok = g == w
            else:
                ok = got[key] == want[key]
            if not ok:
                bad += 1; print(f"MISMATCH {c['id']} {key}: cases.json {got[key]!r}, recomputed {want[key]!r}")
        print(f"ok  {c['id']}  now {c['variables']['now']}  {got['outcome']}/{got['reason']}  "
              + ", ".join(f"{p['product_id']} {p['amount']} {p['date']}" for p in got["promises"]))
    if extra: extra()
    if bad: sys.exit(f"{bad} mismatches")
    print("all expected values recomputed and equal")


# ---------------------------------------------------------------- plan2: Brief A + two-part split (rule 36)
REASONS = {"promise": [], "paid": [], "transferred": ["restructuring", "human", "support"],
           "no_promise": ["refused", "hardship", "dispute", "service_error"], "not_verified": ["failed", "declined"],
           "not_reached": ["callback", "no_callback", "wrong_number", "deceased", "unclear"]}
D = dt.date


def latest_part2(k):
    """No later than 30 calendar days after today; a non-business 30th day snaps back."""
    d = k.today + dt.timedelta(30)
    while not is_bd(d): d -= dt.timedelta(1)
    return d


def part1_min(k, pid): return ceil_cent(k.full(pid) * 0.4)


def split(k, pid, a1, d1_named, d2_named):
    """Customer-named split. a1 None = agent proposal (40% on the deadline); a part-1 amount below 40% is
    raised to the minimum (the customer accepts it in every script); a part-1 date after the deadline becomes
    the deadline; a part-2 date after the latest becomes the latest."""
    full = k.full(pid)
    a1 = part1_min(k, pid) if a1 is None else max(a1, part1_min(k, pid))
    d1 = k.deadline(pid) if d1_named is None else k.recordable(pid, d1_named)
    last = latest_part2(k)
    d2 = last if d2_named is None or d2_named > last else k.next_bd_from(d2_named)
    assert d2 <= last and d2 > d1 and is_bd(d2) and a1 < full
    a2 = round(full - a1, 2)
    return [(pid, round(a1, 2), iso(d1)), (pid, a2, iso(d2))]


P = lambda pr: {"outcome": "promise", "reason": None, "promises": pr}
DERIVE = {
    "PL2-01": lambda k: P(split(k, "p1", 500.0, k.next_weekday("friday"), D(2026, 11, 6))),
    "PL2-02": lambda k: P(split(k, "p1", 300.0, k.today, D(2026, 12, 4))),
    "PL2-03": lambda k: P([("p1", k.full("p1"), iso(k.recordable("p1", k.next_weekday("tuesday"))))]),
    "PL2-04": lambda k: P(split(k, "p1", 400.0, D(2026, 12, 24), D(2027, 1, 29))),
    "PL2-05": lambda k: P(split(k, "p1", 300.0, D(2026, 10, 30), D(2026, 11, 13))),
    "PL2-06": lambda k: {"outcome": "transferred", "reason": "restructuring", "promises": [], "transfers": [{"queue": "restructuring"}]},
    "PL2-07": lambda k: P(split(k, "p1", None, None, None)),
    "PL2-08": lambda k: P(split(k, "p1", 350.0, D(2026, 12, 23), D(2027, 1, 8))),
}


def extra():
    by = {c["id"]: K(c) for c in CASES}
    assert part1_min(by["PL2-02"], "p1") == 493.83                       # 0.4 * 1234.56 = 493.824 -> up to the cent
    assert latest_part2(by["PL2-02"]) == D(2026, 12, 4)                   # 6 Dec is a Sunday
    assert latest_part2(by["PL2-04"]) == D(2027, 1, 15)                   # 17 Jan is a Sunday
    assert latest_part2(by["PL2-05"]) == D(2026, 11, 13)                  # exactly day 30, a Friday
    assert by["PL2-05"].deadline("p1") == D(2026, 10, 23)
    assert latest_part2(by["PL2-07"]) == D(2026, 12, 18) and by["PL2-07"].deadline("p1") == D(2026, 12, 9)
    split_cases = [c for c in CASES if len(c["expected"]["promises"]) == 2]
    for c in split_cases:
        ps = c["expected"]["promises"]
        assert ps[0]["product_id"] == ps[1]["product_id"]
        assert round(ps[0]["amount"] + ps[1]["amount"], 2) == c["products"][0]["past_due"], c["id"]
    print(f"split arithmetic ok ({len(split_cases)} split cases)")


if __name__ == "__main__":
    print("now days:", common_checks("plan2", REASONS))
    run(DERIVE, extra)
