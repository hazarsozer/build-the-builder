"""Replay every answer in data/test-customers.json against the mock and compare status + body.

    python3 final-mock/parity.py http://localhost:8787 [--skip-delay]

Also checks the not-found / empty-field answers. Exit 1 on any mismatch.
"""
import json, pathlib, subprocess, sys

BASE = sys.argv[1].rstrip("/")
SKIP_DELAY = "--skip-delay" in sys.argv
DB = json.loads((pathlib.Path(__file__).parent / "data" / "test-customers.json").read_text())
VOLATILE = ("guid",)


def post(path, data):
    out = subprocess.run(["curl", "-4", "-s", "-m", "30", "-A", "parity/1", "-o", "-", "-w", "\n%{http_code}",
                          "-H", "content-type: application/json", "-X", "POST", BASE + path,
                          "-d", json.dumps({"data": data, "call": {"ani": "5000000000", "lang": "tr"}})],
                         capture_output=True, text=True, check=True).stdout
    body, code = out.rsplit("\n", 1)
    return int(code), json.loads(body)


def strip(x):
    if isinstance(x, dict):
        return {k: strip(v) for k, v in x.items() if k not in VOLATILE}
    return x


bad = n = 0


def check(label, path, data, http, body, exact=True):
    global bad, n
    n += 1
    code, got = post(path, data)
    ok = code == http and (strip(got) == strip(body) if exact else got.get("result", {}).get("code") == body)
    if not ok:
        bad += 1
        print("MISMATCH", label, code, http, json.dumps(got, ensure_ascii=False)[:200])


for c in DB["customers"]:
    no = c["customerNo"]
    s = c["services"]
    for svc in ("cards.list", "contact.email.get", "statement.pdf.send"):
        leaf = s.get(svc)
        data = {"customerNo": int(no) if svc == "contact.email.get" else no}
        if svc == "contact.email.get":
            data["intAddressInfoType"] = "E"
        if svc == "statement.pdf.send":
            data.update(cardNumber="540000******0000", currencyType="TL", statementDate="05.10.2026", emailAddress="x@y.example")
        if leaf:
            check(f"{no} {svc}", "/voice/v1/" + svc, data, leaf["http"], leaf["response"])
        elif svc == "statement.pdf.send":
            check(f"{no} {svc} (no entry)", "/voice/v1/" + svc, data, 404, "FB_IVR_CHANNEL_107", exact=False)
    for svc in ("cards.balance", "statement.week.get"):
        for card, leaf in (s.get(svc) or {}).items():
            if leaf.get("delaySeconds") and SKIP_DELAY:
                continue
            check(f"{no} {svc} {card}", "/voice/v1/" + svc, {"cardNumber": card}, leaf["http"], leaf["response"])
    for svc in ("statement.week.decide", "statement.week.change"):
        for card, weeks in (s.get(svc) or {}).items():
            for wk, leaf in weeks.items():
                check(f"{no} {svc} {card} w{wk}", "/voice/v1/" + svc,
                      {"cardNumber": card, "newStatementWeek": int(wk), "statementDay": 5}, leaf["http"], leaf["response"])

# not-found and empty-field answers
check("unknown customer cards.list", "/voice/v1/cards.list", {"customerNo": "11111111"}, 404, "FB_IVR_CHANNEL_102", exact=False)
check("unknown card balance", "/voice/v1/cards.balance", {"cardNumber": "999999******0000"}, 404, "FB_IVR_CHANNEL_120", exact=False)
check("unknown card week.get", "/voice/v1/statement.week.get", {"cardNumber": "999999******0000"}, 404, "FB_IVR_CHANNEL_121", exact=False)
check("unknown card decide", "/voice/v1/statement.week.decide", {"cardNumber": "999999******0000", "newStatementWeek": 2, "statementDay": 5}, 400, "INVALID_ARGUMENTS_ERROR", exact=False)
check("empty card balance", "/voice/v1/cards.balance", {"cardNumber": ""}, 400, "INVALID_ARGUMENTS_ERROR", exact=False)
check("empty type email", "/voice/v1/contact.email.get", {"customerNo": "90000001", "intAddressInfoType": ""}, 400, "INVALID_ARGUMENTS_ERROR", exact=False)
print(f"{n - bad}/{n} match")
sys.exit(1 if bad else 0)
