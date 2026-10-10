"""Recompute every expected value in cases.json from the brief's rules and assert it.

    python3 bench/fields/verify.py
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


# ---------------------------------------------------------------- fields: Brief A + create_promise.channel
REASONS = {"promise": [], "paid": [], "transferred": ["restructuring", "human", "support"],
           "no_promise": ["refused", "hardship", "dispute", "service_error"], "not_verified": ["failed", "declined"],
           "not_reached": ["callback", "no_callback", "wrong_number", "deceased", "unclear"]}
D = dt.date


def promise(pid, amount, date, channel): return [(pid, round(amount, 2), iso(date), channel)]


DERIVE = {
    # pays in full today, by card
    "FLD-01": lambda k: {"outcome": "promise", "reason": None, "promises": promise("p1", k.full("p1"), k.today, "card")},
    # names Saturday 24 Oct -> next business day; Monday 26 Oct is a holiday -> Tuesday 27 Oct; app
    "FLD-02": lambda k: {"outcome": "promise", "reason": None, "promises": promise("p1", k.full("p1"), k.recordable("p1", D(2026, 10, 24)), "app")},
    "FLD-03": lambda k: {"outcome": "not_verified", "reason": "failed", "promises": []},
    # flat refusal -> attempt 1 (today is Friday, so next week's last business day); cash twice -> bank_transfer
    "FLD-04": lambda k: {"outcome": "promise", "reason": None, "promises": promise("p1", k.full("p1"), k.attempt1(), "bank_transfer")},
    # 'next Monday' -> following calendar week; card changed to bank transfer at the read-back
    "FLD-05": lambda k: {"outcome": "promise", "reason": None, "promises": promise("p1", k.full("p1"), k.recordable("p1", k.next_weekday("monday")), "bank_transfer")},
    # floor, tomorrow, app
    "FLD-06": lambda k: {"outcome": "promise", "reason": None, "promises": promise("p1", k.floor("p1"), k.recordable("p1", k.today + dt.timedelta(1)), "app")},
    # full tomorrow by card; create_promise fails once, retried
    "FLD-07": lambda k: {"outcome": "promise", "reason": None, "promises": promise("p1", k.full("p1"), k.recordable("p1", k.today + dt.timedelta(1)), "card")},
    "FLD-08": lambda k: {"outcome": "paid", "reason": None, "promises": []},
}


def extra():
    cp = OPENAPI["paths"]["/create_promise"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert cp["properties"]["channel"]["enum"] == ["bank_transfer", "card", "app"] and "channel" in cp["required"]
    vr = OPENAPI["paths"]["/verify_identity"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    assert "match" in vr and "attempts_left" in vr and "verified" not in vr and "wrong_answers_left" not in vr
    for c in CASES:
        for p in c["expected"]["promises"]:
            assert p["channel"] in cp["properties"]["channel"]["enum"], c["id"]
            assert f"channel '{p['channel']}'" in " ".join(c["checks"]), (c["id"], "channel check missing")
    k = K(next(c for c in CASES if c["id"] == "FLD-04"))
    assert k.attempt1() == D(2026, 10, 23) and k.deadline("p1") == D(2026, 10, 28)
    print("openapi mutation and channel checks ok")


if __name__ == "__main__":
    print("now days:", common_checks("fields", REASONS))
    run(DERIVE, extra)
