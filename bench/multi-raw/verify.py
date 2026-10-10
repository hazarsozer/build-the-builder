"""Recompute every expected value in cases.json from the brief's rules and assert it.

    python3 bench/multi-raw/verify.py
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


# ---------------------------------------------------------------- multi-raw: Brief B rules as policy + memo + code list
REASONS = {"promise": [], "paid": [], "transferred": ["restructuring", "human", "support"],
           "no_promise": ["refused", "hardship", "dispute", "service_error"], "not_verified": ["failed", "declined"],
           "not_reached": ["callback", "no_callback", "wrong_number", "deceased", "unclear"]}
NOTE_TO_CLOSE = {"declined": "refused", "disputed": "dispute", "hardship": "hardship", "system_error": "service_error"}
D = dt.date


def close(promises, notes, transfer=None):
    """Memo version of RC-4 rule 43."""
    if transfer: return {"outcome": "transferred", "reason": transfer, "notes": [], "promises": promises}
    if promises: return {"outcome": "promise", "reason": None, "notes": notes, "promises": promises}
    if all(n["reason"] == "payment_claimed" for n in notes): return {"outcome": "paid", "reason": None, "notes": notes, "promises": []}
    first = next(n for n in notes if n["reason"] != "payment_claimed")   # notes are in get_account order
    return {"outcome": "no_promise", "reason": NOTE_TO_CLOSE[first["reason"]], "notes": notes, "promises": []}


def shared(k, pid, amount, named):
    """RC-4 rule 20: one date for all; a product whose deadline is earlier is recorded on its own deadline."""
    return (pid, round(amount, 2), iso(min(k.next_bd_from(named), k.deadline(pid))))


def n(pid, r): return {"product_id": pid, "reason": r}


DERIVE = {
    "MRW-01": lambda k: close([shared(k, p, k.full(p), D(2026, 11, 20)) for p in ("p1", "p2", "p3")], []),
    "MRW-02": lambda k: close([("p3", k.full("p3"), iso(k.recordable("p3", k.next_weekday("tuesday"))))],
                              [n("p1", "payment_claimed"), n("p2", "disputed")]),
    # card promise recorded, then a restructuring request: the recorded promise stays, no notes at all
    "MRW-03": lambda k: close([("p1", k.full("p1"), iso(k.recordable("p1", k.next_weekday("friday"))))], [], transfer="restructuring"),
    "MRW-04": lambda k: close([], [n("p1", "declined"), n("p2", "declined")]),
    "MRW-05": lambda k: close([], [n("p1", "payment_claimed"), n("p2", "payment_claimed")]),
    "MRW-06": lambda k: close([], [n("p1", "payment_claimed"), n("p2", "disputed"), n("p3", "declined")]),
    "MRW-07": lambda k: close([shared(k, "p1", k.floor("p1"), D(2026, 11, 30)), shared(k, "p2", k.full("p2"), D(2026, 11, 30)),
                               shared(k, "p3", k.full("p3"), D(2026, 11, 30))], []),
    # agreed but not recorded when the transfer happens -> nothing recorded
    "MRW-08": lambda k: close([], [], transfer="restructuring"),
}


def extra():
    enum = OPENAPI["paths"]["/add_note"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["reason"]["enum"]
    assert enum == ["declined", "hardship", "disputed", "payment_claimed", "system_error"]
    by = {c["id"]: c for c in CASES}
    for c in CASES:
        assert 2 <= len(c["products"]) <= 3, c["id"]
        bands = {"early" if p["days_late"] <= 7 else "late" for p in c["products"]}
        assert bands == {"early", "late"}, (c["id"], "bands not mixed")
        for x in c["expected"]["notes"]: assert x["reason"] in enum, c["id"]
        if c["expected"]["transfers"]: assert c["expected"]["transfers"] == [{"queue": c["expected"]["reason"]}]
    k = K(by["MRW-01"]); assert k.deadline("p2") == D(2026, 11, 17) < D(2026, 11, 20) <= k.deadline("p1")
    k = K(by["MRW-04"]); assert k.attempt1() == D(2026, 12, 18) and k.deadline("p1") == D(2026, 12, 29) and k.floor("p1") == 234.0
    k = K(by["MRW-06"]); assert k.attempt1() == D(2026, 10, 23) and k.deadline("p3") == D(2026, 10, 28)
    k = K(by["MRW-03"]); assert k.attempt1() == D(2026, 11, 27) and k.deadline("p2") == D(2026, 12, 2)
    print("note codes, bands and ladder dates ok")


if __name__ == "__main__":
    print("now days:", common_checks("multi-raw", REASONS))
    run(DERIVE, extra)
