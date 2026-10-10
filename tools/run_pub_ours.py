"""Run the kit's public practice cases (public-test-set.json) against OUR mock bank, not the organisers' bank.

    set -a; source env.sh; set +a
    uv run python tools/run_pub_ours.py --spec specs/reference/spec-a.json --brief A [--style pieces] [--reps 1]
                                        [--slot ours-A-selftest] [--repo-root PATH]

Starts our bank (tools/openapi.json) and a Cloudflare tunnel, compiles the spec with base URL <tunnel>/v1, saves it to
the slot, seeds each brief case like tools/conformance.py does (PUB case -> seed, Brief A's close_call reasons), runs
builder.selftest for --reps reps and prints one PASS/FAIL line per case plus the total. Writes
results/pub-ours-<brief>-<style>.json. --repo-root puts another worktree first on sys.path so its `builder` package
is the code under test. The tunnel is stopped by pid at the end (tunnel.stop); no pkill.
"""
import argparse, json, pathlib, sys, time
HERE = pathlib.Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True); ap.add_argument("--brief", required=True, choices=["A", "B"])
    ap.add_argument("--style", default=None); ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--slot"); ap.add_argument("--repo-root", default=str(HERE))
    a = ap.parse_args()
    root = pathlib.Path(a.repo_root).resolve()
    # Later insert(0) wins: this worktree's helpers first, then --repo-root on top so its `builder` is the one imported.
    for p in (HERE / "tools", HERE, root / "tools", root):
        if str(p) in sys.path: sys.path.remove(str(p))
        sys.path.insert(0, str(p))
    import freya
    from bank import server, tunnel
    from builder import compile as comp, selftest
    from conformance import BRIEF_A_REASONS
    slot = a.slot or f"ours-{a.brief}-selftest"
    openapi = json.loads((HERE / "tools" / "openapi.json").read_text())
    spec_path = pathlib.Path(a.spec)
    if not spec_path.is_absolute() and not spec_path.exists(): spec_path = HERE / spec_path
    spec = json.loads(spec_path.read_text())
    cases = [dict(c, seed=dict(server.seed_from_case(c), reasons=BRIEF_A_REASONS))
             for c in json.loads((HERE / "public-test-set.json").read_text()) if c["brief"] == a.brief]
    styles = [a.style] if a.style else None
    t0, runs, agent, proc = time.time(), [], None, None
    srv = server.serve(0, openapi)
    try:
        print("bank port", srv.server_address[1], flush=True)
        url, proc = tunnel.start(srv.server_address[1])
        print("tunnel", url, flush=True)
        agent = freya.save_workflow(slot, comp.compile(spec, openapi, url + "/v1"), state_file=str(HERE / ".freya-agents.json"))["agent_id"]
        print("agent", agent, "slot", slot, flush=True)
        for rep in range(a.reps):
            res = selftest.run(agent, cases, url, styles=styles)
            runs.append(res)
            print(f"rep {rep + 1}: {res['passed']}/{res['total']}", flush=True)
            for r in res["rows"]:
                why = "; ".join(r["failed"])
                warn = ("  [warn: " + "; ".join(r["warnings"]) + "]") if r["warnings"] else ""
                print(f"{'PASS' if r['pass'] else 'FAIL'} {r['id']} {r['title']}" + (f" -- {why}" if why else "") + warn, flush=True)
    finally:
        if proc: tunnel.stop(proc)
        srv.shutdown()
    passed, total = sum(r["passed"] for r in runs), sum(r["total"] for r in runs)
    print(f"TOTAL {passed}/{total} (brief {a.brief}, style {a.style}, reps {a.reps})")
    out = HERE / "results" / f"pub-ours-{a.brief}-{a.style or 'none'}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"brief": a.brief, "style": a.style, "reps": a.reps, "spec": a.spec, "slot": slot, "agent_id": agent,
                               "passed": passed, "total": total, "seconds": round(time.time() - t0), "runs": runs}, indent=1))
    print("wrote", out)


if __name__ == "__main__": main()
