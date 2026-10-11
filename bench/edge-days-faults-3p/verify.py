"""Recompute every expected record in cases.json from brief.md (Brief A), then replay it on our bank and score it.

    python3 bench/edge-days-faults-3p/verify.py

1. Calendar, bands and callback window are parsed from brief.md. DERIVE says, per case, what the customer does in the
   brief's terms; the record is derived from that independently of the hand-written cases.json.
2. Each expected record is replayed in process on bank.server.Bank (no HTTP) by an agent that retries a failed tool
   call exactly once (rule 37 applied to every tool, our "brief silent" reading). A case whose fault outlasts that
   retry must expect no_promise / service_error; the other fault cases must expect the record the fault delayed.
3. Format checks: the seed mirrors the case, 2 to 4 checks, run_cases.persona_for renders it, and the outcome
   pairs and identity fields agree with specs/reference/spec-a.json (bench/run.py extraction_checks).
"""
import datetime as dt, json, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from bank.server import Bank            # noqa: E402
from score import score_ledger          # noqa: E402
from run_cases import persona_for       # noqa: E402

BRIEF = (HERE / "brief.md").read_text()
CASES = json.loads((HERE / "cases.json").read_text())
OPENAPI = json.loads((HERE / "openapi.json").read_text())
D = dt.date.fromisoformat
WD = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# ---- the brief's numbers ------------------------------------------------------------------------------------------
cal_line = next(l for l in BRIEF.splitlines() if l.startswith("Business days are"))
assert "Monday to Friday" in cal_line
HOLIDAYS = {D(x) for x in re.findall(r"\d{4}-\d{2}-\d{2}", cal_line)}
assert len(HOLIDAYS) == 5
BANDS = [(int(a), int(b), int(n), int(k)) for a, b, n, k in
         re.findall(r"\| (\d+) to (\d+) \| \w+ \| the (\d+)\w\w business day after today \| (\d) \|", BRIEF)]
assert BANDS == [(1, 7, 12, 1), (8, 45, 7, 2)], BANDS
m = re.search(r"from (\d\d:\d\d) to (\d\d:\d\d), both included, and later than `now`", BRIEF)
CB_FROM, CB_TO = m[1], m[2]
REASONS = {"promise": [], "paid": [], "transferred": ["restructuring", "human", "support"],
           "no_promise": ["refused", "hardship", "dispute", "service_error"], "not_verified": ["failed", "declined"],
           "not_reached": ["callback", "no_callback", "wrong_number", "deceased", "unclear"]}
RETRIES = 1                              # rule 37: "try once more"


def is_bd(d): return d.weekday() < 5 and d not in HOLIDAYS


class K:
    def __init__(self, c):
        self.c = c
        self.now = c["variables"]["now"]
        self.today = D(self.now[:10])
        self.p = c["products"][0]

    def nth_bd(self, n):
        d = self.today
        while n:
            d += dt.timedelta(1)
            if is_bd(d): n -= 1
        return d

    def deadline(self):
        n = next(n for a, b, n, _ in BANDS if a <= self.p["days_late"] <= b)
        return self.nth_bd(n)

    def full(self): return self.p["past_due"]

    def next_bd(self):
        d = self.today + dt.timedelta(1)
        while not is_bd(d): d += dt.timedelta(1)
        return d

    def this_weekday(self, name): return self.today + dt.timedelta(WD.index(name) - self.today.weekday())

    def recordable(self, d):
        """Rules 19, 20, 23: a named date -> the recorded date (the deadline when it is after it, accepted in the script)."""
        if d > self.deadline(): return self.deadline()
        while not is_bd(d): d += dt.timedelta(1)
        return d

    def cb_ok(self, d, t):
        """Rule 6: today or the next business day, CB_FROM..CB_TO both included, later than now."""
        return d in (self.today, self.next_bd()) and is_bd(d) and CB_FROM <= t <= CB_TO and (d, t) > (self.today, self.now[11:16])

    def first_ok(self, *offers):
        """Rule 7: the first offer inside the window, if it comes within one more ask; else no callback."""
        for d, t in offers[:2]:
            if self.cb_ok(d, t): return [(d.isoformat(), t)]
        return []


def P(k, d, amount=None): return {"outcome": "promise", "reason": None, "promises": [("p1", amount or k.full(), d.isoformat())]}
def CB(k, *offers):
    got = k.first_ok(*offers)
    return {"outcome": "not_reached", "reason": "callback" if got else "no_callback", "callbacks": got}
def SERVICE_ERROR(k): return {"outcome": "no_promise", "reason": "service_error", "promises": []}
def plus(k, n): return k.today + dt.timedelta(n)


DERIVE = {
    # vague period (rule 21) twice -> the deadline; early band, 12 business days across the 11-11 holiday
    "EDF-01": lambda k: P(k, k.deadline()),
    # salary on 8 January > late-band deadline (7 bd across 12-25 and 01-01) -> rule 23, deadline accepted
    "EDF-02": lambda k: P(k, k.recordable(D("2027-01-08"))),
    # 20:30 tonight is after 19:00 -> ask once -> tomorrow 10:00 (the window's first minute, included)
    "EDF-03": lambda k: CB(k, (k.today, "20:30"), (plus(k, 1), "10:00")),
    # 19:00 today: included and later than 18:30
    "EDF-04": lambda k: CB(k, (k.today, "19:00")),
    # tomorrow is 11-11, a holiday, so not the next business day -> ask once -> Thursday 12:00
    "EDF-05": lambda k: CB(k, (plus(k, 1), "12:00"), (k.this_weekday("thursday"), "12:00")),
    "EDF-06": lambda k: P(k, k.recordable(plus(k, 1))),
    "EDF-07": SERVICE_ERROR,
    "EDF-08": lambda k: P(k, k.today),
    "EDF-09": SERVICE_ERROR,
    "EDF-10": lambda k: CB(k, (plus(k, 1), "12:00")),
    "EDF-11": lambda k: {"outcome": "transferred", "reason": "restructuring", "transfers": ["restructuring"]},
    # Saturday morning, then Monday 19 Oct: neither is today or the next business day (Thu 10-15)
    "EDF-12": lambda k: CB(k, (k.this_weekday("saturday"), "10:00"), (D("2026-10-19"), "12:00")),
    # 'this Friday' said on a Wednesday
    "EDF-13": lambda k: P(k, k.recordable(k.this_weekday("friday"))),
}
# what each case is about: the weekday or hour, or the fault, it depends on
SHAPE = {"EDF-01": lambda k: k.today.weekday() == 4 and k.next_bd().month != k.today.month and HOLIDAYS & {k.today + dt.timedelta(i) for i in range(30)},
         "EDF-02": lambda k: {D("2026-12-25"), D("2027-01-01")} < {k.today + dt.timedelta(i) for i in range(20)},
         "EDF-03": lambda k: k.now[11:16] > CB_TO,
         "EDF-04": lambda k: k.today.weekday() == 4 and k.now[11:16] < CB_TO,
         "EDF-05": lambda k: not is_bd(k.today + dt.timedelta(1)) and (k.today + dt.timedelta(1)).weekday() < 5,
         "EDF-08": lambda k: k.today.weekday() == 4}


def check_derived():
    bad = 0
    for c in CASES:
        k, e = K(c), c["expected"]
        if c["id"] in SHAPE: assert SHAPE[c["id"]](k), (c["id"], "the case's day/hour does not have the shape it tests")
        want = DERIVE[c["id"]](k)
        got = {"outcome": e["outcome"], "reason": e["reason"],
               "promises": [(p["product_id"], p["amount"], p["date"]) for p in e["promises"]],
               "callbacks": [(x["date"], x["time"]) for x in e["callbacks"]], "transfers": [t["queue"] for t in e["transfers"]]}
        for key, w in want.items():
            if got[key] != w: bad += 1; print(f"MISMATCH {c['id']} {key}: cases.json {got[key]!r}, derived {w!r}")
        for key in ("promises", "callbacks", "transfers"):
            if key not in want and got[key]: bad += 1; print(f"MISMATCH {c['id']} {key}: expected none, cases.json {got[key]!r}")
        for _, _, d in got["promises"]:
            assert is_bd(D(d)) and k.today <= D(d) <= k.deadline(), (c["id"], d)
        print(f"ok  {c['id']}  {k.today:%a} {k.now}  {e['outcome']}/{e['reason']}  "
              + ", ".join(f"{p} {a} {d}" for p, a, d in got["promises"]) + ", ".join(f"cb {d} {t}" for d, t in got["callbacks"]))
    if bad: sys.exit(f"{bad} mismatches")


def check_format():
    ids = [c["id"] for c in CASES]
    assert len(set(ids)) == len(ids) == 13, ids
    outcomes = OPENAPI["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["outcome"]["enum"]
    for c in CASES:
        e, s = c["expected"], c["seed"]
        assert c["brief"] == HERE.name, c["id"]
        assert s["variables"] == c["variables"] and s["identity"] == c["identity"] and s["products"] == c["products"], c["id"]
        assert s["reasons"] == REASONS and e["outcome"] in outcomes, c["id"]
        assert (e["reason"] is None) if not REASONS[e["outcome"]] else (e["reason"] in REASONS[e["outcome"]]), c["id"]
        assert s["faults"] == {f["tool"]: list(range(1, f["fail_first_n"] + 1)) for f in c.get("faults") or []}, c["id"]
        assert 2 <= len(c["checks"]) <= 4 and c["script"], c["id"]
        assert set(c["identity"]) == {"date_of_birth", "phone_last4"} and len(c["products"]) == 1, c["id"]
        assert not e["notes"], (c["id"], "Brief A never uses add_note")
        p = persona_for(c, {"identity": c["identity"]})
        third = c.get("third_party") or c["family"] == "Third party"
        assert ("You are NOT" in p["prompt"]) == bool(third), c["id"]
        if third: assert e["outcome"] == "not_reached", c["id"]
    # the brief's own outcome table and questions, as the reference spec has them
    sys.path.insert(0, str(ROOT / "bench"))
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("bench_run", ROOT / "bench" / "run.py"); br = importlib.util.module_from_spec(spec_); spec_.loader.exec_module(br)
    ref = json.loads((ROOT / "specs" / "reference" / "spec-a.json").read_text())
    ex = br.extraction_checks(ref, CASES)
    assert ex["outcomes"]["ok"] and ex["identity"]["ok"], ex
    assert [b["name"] for b in br.discover(names=[HERE.name])] == [HERE.name]
    assert [f.name for f in br.discover(names=[HERE.name])[0]["files"]] == ["brief.md"]
    print("format, personas and extraction checks ok")


def replay():
    """An agent that does exactly what `expected` says and retries a failed tool once. Its ledger must pass."""
    for c in CASES:
        bank = Bank(OPENAPI)
        st, r = bank.session({"seed": c["seed"], "case": c["id"]})
        assert st == 200 and r["ok"], (c["id"], r)
        cid, exp = r["variables"]["customer_id"], c["expected"]
        dead = []

        def call(tool, **args):
            for _ in range(1 + RETRIES):
                st, res = bank.tool(tool, {"customer_id": cid, **args})
                if st == 200:
                    assert res.get("ok"), (c["id"], tool, args, res)
                    return res
            dead.append(tool)
            return None

        third = c.get("third_party") or exp["outcome"] == "not_reached"
        if not third:
            for field, answer in c["identity"].items():
                if not dead: call("verify_identity", field=field, answer=answer)
            if not dead: call("get_account")
            for p in exp["promises"]:
                if not dead: call("create_promise", product_id=p["product_id"], amount=p["amount"], date=p["date"])
            for t in exp["transfers"]:
                if not dead: call("transfer_to_agent", queue=t["queue"])
        for cb in exp["callbacks"]:
            if not dead: call("schedule_callback", date=cb["date"], time=cb["time"])
        call("close_call", outcome=exp["outcome"], **({"reason": exp["reason"]} if exp["reason"] else {}))
        _, led = bank.ledger(cid)
        sc = score_ledger(c, led)
        assert sc["pass"], (c["id"], sc["failed"])
        for f in c.get("faults") or []:
            n = led["failures"].get(f["tool"])
            if f["fail_first_n"] > RETRIES:
                # outlasts the retry: the call must give up with service_error and record nothing
                assert dead == [f["tool"]] and n == 1 + RETRIES and (exp["outcome"], exp["reason"]) == ("no_promise", "service_error"), (c["id"], dead, n)
            else:
                assert n == f["fail_first_n"] and not dead, (c["id"], led["failures"])
                assert (exp["outcome"], exp["reason"]) != ("no_promise", "service_error"), c["id"]
        if exp["outcome"] != "not_reached":
            assert not dead or dead == [f["tool"] for f in c["faults"]], (c["id"], dead)
    print("replay on our bank ok for", len(CASES), "cases")


if __name__ == "__main__":
    check_derived()
    check_format()
    replay()
    print("all expected values derived, formatted and replayed")
