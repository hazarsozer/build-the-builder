"""Run your own test cases against the agent on OUR mock bank (for briefs the official practice routes do not seed).

    python3 fallback/selftest_cli.py <slot> <workflow.json> <cases.json> [--out results/selftest.json]

<cases.json> is a list in the public-test-set.json format (id, family, level, title, variables, identity, products, script,
expected, optional faults). Optional per case: "reasons": {"no_promise": ["refused", ...], "promise": []} = the brief's close_call table.
The workflow file must use https://freya-bank-mock.freyavoice.workers.dev/v1 as the tool base URL; this script starts the bank and a
Cloudflare tunnel, swaps that URL for the tunnel URL, saves the workflow under <slot>-selftest, runs ONE simulation of all cases and
prints passed/total plus each failing case with its reasons. The tunnel is stopped at the end.
"""
import argparse, json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import freya
from bank import server, tunnel
from builder import selftest
from builder.redact import scrub

OFFICIAL_V1 = "https://freya-bank-mock.freyavoice.workers.dev/v1"


def prepare(cases):
    out = []
    for c in cases:
        c = dict(c)
        seed = server.seed_from_case(c)
        if c.get("reasons"): seed["reasons"] = c["reasons"]
        c.setdefault("seed", seed)
        c.setdefault("scenario", c.get("family"))
        out.append(c)
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("slot"); ap.add_argument("workflow"); ap.add_argument("cases")
    ap.add_argument("--out", default=str(ROOT / "results" / "selftest.json"))
    a = ap.parse_args()
    openapi = json.loads((ROOT / "tools" / "openapi.json").read_text())
    cases = prepare(json.loads(pathlib.Path(a.cases).read_text()))
    srv, proc = server.serve(0, openapi), None
    try:
        url, proc = tunnel.start(srv.server_address[1])
        wf = json.loads(pathlib.Path(a.workflow).read_text().replace(OFFICIAL_V1, url + "/v1"))
        agent = freya.save_workflow(a.slot + "-selftest", wf, state_file=str(ROOT / ".freya-agents.json"))["agent_id"]
        res = selftest.run(agent, cases, url)
        res["agent_id"] = agent
        pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(a.out).write_text(scrub(json.dumps(res, indent=1)))
        print(scrub(json.dumps({"passed": res["passed"], "total": res["total"], "agent_id": agent})))
        for r in res["rows"]:
            if not r["pass"]: print(scrub(json.dumps({"id": r["id"], "title": r["title"], "failed": r["failed"]})))
            for w in r.get("warnings") or []: print(scrub(f"warning {r['id']} {w}"))
    finally:
        if proc: tunnel.stop(proc)
        srv.shutdown()


if __name__ == "__main__": main()
