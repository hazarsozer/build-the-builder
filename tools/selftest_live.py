"""First live end-to-end self-test: our bank + tunnel + compiled spec-a in slot ours-A-selftest + one selftest round.

    set -a; source env.sh; set +a; uv run python tools/selftest_live.py [--out results/selftest-live-1.json]

Starts the bank on a free port and a Cloudflare tunnel, saves the compiled workflow (base URL <tunnel>/v1, as
tools/conformance.py does), runs cases.generate(seed=0, now=2026-10-14T11:00) once with no style, prints the result as
JSON. Library code is not touched. The tunnel is stopped at the end.
"""
import argparse, json, pathlib, sys, time
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import freya
from bank import server, tunnel
from builder import cases, compile as comp, selftest

SLOT, NOW = "ours-A-selftest", "2026-10-14T11:00"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=str(ROOT / "results" / "selftest-live-1.json"))
    a = ap.parse_args()
    t0 = time.time()
    openapi = json.loads((ROOT / "tools" / "openapi.json").read_text())
    spec = json.loads((ROOT / "specs" / "reference" / "spec-a.json").read_text())
    srv = server.serve(0, openapi)
    proc = None
    try:
        print("bank port", srv.server_address[1], flush=True)
        url, proc = tunnel.start(srv.server_address[1])
        print("tunnel", url, flush=True)
        wf = comp.compile(spec, openapi, url + "/v1")
        agent = freya.save_workflow(SLOT, wf, state_file=str(ROOT / ".freya-agents.json"))["agent_id"]
        print("agent", agent, flush=True)
        cs = cases.generate(spec, openapi, NOW, seed=0)
        print("cases", [c["id"] + ":" + str(c.get("scenario")) for c in cs], "skipped", cs.skipped, flush=True)
        res = selftest.run(agent, cs, url)
        res.update(agent_id=agent, seconds=round(time.time() - t0))
        pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(a.out).write_text(json.dumps(res, indent=1))
        print(json.dumps({k: v for k, v in res.items() if k != "rows"}))
    finally:
        if proc: tunnel.stop(proc)
        srv.shutdown()


if __name__ == "__main__": main()
