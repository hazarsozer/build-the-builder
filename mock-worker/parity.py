"""Replay the same requests against our Python bank (bank/server.py, started in-process) and the Worker, and diff
every response and the final ledger. customer_id and last_at are masked (random / time).

    python3 mock-worker/parity.py http://127.0.0.1:8790 [--openapi tools/openapi.json]
Exit 0 when every scenario matches.
"""
import argparse, json, pathlib, sys, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bank import server  # noqa: E402

SEED = {"variables": {"first_name": "Daniel", "last_name": "Reyes", "title": "Mr", "now": "2026-10-14T11:00"},
        "identity": {"date_of_birth": "1988-03-14", "phone_last4": "4471"},
        "products": [{"type": "credit_card", "card_last4": "4821", "past_due": 420.0, "days_late": 6, "minimum_payment": 126.0},
                     {"type": "personal_loan", "loan_last4": "9012", "past_due": 310.5, "days_late": 12}]}
VERIFY = [("verify_identity", {"field": "date_of_birth", "answer": "1988-03-14"}),
          ("verify_identity", {"field": "phone_last4", "answer": "4471"})]

SCENARIOS = {
    "happy_promise": (SEED, VERIFY + [("get_account", {}),
        ("create_promise", {"product_id": "p1", "amount": 420, "date": "2026-10-14"}),
        ("create_promise", {"product_id": "p1", "amount": 420, "date": "2026-10-14"}),
        ("close_call", {"outcome": "promise"})]),
    "wrong_identity_to_limit": (SEED, [
        ("get_account", {}),
        ("verify_identity", {"field": "date_of_birth", "answer": "1988-14-03"}),   # swapped: hint, not counted
        ("verify_identity", {"field": "date_of_birth", "answer": "not a date"}),
        ("verify_identity", {"field": "phone_last4", "answer": "1111"}),
        ("verify_identity", {"field": "phone_last4", "answer": "2222"}),
        ("verify_identity", {"field": "date_of_birth", "answer": "14 March 1987"}),
        ("verify_identity", {"field": "phone_last4", "answer": "4471"}),
        ("verify_identity", {"field": "mothers_maiden_name", "answer": "x"}),
        ("get_account", {}), ("close_call", {"outcome": "no_promise", "reason": "identity_failed"})]),
    "fault_create_promise": ({**SEED, "faults": {"create_promise": [1]}}, VERIFY + [
        ("create_promise", {"product_id": "p2", "amount": "310.5", "date": "2026-10-20"}),
        ("create_promise", {"product_id": "p2", "amount": "310.5", "date": "2026-10-20"}),
        ("create_promise", {"product_id": "p9", "amount": 1, "date": "2026-10-20"}),
        ("create_promise", {"product_id": "p1", "amount": -5, "date": "2026-10-20"}),
        ("create_promise", {"product_id": "p1", "amount": 5, "date": "20/10/2026"})]),
    "callback": (SEED, VERIFY + [
        ("schedule_callback", {"date": "2026-10-15", "time": "9:00"}),
        ("schedule_callback", {"date": "2026-10-15", "time": "09:00"}),
        ("close_call", {"outcome": "callback", "reason": "customer asked"})]),
    "transfer": (SEED, VERIFY + [
        ("transfer_to_agent", {"queue": "nope"}),
        ("transfer_to_agent", {"queue": "hardship"}),
        ("close_call", {"outcome": "transferred", "reason": "hardship"})]),
    "add_note": (SEED, VERIFY + [
        ("add_note", {"product_id": "p1", "reason": "nope", "text": "x"}),
        ("add_note", {"product_id": "p1", "reason": "dispute", "text": "says the charge is wrong"}),
        ("add_note", {"product_id": "p2", "reason": "dispute"})]),
    "close_call": (SEED, [
        ("close_call", {"outcome": "bogus"}),
        ("close_call", {"outcome": "promise", "reason": "x"}),
        ("close_call", {"outcome": "no_promise"}),
        ("close_call", {"outcome": "no_promise", "reason": "refused"}),
        ("close_call", {"outcome": "no_promise", "reason": "refused"}),
        ("verify_identity", {"field": "phone_last4", "answer": "4471"})]),
    "reasons_table_and_hints": ({**SEED, "reasons": {"no_promise": ["refused", "wrong_person"], "promise": []},
                                 "hints": {"day_month_swapped": {"when": "swapped_date"}, "partial": {"when": "prefix"}},
                                 "identity_policy": {"max_wrong": 2, "scope": "per_question"},
                                 "response_aliases": {"create_promise": {"promise_id": "id"}}}, [
        ("verify_identity", {"field": "phone_last4", "answer": "44"}),
        ("verify_identity", {"field": "phone_last4", "answer": "44"}),
        ("verify_identity", {"field": "phone_last4", "answer": "4471"}),
        ("verify_identity", {"field": "date_of_birth", "answer": "March 14, 1988"}),
        ("create_promise", {"product_id": "p1", "amount": 100, "date": "2026-10-16"}),
        ("close_call", {"outcome": "no_promise", "reason": "busy"}),
        ("close_call", {"outcome": "no_promise", "reason": "refused"})]),
}


def http(base, path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json", "x-team-key": "ignored"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())


def mask(x, cid):
    s = json.dumps(x).replace(cid, "<cid>")
    x = json.loads(s)
    if isinstance(x, dict) and "last_at" in x: x["last_at"] = "<t>"
    return x


def run(base, seed, steps, case=None):
    body = {"case": case} if case else {"seed": seed}
    st, r = http(base, "/v1/practice/sessions", body)
    cid = r.get("variables", {}).get("customer_id", "")
    out = [("session", st, mask(r, cid))]
    for tool, args in steps:
        out.append((tool, *http(base, "/v1/" + tool, {"customer_id": cid, **args})))
        out[-1] = (tool, out[-1][1], mask(out[-1][2], cid))
    out.append(("ledger", *http(base, "/v1/practice/ledger/" + cid)))
    out[-1] = ("ledger", out[-1][1], mask(out[-1][2], cid))
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("worker"); ap.add_argument("--openapi", default=str(ROOT / "tools" / "openapi.json"))
    a = ap.parse_args()
    srv = server.serve(0, json.loads(pathlib.Path(a.openapi).read_text()))
    py = f"http://127.0.0.1:{srv.server_address[1]}"
    extra = {
        "unknown_tool": [("POST", "/v1/no_such_tool", {"customer_id": "x"}), ("GET", "/v1/practice/ledger/c_nope", None),
                         ("POST", "/v1/get_account", {"customer_id": "c_nope"}), ("POST", "/v1/practice/sessions", {"case": "NOPE"}),
                         ("GET", "/health", None), ("GET", "/nope", None)],
    }
    bad = 0
    runs = {k: (lambda b, v=v: run(b, *v)) for k, v in SCENARIOS.items()}
    runs["practice_case_PUB-01"] = lambda b: run(b, None, VERIFY + [("get_account", {})], case="PUB-01")
    runs["unknown_tool"] = lambda b: [(m + " " + p, *http(b, p, body)) for m, p, body in extra["unknown_tool"]]
    for name, fn in runs.items():
        want, got = fn(py), fn(a.worker.rstrip("/"))
        diffs = [(i, w, g) for i, (w, g) in enumerate(zip(want, got)) if w != g] + ([("len", len(want), len(got))] if len(want) != len(got) else [])
        print(f"{'OK  ' if not diffs else 'DIFF'} {name} ({len(want)} requests)")
        for d in diffs[:5]: print("   python:", json.dumps(d[1])[:600], "\n   worker:", json.dumps(d[2])[:600])
        bad += bool(diffs)
    srv.shutdown()
    print("all match" if not bad else f"{bad} scenario(s) differ")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
