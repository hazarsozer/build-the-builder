"""Does our mock bank (bank/server.py) behave like the official one? Two checks.

    python tools/conformance.py <agent_id_official> <agent_id_ours|auto> [--official-results results/run-a3.json ...]
                                [--workflow out/brief-a.workflow.json] [--slot conf-ours] [--only PUB-01,PUB-03] [--out results/conformance.md]
    python tools/conformance.py --replay-only --official-results results/run-a*.json

1. Replay (no simulation, deterministic). Every tool call in an official ledger is replayed against a fresh session of
   the same case on our bank, and every call in one of our ledgers against a fresh official session. Each call must
   get the same HTTP status and the same JSON body. This checks the bank and nothing else.
2. Live. Brief A's public cases run on both banks with the same agent; the ledgers are diffed per case on what the
   scorer reads: outcome, reason, promises, notes, callbacks, transfers. The agent is not deterministic, so a case
   matches when our ledger equals one of the official ledgers for it.

`auto` for the second agent id: start our bank and a Cloudflare quick tunnel, copy --workflow with the official bank
URL replaced by the tunnel's, save it as --slot, and run on that. The tunnel is stopped at the end.
Official ledgers come from --official-results (earlier run_cases.py outputs with customer ids), or from a new run.

Needs FREYA_API_KEY and FREYA_BANK_TEAM_KEY in the environment.
"""
import argparse, hashlib, json, pathlib, sys, time
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import freya
from score import http as official, score_ledger
from run_cases import persona_for
from bank import server, tunnel

OFFICIAL_V1 = "https://freya-bank-mock.freyavoice.workers.dev/v1"
FIELDS = ("outcome", "reason", "promises", "notes", "callbacks", "transfers")


def scored(led):
    """The parts of a ledger the scorer reads, in a comparable form."""
    return {"outcome": led.get("outcome"), "reason": led.get("reason"),
            "promises": sorted((p["product_id"], round(float(p["amount"]), 2), p["date"]) for p in led.get("promises", [])),
            "notes": sorted((n.get("product_id") or "", n["reason"]) for n in led.get("notes", [])),
            "callbacks": sorted((c["date"], c["time"]) for c in led.get("callbacks", [])),
            "transfers": sorted(t["queue"] for t in led.get("transfers", []))}


def diff(a, b): return {k: (a[k], b[k]) for k in FIELDS if a[k] != b[k]}


# ---- replay -------------------------------------------------------------------------------------------------------

def local_caller(bank):
    def call(path, body=None):
        if path == "/v1/practice/sessions": return bank.session(body)[1]
        return bank.tool(path[len("/v1/"):], body)
    return call


def official_tool(path, body):
    """A tool call on the official mock, with its HTTP status (score.http drops it)."""
    import urllib.request, urllib.error
    req = urllib.request.Request(OFFICIAL_V1[:-3] + path, data=json.dumps(body).encode(),
                                 headers={"user-agent": "freya-bank-conformance/0.1", "content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)


def replay(case_id, calls, target):
    """Replay `calls` (ledger entries) on a fresh session of case_id at target ('ours' bank object or 'official').
    Returns the list of mismatches [(n, tool, args, recorded, replayed)]."""
    if target == "official":
        cid = official("/v1/practice/sessions", {"case": case_id})["variables"]["customer_id"]
        run = lambda tool, args: official_tool("/v1/" + tool, {"customer_id": cid, **args})
    else:
        cid = target.session({"case": case_id})[1]["variables"]["customer_id"]
        run = lambda tool, args: target.tool(tool, {"customer_id": cid, **args})
    bad = []
    for c in calls:
        status, result = run(c["tool"], c["args"])
        if (status, result) != (c["status"], c["result"]): bad.append((c["n"], c["tool"], c["args"], (c["status"], c["result"]), (status, result)))
    return bad


# ---- live runs ----------------------------------------------------------------------------------------------------

def personas(cases, store):
    """Persona ids for the cases, made the same way as run_cases.py and sharing its store."""
    ids = json.loads(store.read_text()) if store.exists() else {}
    spec = {c["id"]: persona_for(c, {"identity": c["identity"]}, None) for c in cases}
    key = {i: i + ":" + hashlib.sha1(json.dumps(p, sort_keys=True).encode()).hexdigest()[:10] for i, p in spec.items()}
    new = [c for c in cases if key[c["id"]] not in ids]
    if new:
        made = freya.create_personas([spec[c["id"]] for c in new])["personas"]
        ids.update({key[c["id"]]: p["id"] for c, p in zip(new, made)}); store.write_text(json.dumps(ids, indent=1))
    return {c["id"]: ids[key[c["id"]]] for c in cases}


def live(agent_id, cases, pids, new_session, read_ledger, label):
    """One simulation of every case. Returns {case_id: {"customer_id", "ledger", "judge"}}."""
    sessions = {c["id"]: new_session(c["id"]) for c in cases}
    sim_id, it_id, status = freya.run_simulation(agent_id, f"conformance {label} {time.strftime('%H:%M:%S')}", {pids[c["id"]]: sessions[c["id"]] for c in cases})
    judge = {r["personaId"]: r for r in freya.get_iteration(sim_id, it_id)["runs"]}
    print(f"{label}: simulation {sim_id} {status}", flush=True)
    return {c["id"]: {"customer_id": sessions[c["id"]]["customer_id"], "ledger": read_ledger(sessions[c["id"]]["customer_id"]),
                      "judge": judge.get(pids[c["id"]], {}).get("outcome")} for c in cases}


def load_official(paths, cases):
    """Official ledgers from earlier run_cases.py outputs: {case_id: [{"customer_id", "ledger", "judge", "source"}]}."""
    out = {c["id"]: [] for c in cases}
    for p in paths:
        d = json.loads(pathlib.Path(p).read_text())
        for rows in d.get("all_rows") or [d["rows"]]:
            for r in rows:
                if r["id"] not in out or not r.get("customer_id"): continue
                led = official("/v1/practice/ledger/" + r["customer_id"])
                if led.get("ok") is False: continue
                out[r["id"]].append({"customer_id": r["customer_id"], "ledger": led, "judge": None, "source": pathlib.Path(p).name})
    return out


# ---- report -------------------------------------------------------------------------------------------------------

def fmt(v): return json.dumps(v, separators=(", ", ": "))


def report(cases, offi, ours, replays, out):
    L = ["# Mock bank conformance", "", f"Generated {time.strftime('%Y-%m-%d %H:%M')} by `tools/conformance.py`.", ""]
    if replays:
        n_calls = sum(r["calls"] for r in replays); n_bad = sum(len(r["bad"]) for r in replays)
        L += ["## 1. Replay: same calls, same answers", "",
              f"{len(replays)} ledgers replayed, {n_calls} tool calls, **{n_bad} mismatches** (status + JSON body compared exactly).", "",
              "| Ledger | Case | Replayed on | Calls | Mismatches |", "|---|---|---|---|---|"]
        for r in replays: L.append(f"| {r['source']} `{r['customer_id']}` | {r['case']} | {r['target']} | {r['calls']} | {len(r['bad'])} |")
        for r in replays:
            for n, tool, args, rec, rep in r["bad"]:
                L += ["", f"- {r['case']} call {n} `{tool}` {fmt(args)}: recorded {fmt(rec)}, replayed {fmt(rep)}"]
        L.append("")
    if ours:
        L += ["## 2. Live: the same agent on both banks", "",
              "Compared on what the scorer reads. A case matches when our ledger equals at least one official ledger for it.", "",
              "| Case | Ours: score | Official: scores | Match | Differences against the closest official ledger |", "|---|---|---|---|---|"]
        same = 0
        for c in cases:
            o = ours.get(c["id"]); offs = offi.get(c["id"], [])
            if not o: continue
            mine = scored(o["ledger"]); ds = [diff(mine, scored(x["ledger"])) for x in offs]
            best = min(ds, key=len) if ds else None
            match = best == {}; same += match
            s_ours = "pass" if score_ledger(c, o["ledger"])["pass"] else "fail"
            s_off = " ".join("pass" if score_ledger(c, x["ledger"])["pass"] else "fail" for x in offs) or "none"
            d = "; ".join(f"{k}: ours {fmt(a)}, official {fmt(b)}" for k, (a, b) in (best or {}).items()) or ("none" if best == {} else "no official ledger")
            L.append(f"| {c['id']} | {s_ours} | {s_off} | {'yes' if match else 'NO'} | {d} |")
        L += ["", f"**{same} / {len(ours)} cases match.** Ledger scores only; the judge's conversation verdicts are not compared.", ""]
        L += ["### Tool sequences", ""]
        for c in cases:
            o = ours.get(c["id"])
            if not o: continue
            seq = lambda led: " > ".join(f"{x['tool'].replace('verify_identity', 'verify')}{'(500)' if x['status'] == 500 else ''}" for x in led["calls"])
            L.append(f"- {c['id']} ours: {seq(o['ledger'])}")
            for x in offi.get(c["id"], []): L.append(f"  - official ({x['source']}): {seq(x['ledger'])}")
        L.append("")
    pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(out).write_text("\n".join(L))
    print("\n".join(L))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agent_official", nargs="?"); ap.add_argument("agent_ours", nargs="?")
    ap.add_argument("--official-results", nargs="*", default=[]); ap.add_argument("--replay-only", action="store_true")
    ap.add_argument("--ours-results", help="JSON written by an earlier live run of ours (skips the simulation)")
    ap.add_argument("--workflow", default=str(ROOT / "out" / "brief-a.workflow.json")); ap.add_argument("--slot", default="conf-ours")
    ap.add_argument("--state", default=str(ROOT / ".freya-agents.json")); ap.add_argument("--personas", default=str(ROOT / ".personas.json"))
    ap.add_argument("--port", type=int, default=0); ap.add_argument("--only"); ap.add_argument("--brief", default="A")
    ap.add_argument("--out", default=str(ROOT / "results" / "conformance.md"))
    a = ap.parse_args()
    cases = [c for c in json.loads((ROOT / "public-test-set.json").read_text()) if c["brief"] == a.brief and (not a.only or c["id"] in a.only.split(","))]
    openapi = json.loads((ROOT / "tools" / "openapi.json").read_text())
    srv = server.serve(a.port, openapi); bank = srv.bank
    proc = None
    try:
        offi = load_official(a.official_results, cases)
        ours = {}
        if a.ours_results: ours = json.loads(pathlib.Path(a.ours_results).read_text())
        elif not a.replay_only:
            pids = personas(cases, pathlib.Path(a.personas))
            if not any(offi.values()):
                offi = {k: [dict(v, source="new official run")] for k, v in live(a.agent_official, cases, pids, lambda i: official("/v1/practice/sessions", {"case": i})["variables"],
                                                                                lambda cid: official("/v1/practice/ledger/" + cid), "official").items()}
            agent = a.agent_ours
            if agent == "auto":
                url, proc = tunnel.start(srv.server_address[1])
                print("tunnel", url, flush=True)
                wf = json.loads(pathlib.Path(a.workflow).read_text().replace(OFFICIAL_V1, url + "/v1"))
                agent = freya.save_workflow(a.slot, wf, state_file=a.state)["agent_id"]
                print("agent", agent, flush=True)
            ours = live(agent, cases, pids, lambda i: bank.session({"case": i})[1]["variables"], lambda cid: bank.ledger(cid)[1], "ours")
            raw = pathlib.Path(a.out).with_suffix(".ours.json"); raw.write_text(json.dumps(ours, indent=1))
        replays = []
        for cid_, runs in offi.items():
            for x in runs:
                bad = replay(cid_, x["ledger"]["calls"], bank)
                replays.append({"source": x["source"], "customer_id": x["customer_id"], "case": cid_, "target": "ours", "calls": len(x["ledger"]["calls"]), "bad": bad})
        for cid_, x in ours.items():
            bad = replay(cid_, x["ledger"]["calls"], "official")
            replays.append({"source": "ours", "customer_id": x["customer_id"], "case": cid_, "target": "official", "calls": len(x["ledger"]["calls"]), "bad": bad})
        report(cases, offi, ours, replays, a.out)
    finally:
        if proc: tunnel.stop(proc)
        srv.shutdown()


if __name__ == "__main__": main()
