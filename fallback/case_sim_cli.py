"""Test a saved agent with simulated customers whose tools reach a case's own mock service (no practice bank).

    python3 fallback/case_sim_cli.py <agent_id> <cases.json> [--out results/case-sim.json]

<cases.json> is a list of objects:
  id         short id, e.g. "C01"
  title      one line
  variables  the call variables the case passes, e.g. {"customerNumber": "...", "now": "2026-10-12T10:30:00+03:00"}
  script     who the simulated customer is and what they say and do, in the call's language
  checks     list of sentences the judge must find true of the conversation
Runs ONE simulation of all cases and prints, per case, pass/fail with the judge's reason and the agent's turns and tool
calls (with HTTP status), so a failing case can be read without opening the platform.
"""
import argparse, hashlib, json, pathlib, sys, time
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import freya
from builder.redact import scrub

STORE = ROOT / ".personas.json"
PERSONA = """OUTPUT RULE: Write only the words you say on the phone. No stage directions, no brackets. One or two short sentences per turn, then wait for the other side.

WHAT HAPPENS IN THIS CALL: {script}

RULES:
- Answer what you are asked. Do not volunteer information nobody asked for.
- Do not invent facts that are not written above. If asked something that is not covered, give a short neutral answer.
- When the other side ends the call or transfers you, say goodbye and say nothing more."""


def persona(c):
    return {"name": c["id"] + " " + c["title"], "prompt": PERSONA.format(script=c["script"]),
            "successCriteria": "All of these are true:\n- " + "\n- ".join(c["checks"]), "tags": ["hackathon", "case-sim"]}


def persona_ids(cases):
    ids = json.loads(STORE.read_text()) if STORE.exists() else {}
    spec = {c["id"]: persona(c) for c in cases}
    key = {i: "case:" + i + ":" + hashlib.sha1(json.dumps(p, sort_keys=True).encode()).hexdigest()[:10] for i, p in spec.items()}
    new = [c for c in cases if key[c["id"]] not in ids]
    if new:
        made = freya.create_personas([spec[c["id"]] for c in new])["personas"]
        ids.update({key[c["id"]]: p["id"] for c, p in zip(new, made)})
        STORE.write_text(json.dumps(ids, indent=1))
    return {c["id"]: ids[key[c["id"]]] for c in cases}


def turns(call_id):
    out = []
    try:
        msgs = freya.get_call(call_id).get("messages", [])
    except Exception as e:
        return ["(transcript unavailable: %s)" % type(e).__name__]
    for m in msgs:
        role = m.get("role")
        text = " ".join(str(m.get("message") or "").split())
        if role in ("assistant", "user", "bot", "customer"):
            out.append("%s: %s" % ("AGENT" if role in ("assistant", "bot") else "CUSTOMER", text[:400]))
        elif text or m.get("toolCalls") or m.get("name"):
            out.append("TOOL %s %s" % (m.get("name") or role, json.dumps(m, ensure_ascii=False, default=str)[:300]))
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("agent_id"); ap.add_argument("cases")
    ap.add_argument("--out", default=str(ROOT / "results" / "case-sim.json"))
    a = ap.parse_args()
    cases = json.loads(pathlib.Path(a.cases).read_text())
    pids = persona_ids(cases)
    sim_id, it_id, status = freya.run_simulation(a.agent_id, "case-sim %s" % time.strftime("%H:%M:%S"),
                                                 {pids[c["id"]]: c["variables"] for c in cases})
    runs = {r["personaId"]: r for r in freya.get_iteration(sim_id, it_id)["runs"]}
    rows = []
    for c in cases:
        r = runs.get(pids[c["id"]], {})
        row = {"id": c["id"], "title": c["title"], "pass": r.get("outcome") == "pass",
               "reason": " ".join(str(r.get("reason") or "no verdict").split())[:600], "call_id": r.get("callId"),
               "turns": turns(r["callId"]) if r.get("callId") else []}
        rows.append(row)
    res = {"simulation_id": sim_id, "status": status, "passed": sum(x["pass"] for x in rows), "total": len(rows), "rows": rows}
    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(a.out).write_text(scrub(json.dumps(res, indent=1, ensure_ascii=False)))
    print(scrub("passed %d / %d (simulation %s, %s)" % (res["passed"], res["total"], sim_id, status)))
    for x in rows:
        print(scrub("\n== %s %s: %s\n   judge: %s" % (x["id"], x["title"], "PASS" if x["pass"] else "FAIL", x["reason"])))
        if not x["pass"]:
            for t in x["turns"]:
                print(scrub("   " + t))


if __name__ == "__main__":
    main()
