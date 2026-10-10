"""Bench: build every corpus brief with one builder, then run its cases on OUR bank (Task 9).

    uv run python bench/run.py <ours|fallback> [names...] [--no-sim] [--style S] [--reps 1] [--repo-root PATH]

Briefs: every folder in bench/ (names starting with "_" or "." are not briefs). Brief files are the folder's files
except openapi.json, cases.json, notes.md, verify.py and bank_patch.md. plan2 is extraction-only: built, never
simulated (its notes: our bank refuses a second promise per product). A folder without cases.json is built only.

Per brief, sequentially (one simulation slot):
  build    ours:     python -m builder.main <brief files> --openapi <brief openapi> --slot bench-<name> --no-selftest
                     --out-dir results/bench/<name>
           fallback: python fallback/agent_builder.py <brief files>; its workflow is the newest out/*.workflow.json
                     written during the build (copied to results/bench/<name>/).
           Build seconds, exit code and the log tail are recorded; the full log goes to results/bench/<name>/build.log.
  extract  (ours) validation errors and conflicts of <slot>.spec.json, plus two cheap generic checks against the
           hand-checked cases: the spec's (outcome, reason) pairs vs the cases' close_call reason tables, and the
           spec's identity fields vs the cases' identity keys.
  sim      (unless --no-sim) ours: compile the spec with base URL <tunnel>/v1; fallback: swap the workflow's
           servers[0] URL for the tunnel. Save to ONE reused slot `bench-sim`, point our bank at the brief's own
           openapi, and run builder.selftest on the brief's cases (each has `seed`) for --reps reps.

One bank for the whole run; the tunnel starts at the first simulation, is restarted if it died, and is stopped by pid
at the end. A failing brief is recorded and the bench goes on. .freya-agents.json is copied in from the main checkout
before each brief and back after it. Results: results/bench-<builder>-<time>.json and .md, rewritten after every brief.
Env: env.sh of the main checkout is always loaded with setdefault (never printed). SIGTERM/SIGHUP exit 143 through
the same cleanup; Ctrl-C kills the running build's process group and records an `interrupted` row.
"""
import argparse, datetime, json, os, pathlib, shutil, signal, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent.parent
BENCH = HERE / "bench"
NOT_BRIEF = {"openapi.json", "cases.json", "notes.md", "verify.py", "bank_patch.md"}
NO_SIM = {"plan2": "extraction-only (notes.md: the bank refuses a second promise per product)"}
SIM_SLOT = "bench-sim"
BUILD_TIMEOUT_S = {"ours": 35 * 60, "fallback": 60 * 60}


# ------------------------------------------------------------------------------------------------- pure parts

def brief_files(d):
    d = pathlib.Path(d)
    return sorted(p for p in d.iterdir() if p.is_file() and p.name not in NOT_BRIEF and not p.name.startswith((".", "_")))


def discover(bench_dir=BENCH, names=None):
    """The bench briefs, in name order: [{name, dir, files, openapi, cases, sim_skip}]. Unknown names raise."""
    bench_dir = pathlib.Path(bench_dir)
    all_dirs = {p.name: p for p in bench_dir.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
                and (p / "openapi.json").exists()}
    if names:
        missing = [n for n in names if n not in all_dirs]
        if missing: raise SystemExit("unknown bench brief(s): %s (have: %s)" % (", ".join(missing), ", ".join(sorted(all_dirs))))
    out = []
    for name in (names or sorted(all_dirs)):
        d = all_dirs[name]
        cases = d / "cases.json"
        skip = NO_SIM.get(name) or (None if cases.exists() else "no cases.json")
        out.append({"name": name, "dir": d, "files": brief_files(d), "openapi": d / "openapi.json",
                    "cases": cases if cases.exists() else None, "sim_skip": skip})
    return out


def build_cmd(builder, brief, out_dir, python=sys.executable, repo_root=HERE):
    files = [str(f) for f in brief["files"]]
    if builder == "ours":
        return [python, "-m", "builder.main", *files, "--openapi", str(brief["openapi"]), "--slot", "bench-" + brief["name"],
                "--no-selftest", "--out-dir", str(out_dir)]
    return [python, str(pathlib.Path(repo_root) / "fallback" / "agent_builder.py"), *files]


def outcome_pairs(spec):
    return {(r.get("outcome"), r.get("reason")) for r in (spec or {}).get("outcomes") or [] if isinstance(r, dict)}


def case_pairs(cases):
    """(outcome, reason) pairs from the cases' close_call tables (seed.reasons, else reasons); None when none has one."""
    pairs, seen = set(), False
    for c in cases:
        t = (c.get("seed") or {}).get("reasons") or c.get("reasons")
        if not isinstance(t, dict): continue
        seen = True
        for o, rs in t.items():
            if rs: pairs |= {(o, r) for r in rs}
            else: pairs.add((o, None))
    return pairs if seen else None


def extraction_checks(spec, cases):
    """Cheap generic comparisons of the spec with the hand-checked cases. Each: {ok, missing, extra} or skipped."""
    out = {}
    want = case_pairs(cases or [])
    if want is not None:
        have = outcome_pairs(spec)
        # Outcomes the cases' table lists without any reason are matched on the outcome alone.
        miss = sorted(([o, r] for o, r in want - have if not (r is None and any(h[0] == o for h in have))), key=str)
        extra = sorted(([o, r] for o, r in have - want if not ((o, None) in want and r is None)), key=str)
        out["outcomes"] = {"ok": not miss and not extra, "missing": miss, "extra": extra}
    keys = {k for c in (cases or []) for k in (c.get("identity") or {})}
    if keys:
        fields = {q.get("field") for q in ((spec or {}).get("identity") or {}).get("questions") or [] if isinstance(q, dict)}
        out["identity"] = {"ok": fields == keys, "missing": sorted(keys - fields), "extra": sorted(f for f in fields - keys if f)}
    return out


def summarize_sim(runs):
    """selftest.run results (one per rep) -> {passed, total, failures: [{id, rep, failed}]}."""
    fails = [{"id": r["id"], "rep": i + 1, "failed": [str(x)[:300] for x in (r.get("failed") or [])[:3]]}
             for i, res in enumerate(runs) for r in res.get("rows") or [] if not r.get("pass")]
    return {"passed": sum(r.get("passed", 0) for r in runs), "total": sum(r.get("total", 0) for r in runs), "failures": fails}


def _err(e):
    from builder.redact import scrub
    return scrub("%s: %s" % (type(e).__name__, e))[:600]


def run_brief(brief, builder, build_fn, sim_fn, no_sim=False, extract_fn=None):
    """One brief, every phase caught: a failure is recorded in `errors` and the next phase or brief still runs."""
    rec = {"name": brief["name"], "builder": builder, "build_s": None, "exit": None, "spec_errors": None,
           "conflicts": None, "checks": {}, "sim": None, "sim_skip": None, "errors": []}
    artifact = None
    try:
        b = build_fn(brief)
        rec.update(build_s=b.get("seconds"), exit=b.get("exit"), log=b.get("log"))
        if b.get("tail") and b.get("exit"): rec["build_tail"] = b["tail"]
        artifact = b.get("artifact")
    except Exception as e:
        rec["errors"].append({"phase": "build", "error": _err(e)})
    if extract_fn and artifact:
        try:
            rec.update(extract_fn(brief, artifact))
        except Exception as e:
            rec["errors"].append({"phase": "extract", "error": _err(e)})
    if no_sim: rec["sim_skip"] = "--no-sim"
    elif brief.get("sim_skip"): rec["sim_skip"] = brief["sim_skip"]
    elif not artifact: rec["sim_skip"] = "no build output"
    else:
        try:
            rec["sim"] = sim_fn(brief, artifact)
        except Exception as e:
            rec["errors"].append({"phase": "sim", "error": _err(e)})
    return rec


def _cell(x):
    return "-" if x is None else str(x).replace("|", "\\|").replace("\n", " ")


def render_md(meta, rows):
    lines = ["# Bench: %s" % meta["builder"], "",
             "Started %s, style %s, reps %s, sim %s, commit %s." % (meta["started"], meta.get("style") or "mixed/none",
                                                                   meta.get("reps"), "off" if meta.get("no_sim") else "on",
                                                                   meta.get("git_commit")), "",
             "| brief | build s | exit | spec errors | conflicts | checks | sim | failures |",
             "|---|---|---|---|---|---|---|---|"]
    tp = tt = 0
    for r in rows:
        checks = ", ".join("%s %s" % (k, "ok" if v.get("ok") else "MISMATCH") for k, v in (r.get("checks") or {}).items()) or None
        sim, fails = r.get("sim"), []
        if sim:
            tp, tt = tp + sim["passed"], tt + sim["total"]
            fails = ["%s%s: %s" % (f["id"], "" if meta.get("reps", 1) == 1 else " r%d" % f["rep"], "; ".join(f["failed"])[:160])
                     for f in sim["failures"]]
        simcell = "%d/%d" % (sim["passed"], sim["total"]) if sim else ("skipped: " + r["sim_skip"] if r.get("sim_skip") else None)
        fails += ["%s error: %s" % (e["phase"], e["error"][:160]) for e in r.get("errors") or []]
        lines.append("| %s |" % " | ".join(_cell(x) for x in (r["name"], r.get("build_s"), r.get("exit"), r.get("spec_errors"),
                                                              r.get("conflicts"), checks, simcell, "<br>".join(fails) or None)))
    lines += ["", "**Sim total: %d/%d**" % (tp, tt), ""]
    for r in rows:
        bad = {k: v for k, v in (r.get("checks") or {}).items() if not v.get("ok")}
        if bad or r.get("build_tail"):
            lines.append("## %s" % r["name"])
            for k, v in bad.items(): lines.append("- %s check: missing %s, extra %s" % (k, v["missing"], v["extra"]))
            if r.get("build_tail"): lines += ["- build log tail:", "", "```", r["build_tail"], "```"]
            lines.append("")
    return "\n".join(lines)


def clear_outputs(out_dir, name):
    """Delete the previous build's bench-<name>.spec.json and *.workflow.json in out_dir."""
    out_dir = pathlib.Path(out_dir)
    for f in [out_dir / ("bench-%s.spec.json" % name), *out_dir.glob("*.workflow.json")]:
        f.unlink(missing_ok=True)


def kill_group(p):
    try: os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError: pass


def interrupted_row(brief, builder, why):
    return {"name": brief["name"], "builder": builder, "build_s": None, "exit": None, "spec_errors": None, "conflicts": None,
            "checks": {}, "sim": None, "sim_skip": why, "interrupted": True, "errors": [{"phase": "run", "error": why}]}


def _exit_on_signal(signum, frame):
    sys.exit(143)                                     # SystemExit runs every `finally`: the tunnel and bank are stopped


def newest_workflow(out_dir, since):
    ws = [p for p in pathlib.Path(out_dir).glob("*.workflow.json") if p.stat().st_mtime >= since]
    return max(ws, key=lambda p: p.stat().st_mtime) if ws else None


# ------------------------------------------------------------------------------------------------- live parts

def main_checkout():
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=HERE,
                                capture_output=True, text=True, timeout=10).stdout.strip()
        return pathlib.Path(common).parent if common else HERE
    except Exception:
        return HERE


def load_env(env_file):
    """Source env.sh in bash and merge its variables into os.environ (setdefault: set values win). Nothing is printed."""
    if not pathlib.Path(env_file).exists(): return
    out = subprocess.run(["bash", "-c", 'set -a; source "$1" >/dev/null 2>&1; set +a; env -0', "_", str(env_file)],
                         capture_output=True, timeout=30).stdout
    for item in out.split(b"\0"):
        k, sep, v = item.decode(errors="replace").partition("=")
        if sep and k and k not in ("_", "SHLVL", "PWD", "OLDPWD"): os.environ.setdefault(k, v)


def sync_state(src, dst):
    src, dst = pathlib.Path(src), pathlib.Path(dst)
    if src.resolve() != dst.resolve() and src.exists(): shutil.copy2(src, dst)


def git_commit(root):
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return "unknown"


class Runner:
    def __init__(self, a, root, main_root):
        self.a, self.root, self.main_root = a, root, main_root
        self.state = root / ".freya-agents.json"
        self.srv = self.proc = self.url = None

    # -- build
    def build(self, brief):
        from builder.redact import scrub
        out_dir = HERE / "results" / "bench" / brief["name"]
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = build_cmd(self.a.builder, brief, out_dir, repo_root=self.root)
        log = out_dir / "build.log"
        clear_outputs(out_dir, brief["name"])         # a stale spec or workflow must never pass for this build's
        t0, wall0 = time.monotonic(), time.time()
        p = subprocess.Popen(cmd, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            text, _ = p.communicate(timeout=BUILD_TIMEOUT_S[self.a.builder])
            code = p.returncode
        except subprocess.TimeoutExpired:
            kill_group(p)                             # the build's own process group, by pid
            text, _ = p.communicate()
            code = "timeout"
        except BaseException:                         # Ctrl-C, SIGTERM (SystemExit): the build must not outlive us
            kill_group(p)
            raise
        text = scrub(text or "")
        log.write_text(text)
        res = {"seconds": round(time.monotonic() - t0, 1), "exit": code, "log": str(log.relative_to(HERE)),
               "tail": "\n".join(text.strip().splitlines()[-12:])}
        if code != 0:
            return res                                # no artifact: the sim is skipped, the exit code is recorded
        if self.a.builder == "ours":
            spec = out_dir / ("bench-%s.spec.json" % brief["name"])
            res["artifact"] = spec if spec.exists() else None
        else:
            wf = newest_workflow(self.root / "out", wall0)
            if wf:
                shutil.copy2(wf, out_dir / wf.name)
                res["artifact"] = out_dir / wf.name
        return res

    # -- extraction metrics (ours)
    def extract(self, brief, spec_path):
        from builder.validate import validate
        spec = json.loads(pathlib.Path(spec_path).read_text())
        openapi = json.loads(brief["openapi"].read_text())
        errs = validate(spec, openapi)
        conf = spec.get("conflicts") if isinstance(spec.get("conflicts"), list) else []
        cases = json.loads(brief["cases"].read_text()) if brief["cases"] else []
        return {"spec_errors": len(errs), "spec_error_list": [str(e)[:300] for e in errs[:10]], "conflicts": len(conf),
                "checks": extraction_checks(spec, cases)}

    # -- bank + tunnel
    def ensure_tunnel(self):
        from bank import server, tunnel
        if self.srv is None:
            self.srv = server.serve(0, json.loads((HERE / "tools" / "openapi.json").read_text()))
        if self.proc is None or self.proc.poll() is not None:
            if self.proc is not None: tunnel.stop(self.proc)
            self.url, self.proc = tunnel.start(self.srv.server_address[1])
            print("tunnel up", flush=True)
        return self.url

    def stop(self):
        from bank import tunnel
        if self.proc is not None:
            try: tunnel.stop(self.proc)
            except Exception: pass
            self.proc = None
        if self.srv is not None:
            try: self.srv.shutdown()
            except Exception: pass
            self.srv = None

    # -- sim
    def sim(self, brief, artifact):
        import freya
        from builder import compile as comp, selftest
        from fallback.selftest_cli import swap_base
        openapi = json.loads(brief["openapi"].read_text())
        url = self.ensure_tunnel()
        self.srv.bank.reset(openapi)                  # the same bank and server, now serving this brief's openapi
        if self.a.builder == "ours":
            wf = comp.compile(json.loads(pathlib.Path(artifact).read_text()), openapi, url + "/v1")
        else:
            wf = json.loads(swap_base(pathlib.Path(artifact).read_text(), openapi, url))
        agent = freya.save_workflow(SIM_SLOT, wf, state_file=str(self.state))["agent_id"]
        cases = [dict(c, scenario=c.get("scenario") or c.get("family")) for c in json.loads(brief["cases"].read_text())]
        styles = [self.a.style] if self.a.style else None
        runs = []
        for rep in range(self.a.reps):
            runs.append(selftest.run(agent, cases, url, styles=styles))
            print("  rep %d: %d/%d" % (rep + 1, runs[-1]["passed"], runs[-1]["total"]), flush=True)
        out = summarize_sim(runs)
        out["agent_id"], out["simulation_ids"] = agent, [r.get("simulation_id") for r in runs]
        return out


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="bench/run.py", description=__doc__.split("\n")[0])
    ap.add_argument("builder", choices=["ours", "fallback"])
    ap.add_argument("names", nargs="*", help="bench folders (default: all)")
    ap.add_argument("--no-sim", action="store_true")
    ap.add_argument("--style", default=None, help="persona style for every case (tools/run_cases.STYLES); default none")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--repo-root", default=str(HERE), help="worktree whose builder/ and fallback/ are under test")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    for sig in (signal.SIGTERM, signal.SIGHUP): signal.signal(sig, _exit_on_signal)
    root, main_root = pathlib.Path(a.repo_root).resolve(), main_checkout()
    load_env(main_root / "env.sh")
    for p in (HERE / "tools", HERE, root / "tools", root):   # later insert(0) wins: --repo-root's builder is imported
        if str(p) in sys.path: sys.path.remove(str(p))
        sys.path.insert(0, str(p))
    from builder.redact import scrub
    briefs = discover(BENCH, a.names or None)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_json = HERE / "results" / ("bench-%s-%s.json" % (a.builder, stamp))
    out_json.parent.mkdir(parents=True, exist_ok=True)
    meta = {"builder": a.builder, "started": stamp, "style": a.style, "reps": a.reps, "no_sim": a.no_sim,
            "repo_root": str(root), "git_commit": git_commit(root), "briefs": [b["name"] for b in briefs]}
    run, rows = Runner(a, root, main_root), []

    def write():
        out_json.write_text(scrub(json.dumps({**meta, "rows": rows}, indent=1, default=str)))
        out_json.with_suffix(".md").write_text(scrub(render_md(meta, rows)))

    try:
        for b in briefs:
            print("== %s (%s)" % (b["name"], a.builder), flush=True)
            sync_state(main_root / ".freya-agents.json", run.state)
            try:
                rec = run_brief(b, a.builder, run.build, run.sim, a.no_sim, run.extract if a.builder == "ours" else None)
            except (KeyboardInterrupt, SystemExit) as e:
                rows.append(interrupted_row(b, a.builder, "interrupted (%s)" % type(e).__name__))
                raise
            finally:
                sync_state(run.state, main_root / ".freya-agents.json")
            rows.append(rec)
            write()
            sim = rec.get("sim")
            print(scrub("   build %ss exit %s, spec errors %s, sim %s%s" % (
                rec["build_s"], rec["exit"], rec["spec_errors"], "%d/%d" % (sim["passed"], sim["total"]) if sim else rec["sim_skip"],
                "".join(" | %s: %s" % (e["phase"], e["error"][:200]) for e in rec["errors"]))), flush=True)
    finally:
        run.stop()
        write()
    print("wrote", out_json, "and", out_json.with_suffix(".md").name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
