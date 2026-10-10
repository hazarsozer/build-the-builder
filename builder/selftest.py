"""Run generated cases (builder/cases.py) against an agent wired to OUR mock bank, and score them.

    run(agent_id, cases, bank, styles=None, store=None) -> {"passed", "total", "rows", "simulation_id", "iteration_id", "status"}

`bank` is our bank's base URL (the Cloudflare tunnel, or http://127.0.0.1:<port>). Each case seeds a fresh customer
(`POST /v1/practice/sessions {"seed": ...}`), personas come from tools/run_cases.persona_for so the customer behaves as
the organisers' customers do, one Freya simulation runs every case, and each case is scored from our ledger
(tools/score.score_ledger) plus the judge's verdict. A run where two agent replies open with the same four words
(the spec's fixed sentences removed first) fails that case with "repeated opening".
"""
import hashlib, json, pathlib, re, sys, time, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "tools"):
    if str(_p) not in sys.path: sys.path.insert(0, str(_p))
import freya  # noqa: E402
from run_cases import persona_for  # noqa: E402
from score import score_ledger  # noqa: E402

UA = {"user-agent": "freya-builder-selftest/0.1", "content-type": "application/json"}


def _http(base, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=UA,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r: return json.load(r)
    except urllib.error.HTTPError as e: return json.load(e)


def _pattern(sentence):
    """A fixed sentence as a regex; {placeholders} match any text."""
    return re.sub(r"\\\{\w+\\\}", ".+?", re.escape(sentence))


def repeated_opening(replies, fixed):
    """The first four words two replies share (lower case), after removing the fixed sentences; None if no repeat."""
    pats = [re.compile(_pattern(s), re.I) for s in sorted(fixed, key=len, reverse=True)]
    seen = set()
    for text in replies:
        rest = text or ""
        for p in pats: rest = p.sub(" ", rest)
        words = re.findall(r"[a-z0-9']+", rest.lower())
        if len(words) < 4: continue
        key = " ".join(words[:4])
        if key in seen: return key
        seen.add(key)
    return None


def _personas(styled, store):
    """Persona ids, created only when the persona text is new (same store scheme as run_cases.py)."""
    store = pathlib.Path(store)
    ids = json.loads(store.read_text()) if store.exists() else {}
    spec = {c["id"]: persona_for(c, {"identity": c["identity"]}, style) for c, style in styled}
    key = {i: i + ":" + hashlib.sha1(json.dumps(p, sort_keys=True).encode()).hexdigest()[:10] for i, p in spec.items()}
    new = [c for c, _ in styled if key[c["id"]] not in ids]
    if new:
        made = freya.create_personas([spec[c["id"]] for c in new])["personas"]
        ids.update({key[c["id"]]: p["id"] for c, p in zip(new, made)})
        store.write_text(json.dumps(ids, indent=1))
    return {c["id"]: ids[key[c["id"]]] for c, _ in styled}


def run(agent_id, cases, bank, styles=None, store=None):
    base = bank.rstrip("/")
    styled = [(c, styles[i % len(styles)] if styles else None) for i, c in enumerate(cases)]
    sessions = {}
    for c in cases:
        r = _http(base, "/v1/practice/sessions", {"seed": c["seed"], "case": c["id"]})
        if not r.get("ok"): raise RuntimeError(f"seeding {c['id']} failed: {r}")
        sessions[c["id"]] = r["variables"]
    pids = _personas(styled, store or ROOT / ".personas.json")
    sim_id, it_id, status = freya.run_simulation(agent_id, f"selftest {time.strftime('%H:%M:%S')}", {pids[c["id"]]: sessions[c["id"]] for c in cases})
    judge = {r["personaId"]: r for r in freya.get_iteration(sim_id, it_id)["runs"]}
    rows = []
    for c, style in styled:
        cid = sessions[c["id"]]["customer_id"]
        led = _http(base, "/v1/practice/ledger/" + cid)
        r = score_ledger(c, led)
        j = judge.get(pids[c["id"]], {})
        if j.get("outcome") != "pass": r["failed"].append("conversation: " + " ".join(str(j.get("reason") or "no verdict").split())[:400])
        rep = None
        if j.get("callId"):
            try: msgs = freya.get_call(j["callId"]).get("messages", [])
            except Exception: msgs = []
            rep = repeated_opening([m.get("message") or "" for m in msgs if m.get("role") == "assistant"], c.get("fixed") or [])
            if rep: r["failed"].append("repeated opening")
        rows.append({"id": c["id"], "scenario": c.get("scenario"), "level": c["level"], "family": c["family"], "title": c["title"], "style": style,
                     "pass": not r["failed"], "failed": r["failed"], "repeated_opening": rep, "call_id": j.get("callId"),
                     "tools": [x["tool"] for x in led.get("calls", [])], "customer_id": cid})
    return {"passed": sum(r["pass"] for r in rows), "total": len(rows), "rows": rows,
            "simulation_id": sim_id, "iteration_id": it_id, "status": status}
