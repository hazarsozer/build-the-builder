"""A local copy of the organisers' mock bank (https://freya-bank-mock.freyavoice.workers.dev).

    serve(port, openapi) -> ThreadingHTTPServer, already running in a daemon thread
    python -m bank.server [--port 8787] [--openapi tools/openapi.json]

Routes (no key needed, unlike the official practice routes):
    POST /v1/<tool>                       the tools named in the openapi paths
    POST /v1/practice/sessions            {"seed": {...}} or {"case": "PUB-01"} -> {"ok": true, "variables": {customer_id, ...}}
    GET  /v1/practice/ledger/<customer_id> the ledger, same shape as the official one (tools/score.py reads it)

Seed: {"variables": {first_name, last_name, title, now}, "identity": {field: answer}, "products": [{...}],
       "faults": {"create_promise": [1, 2]},   # 1-based call numbers of that tool that return HTTP 500
       "reasons": {"no_promise": ["refused", ...], "promise": []}}   # close_call table from the brief; optional
Without "reasons", close_call accepts any non-empty reason, and none for promise/paid.
Limits (a tunnel exposes this): body <= 64 KiB (else 413), 2000 sessions (oldest activity evicted), 500 calls per session.

Every response body, error text and the order of checks below was copied from probes of the official mock on
2026-10-10 (see .superpowers/sdd/2026-10-10-spec-generator-builder/task-3-report.md).
"""
import argparse, json, pathlib, re, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES = ROOT / "public-test-set.json"

# close_call reasons come from the brief, not the openapi, so a seed carries them: "reasons": {outcome: [allowed] | []}.
# Without a table any non-empty reason is accepted, except for these outcomes, which take none.
NO_REASON = ("promise", "paid")
# Limits while a tunnel exposes the server to the internet.
MAX_BODY = 64 * 1024
MAX_SESSIONS = 2000
MAX_CALLS = 500
WRONG_ANSWERS = 3
NOT_A_DATE = "answer is not a date. Send it as YYYY-MM-DD."
FAULT = {"ok": False, "error": "service unavailable, nothing was recorded"}
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
ISO_DATE, HHMM = re.compile(r"^\d{4}-\d{2}-\d{2}$"), re.compile(r"^\d{2}:\d{2}$")


# ---- answer parsing -------------------------------------------------------------------------------------------

def parse_date(text):
    """(year, month, day) as written, not range-checked (the official mock counts 1988-13-40 as a wrong answer), or None."""
    if not isinstance(text, str): return None
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m: return int(m[1]), int(m[2]), int(m[3])
    m = re.search(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", text)
    if m: return int(m[3]), int(m[2]), int(m[1])
    month = lambda w: MONTHS.index(w[:3].lower()) + 1 if w[:3].lower() in MONTHS else None
    m = re.search(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+([A-Za-z]{3,})\.?,?\s+(\d{4})", text)
    if m and month(m[2]): return int(m[3]), month(m[2]), int(m[1])
    m = re.search(r"([A-Za-z]{3,})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", text)
    if m and month(m[1]): return int(m[3]), month(m[1]), int(m[2])
    return None


def field_kinds(openapi, identity):
    """date | digits | text per identity field, read from the verify_identity `answer` description."""
    try:
        desc = openapi["paths"]["/verify_identity"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["answer"].get("description", "")
    except (KeyError, TypeError): desc = ""
    kinds = {}
    for f in identity:
        m = re.search(r"For " + re.escape(f) + r":([^.]*(?:\.\d[^.]*)?)", desc)
        said = (m[1] if m else "").lower()
        if "yyyy-mm-dd" in said or (not said and "date" in f): kinds[f] = "date"
        elif "digit" in said or (not said and re.search(r"last4|digits|number|year", f)): kinds[f] = "digits"
        else: kinds[f] = "text"
    return kinds


def enum(openapi, tool, prop):
    try: return openapi["paths"]["/" + tool]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"][prop]["enum"]
    except (KeyError, TypeError): return None


def num(x):
    """JSON numbers the way the official (JavaScript) mock prints them: 420.0 -> 420."""
    return int(x) if isinstance(x, float) and x.is_integer() else x


def jsnum(x):
    """JavaScript Number(x) for the values an agent sends; None when it is not a finite number."""
    if isinstance(x, bool): return float(x)
    if isinstance(x, (int, float)): return float(x)
    if isinstance(x, str):
        s = x.strip()
        if s == "": return 0.0
        try: v = float(s)
        except ValueError: return None
        return v if v == v and abs(v) != float("inf") else None
    return None


def say_or(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " or " + items[-1]


# ---- the bank -------------------------------------------------------------------------------------------------

class Bank:
    def __init__(self, openapi):
        self.lock = threading.Lock()
        self._load(openapi)

    def reset(self, openapi):
        """Serve another openapi from the same server: drop every session and reload the enums, under the lock."""
        with self.lock:
            self._load(openapi)

    def _load(self, openapi):
        self.openapi, self.sessions = openapi, {}
        self.tools = [p.strip("/") for p in openapi.get("paths", {})]
        self.fields = enum(openapi, "verify_identity", "field") or []
        self.outcomes = enum(openapi, "close_call", "outcome")
        self.seq = 0
        self.note_reasons = enum(openapi, "add_note", "reason") or []
        self.queues = enum(openapi, "transfer_to_agent", "queue") or []

    def session(self, body):
        if "seed" in body: seed, case = body["seed"], body.get("case")
        else:
            cases = {c["id"]: c for c in json.loads(CASES.read_text())} if CASES.exists() else {}
            if body.get("case") not in cases: return 404, {"ok": False, "error": "unknown practice case"}
            seed, case = seed_from_case(cases[body["case"]]), body["case"]
        cid = "c_" + str(uuid.uuid4())[:12]
        variables = {"customer_id": cid, **(seed.get("variables") or {})}
        identity = dict(seed.get("identity") or {})
        products = [{"product_id": f"p{i}", **{k: num(v) for k, v in p.items()}} for i, p in enumerate(seed.get("products") or [], 1)]
        reasons = seed.get("reasons")
        with self.lock:
            while len(self.sessions) >= MAX_SESSIONS:       # evict the session with the oldest last activity
                del self.sessions[min(self.sessions, key=lambda k: self.sessions[k]["seq"])]
            self.seq += 1
            self.sessions[cid] = {"case": case, "reasons": dict(reasons) if isinstance(reasons, dict) else None, "seq": self.seq, "variables": variables, "identity": identity, "kinds": field_kinds(self.openapi, identity),
                                  "products": products, "faults": {t: set(ns) for t, ns in (seed.get("faults") or {}).items()},
                                  "tool_calls": {}, "verified": {}, "wrong": 0, "hinted": False, "promises": [], "notes": [],
                                  "callbacks": [], "transfers": [], "outcome": None, "reason": None, "failures": {}, "calls": [],
                                  "closed": False, "last_at": int(time.time() * 1000)}
        return 200, {"ok": True, "variables": variables}

    def ledger(self, cid):
        with self.lock:
            s = self.sessions.get(cid)
            if not s: return 404, {"ok": False, "error": "unknown customer_id for this team key"}
            keys = ["promises", "notes", "callbacks", "transfers", "outcome", "reason", "failures", "calls", "last_at"]
            return 200, json.loads(json.dumps({"ok": True, "case": s["case"], "now": s["variables"].get("now"), "verified": s["verified"],
                                               "wrong": s["wrong"], "hinted": s["hinted"], **{k: s[k] for k in keys}}))

    def tool(self, tool, body):
        cid = body.get("customer_id")
        with self.lock:
            s = self.sessions.get(cid) if isinstance(cid, str) else None
            if s is None: return 404, {"ok": False, "error": "unknown customer_id"}
            self.seq += 1; s["seq"] = self.seq
            if len(s["calls"]) >= MAX_CALLS: return 200, {"ok": False, "error": "too many calls"}
            args = {k: v for k, v in body.items() if k != "customer_id"}
            n = s["tool_calls"][tool] = s["tool_calls"].get(tool, 0) + 1
            s["failures"].setdefault(tool, 0)
            if s["closed"]: status, result = 200, {"ok": False, "error": "the call is already closed"}
            elif n in s["faults"].get(tool, ()): status, result = 500, dict(FAULT); s["failures"][tool] += 1
            else: status, result = 200, getattr(self, "t_" + tool, self.t_unknown)(s, args)
            s["calls"].append({"n": len(s["calls"]) + 1, "tool": tool, "args": args, "status": status, "result": result})
            s["last_at"] = int(time.time() * 1000)
            return status, json.loads(json.dumps(result))

    # one method per tool; each returns the 200 body
    def t_unknown(self, s, a): return {"ok": True}

    def all_verified(self, s): return all(s["verified"].get(f) for f in s["identity"])

    def t_verify_identity(self, s, a):
        field, answer = a.get("field"), a.get("answer")
        allowed = list(dict.fromkeys(self.fields + list(s["identity"])))
        if field not in allowed: return {"ok": False, "error": "field must be " + say_or(allowed)}
        left = lambda: WRONG_ANSWERS - s["wrong"]
        if s["verified"].get(field): return {"ok": True, "verified": True, "wrong_answers_left": left()}
        if left() <= 0: return {"ok": False, "verified": False, "wrong_answers_left": 0, "error": "identity check is over"}
        truth, kind = str(s["identity"].get(field, "")), s["kinds"].get(field, "text")
        if kind == "date":
            got, want = parse_date(answer), parse_date(truth)
            if got is None: return {"ok": False, "error": NOT_A_DATE}
            if got == want: ok = True
            elif not s["hinted"] and want and (got[0], got[2], got[1]) == want:
                s["hinted"] = True
                return {"ok": True, "verified": False, "wrong_answers_left": left(), "hint": "day_month_swapped"}
            else: ok = False
        elif kind == "digits":
            ok = re.sub(r"[\s,.\-]", "", str(answer if answer is not None else "")) == truth
        else:
            ok = re.sub(r"\s", "", str(answer if answer is not None else "")).lower() == re.sub(r"\s", "", truth).lower()
        if ok: s["verified"][field] = True
        else: s["wrong"] += 1
        return {"ok": True, "verified": ok, "wrong_answers_left": left(), "all_verified": self.all_verified(s)}

    def t_get_account(self, s, a):
        if not self.all_verified(s): return {"ok": False, "error": "identity not verified"}
        return {"ok": True, "products": s["products"]}

    def t_create_promise(self, s, a):
        if not self.all_verified(s): return {"ok": False, "error": "identity not verified"}
        product = next((p for p in s["products"] if p["product_id"] == a.get("product_id")), None)
        if product is None: return {"ok": False, "error": "unknown product_id"}
        amount = jsnum(a.get("amount"))
        if amount is None or amount <= 0: return {"ok": False, "error": "amount must be a positive number"}
        if not isinstance(a.get("date"), str) or not ISO_DATE.match(a["date"]): return {"ok": False, "error": "date must be YYYY-MM-DD"}
        if any(p["product_id"] == product["product_id"] for p in s["promises"]): return {"ok": False, "error": "this product already has a promise"}
        pid = f"pr{len(s['promises']) + 1}"
        s["promises"].append({"promise_id": pid, "product_id": product["product_id"], "type": product.get("type"), "amount": num(amount), "date": a["date"]})
        return {"ok": True, "promise_id": pid}

    def t_add_note(self, s, a):
        if self.note_reasons and a.get("reason") not in self.note_reasons: return {"ok": False, "error": "unknown reason"}
        s["notes"].append({"product_id": a.get("product_id"), "reason": a.get("reason"), "text": a.get("text") or ""})
        return {"ok": True}

    def t_schedule_callback(self, s, a):
        d, t = a.get("date"), a.get("time")
        if not (isinstance(d, str) and ISO_DATE.match(d) and isinstance(t, str) and HHMM.match(t)):
            return {"ok": False, "error": "date must be YYYY-MM-DD and time HH:MM"}
        s["callbacks"].append({"date": d, "time": t})
        return {"ok": True}

    def t_transfer_to_agent(self, s, a):
        if self.queues and a.get("queue") not in self.queues: return {"ok": False, "error": "unknown queue"}
        s["transfers"].append({"queue": a.get("queue")})
        return {"ok": True}

    def t_close_call(self, s, a):
        outcome, reason = a.get("outcome"), a.get("reason")
        table = s["reasons"]
        known = (self.outcomes or []) + list(table or {})
        if not isinstance(outcome, str) or (known and outcome not in known): return {"ok": False, "error": "unknown outcome"}
        if table is not None and outcome in table: need = list(table[outcome] or [])
        elif outcome in NO_REASON: need = []
        else: need = None                                  # no table for this outcome: any non-empty reason
        if need is None:
            if not (isinstance(reason, str) and reason.strip()): return {"ok": False, "error": "this outcome needs a reason"}
        elif (reason not in need) if need else bool(reason):
            return {"ok": False, "error": "this outcome needs one of these reasons: " + ", ".join(need)}
        s["outcome"], s["reason"], s["closed"] = outcome, reason if need != [] else None, True
        return {"ok": True}


def seed_from_case(case):
    """A public-test-set case as a seed. Its faults [{"tool", "fail_first_n"}] become call numbers."""
    faults = {f["tool"]: list(range(1, f.get("fail_first_n", 1) + 1)) for f in case.get("faults") or []}
    return {"variables": case["variables"], "identity": case["identity"], "products": case["products"], "faults": faults}


# ---- HTTP -----------------------------------------------------------------------------------------------------

def make_handler(bank):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 15

        def log_message(self, *a): pass

        def send(self, status, body):
            data = json.dumps(body, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/")
            if path == "/health": return self.send(200, {"ok": True})
            if path.startswith("/v1/practice/ledger/"): return self.send(*bank.ledger(path.rsplit("/", 1)[1]))
            self.send(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            path = self.path.split("?")[0].rstrip("/")
            try: length = int(self.headers.get("Content-Length") or 0)
            except ValueError: length = -1
            if length < 0 or length > MAX_BODY:
                self.close_connection = True
                return self.send(413, {"ok": False, "error": "request body too large"})
            raw = self.rfile.read(length)
            try: body = json.loads(raw or b"{}")
            except ValueError: body = {}
            if not isinstance(body, dict): body = {}
            if path == "/v1/practice/sessions": return self.send(*bank.session(body))
            tool = path[len("/v1/"):] if path.startswith("/v1/") else None
            if tool in bank.tools: return self.send(*bank.tool(tool, body))
            self.send(404, {"ok": False, "error": "not found"})
    return Handler


def serve(port, openapi):
    """Start the bank on 127.0.0.1:<port> (0 picks a free port) in a daemon thread. `.bank` holds the state."""
    bank = Bank(openapi)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(bank))
    srv.daemon_threads, srv.bank = True, bank
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8787); ap.add_argument("--openapi", default=str(ROOT / "tools" / "openapi.json"))
    a = ap.parse_args()
    srv = serve(a.port, json.loads(pathlib.Path(a.openapi).read_text()))
    print(f"bank on http://127.0.0.1:{srv.server_address[1]}", flush=True)
    try: threading.Event().wait()
    except KeyboardInterrupt: srv.shutdown()
