"""The builder: brief files -> policy spec -> self-tested Freya agent -> exported workflow, in one unattended run.

    set -a; source env.sh; set +a
    uv run python -m builder.main <brief files or dirs...> [--slot NAME] [--openapi PATH] [--no-selftest]
                                  [--budget-min 27] [--now ISO] [--out-dir submission/]

Steps (design spec §4 and the Task 8 rulings):
  t=0      trace start; our mock bank starts and the Cloudflare tunnel comes up in a background thread,
           in parallel with extraction (nothing is generated until a spec exists).
  extract  builder.extract (dual extraction, settle, oracle), at most 6 min.
  round 1  compile with base URL <tunnel>/v1 -> save_workflow(<slot>-selftest) -> cases.generate(n=10, or 11 when
           multi-product notes are enabled; seed 0) -> selftest.run. All passed: no round 2 (build time is a tie-break).
  fix      otherwise, and with > 12 min left: one short LLM session edits the spec, each change with a brief quote
           (builder.fix); re-validate, recompile, round 2 on cases generated the same way. The spec with the best pass
           count is kept (a tie keeps the fixed spec: its changes are quote-backed).
  final    compile with the openapi servers[0] URL; assert the workflow JSON has no trycloudflare URL and does have the
           servers[0] host; save_workflow(<slot>); export to <out-dir>/<slot>.workflow.json; write <slot>.spec.json.

Hard stop: at --budget-min minus FINAL_RESERVE_S, whatever is running is abandoned and the best spec so far goes to
the final step. Every phase is wrapped: once a spec exists, a crash anywhere still ends in a saved, exported agent.
No spec at all: exit 2 with a `no_spec` trace event. No openapi: exit 2 with a `no_openapi` trace event (the openapi is
--openapi, else openapi.json next to the briefs, else the single OpenAPI .json among the briefs; never a default).

Trace: traces/<UTC time>-<slot>.jsonl (every line through redact.scrub) plus a .md summary of the same events.
Exit code: 0 when the workflow was exported, 1 when a spec existed but the final step failed, 2 with no spec or no openapi.
"""
import argparse
import datetime
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import freya  # noqa: E402
from bank import server, tunnel  # noqa: E402
from builder import cases, fix, sdk, selftest  # noqa: E402
from builder import compile as comp  # noqa: E402
from builder import extract as ex  # noqa: E402
from builder.lint import lint, reconcile_banned  # noqa: E402
from builder.redact import scrub  # noqa: E402
from builder.trace import Trace  # noqa: E402
from builder.validate import validate  # noqa: E402

STATE_FILE = ROOT / ".freya-agents.json"
SIMS_FILE = ROOT / ".freya-sims.json"     # simulations this builder started (only these are ever cancelled)
FINISHED = ("completed", "failed", "paused", "cancelled", "canceled")
EXTRACT_MAX_S = 360          # extraction budget (design §4: <= 6 min)
EXTRACT_RETRY_MIN_S = 10 * 60   # no spec from extraction: retry once only with more than this left (after the reserve)
FINAL_RESERVE_S = 150        # kept back for the final compile, save and export
ROUND2_MIN_S = 12 * 60       # fix round + round 2 only with more than this left (after the reserve)
TUNNEL_WAIT_S = 120          # once the spec exists, wait at most this long for the tunnel
TUNNEL_TRIES = 3             # tunnel.start attempts (in the background, during extraction)
TUNNEL_BACKOFF_S = 3         # sleep before attempt k+1: k * this
TUNNEL_RETRY_MIN_S = 4 * 60  # no further attempt with less than this left for tests
BUSY_MAX_S = 180             # a busy workspace (another team simulation) longer than this skips the self-test
FREYA_CALL_S = 180           # one save_workflow / export_workflow call
STYLES = [None, "talkative", "pieces", "corrects", "asks_back", "in_words"]   # tools/run_cases.STYLES, mixed
SEED = 0


class BudgetStop(Exception):
    """The time budget ran out while a phase was running."""


class WorkspaceBusy(Exception):
    """Another simulation kept the Freya workspace busy for more than BUSY_MAX_S."""


class Bg:
    """Run fn() in a daemon thread. result(timeout) raises BudgetStop when it is not done in time; the thread is then
    abandoned (a daemon thread never holds up the exit) and on_late(value) cleans up if it finishes later.

    Freya calls in the thread are bound to it (freya.bind_call): they never start a retry wait that would end after
    deadline_s, and once result() has timed out they raise freya.CallAbandoned instead of making another request,
    waiting, or writing the state file."""

    def __init__(self, fn, on_late=None, deadline_s=None):
        self.value, self.error, self.abandoned = None, None, False
        self._on_late, self._done, self._lock = on_late, threading.Event(), threading.Lock()
        self._stop = threading.Event()
        self._deadline = None if deadline_s is None else time.monotonic() + deadline_s
        threading.Thread(target=self._run, args=(fn,), daemon=True).start()

    def _run(self, fn):
        freya.bind_call(self._stop, self._deadline)
        try:
            self.value = fn()
        except BaseException as e:  # noqa: BLE001 - re-raised in result()
            self.error = e
        with self._lock:
            self._done.set()
            late = self.abandoned
        if late and self._on_late and self.value is not None:
            try:
                self._on_late(self.value)
            except Exception:
                pass

    def result(self, timeout):
        if not self._done.wait(max(0.0, timeout)):
            self._stop.set()
            raise BudgetStop()
        if self.error is not None:
            raise self.error
        return self.value

    def abandon(self):
        """The value if done (the caller cleans it up), else None (on_late will)."""
        with self._lock:
            self.abandoned = True
            return self.value if self._done.is_set() else None


def bounded(fn, timeout):
    return Bg(fn, deadline_s=timeout).result(timeout)


def run_fix(*a, **kw):
    return fix.fix(*a, **kw)


# ---------------------------------------------------------------------------------------------------------- inputs

class OpenapiNotFound(Exception):
    """No single OpenAPI document was found next to the briefs and --openapi was not given."""


def resolve_openapi(briefs, override):
    """--openapi; else the openapi.json next to the brief files; else the one OpenAPI document (a .json with
    `openapi` and `paths`) among the brief arguments and the brief folders. None or several: OpenapiNotFound.
    Never a silent default: another brief's API would build a wrong agent."""
    if override:
        return Path(override)
    dirs = list(dict.fromkeys((p if p.is_dir() else p.parent).resolve() for p in map(Path, briefs)))
    for d in dirs:
        if (d / "openapi.json").is_file():
            return d / "openapi.json"
    cands = [Path(b).resolve() for b in briefs if sdk.is_openapi_file(b)]
    cands += [x.resolve() for d in dirs if d.is_dir() for x in sorted(d.glob("*.json")) if sdk.is_openapi_file(x)]
    cands = list(dict.fromkeys(cands))
    if len(cands) == 1:
        return cands[0]
    if not cands:
        raise OpenapiNotFound("no OpenAPI document (a .json with `openapi` and `paths`) next to the briefs in %s; "
                              "pass --openapi PATH" % ", ".join(map(str, dirs)))
    raise OpenapiNotFound("several OpenAPI documents next to the briefs (%s); pass --openapi PATH"
                          % ", ".join(map(str, cands)))


def slug_for(briefs):
    """Default slot: the first brief's folder and name, plus a short hash of the resolved brief set, so x/brief.md and
    y/brief.md never share a slot (or an export file). The same brief set always gets the same slug."""
    p = Path(str(briefs[0]).rstrip("/"))
    name = p.name if p.is_dir() else "%s-%s" % (p.resolve().parent.name, p.stem)
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-") or "brief"
    key = "\n".join(sorted(str(Path(str(b).rstrip("/")).resolve()) for b in briefs))
    return "%s-%s" % (base, hashlib.sha1(key.encode()).hexdigest()[:6])


def resolve_now(cli, spec, briefs):
    """--now, else the first spec example's `today` at 11:00, else the first date-time (or date, at 11:00) in the first
    brief file, else today at 11:00."""
    if cli:
        return cli
    for e in (spec or {}).get("examples") or []:
        if isinstance(e, dict) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(e.get("today") or "")):
            return e["today"] + "T11:00"
    files = sdk.expand_briefs(briefs[:1])
    text = files[0].read_text(errors="replace") if files and files[0].exists() else ""
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})", text)
    if m:
        return "%sT%s" % (m[1], m[2])
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if m:
        return m[1] + "T11:00"
    return datetime.date.today().isoformat() + "T11:00"


def git_commit():
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True,
                               text=True, timeout=10).stdout.strip()
        return sha + ("-dirty" if dirty else "") if sha else "unknown"
    except Exception:
        return "unknown"


_sims_lock = threading.Lock()


def own_sims():
    try:
        return {x["id"] for x in json.loads(Path(SIMS_FILE).read_text()) if isinstance(x, dict) and x.get("id")}
    except (OSError, ValueError, TypeError, KeyError):
        return set()


def record_sim(sid, slot):
    """Append a simulation id this builder started to SIMS_FILE (gitignored), so a later run may cancel it."""
    with _sims_lock:
        try:
            rows = json.loads(Path(SIMS_FILE).read_text())
            rows = rows if isinstance(rows, list) else []
        except (OSError, ValueError):
            rows = []
        rows.append({"id": sid, "slot": slot, "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")})
        try:
            Path(SIMS_FILE).write_text(json.dumps(rows[-200:], indent=1))
        except OSError:
            pass


def _expected_key(c):
    return json.dumps(c.get("expected"), sort_keys=True, default=str)


def round2_cases(cs1, cs2):
    """Round 2 runs every round-1 case again (same seed, same customers) plus the round-2 cases whose expected result
    differs from round 1's case for the same scenario (new ids SELF-R2-nn). Returns (cases to run, ids of round-1
    cases whose expected result is identical in both rounds -- the only ones compared, extra cases)."""
    by1 = {}
    for c in cs1:
        by1.setdefault(c.get("scenario"), c)
    by2 = {}
    for c in cs2:
        by2.setdefault(c.get("scenario"), c)
    comparable = {c["id"] for c in cs1
                  if c.get("scenario") in by2 and _expected_key(by2[c.get("scenario")]) == _expected_key(c)}
    extra = []
    for c in cs2:
        c1 = by1.get(c.get("scenario"))
        if c1 is None or _expected_key(c1) != _expected_key(c):
            extra.append(dict(c, id="SELF-R2-%02d" % (len(extra) + 1)))
    return list(cs1) + extra, comparable, extra


def _notes_on(spec):
    return bool((((spec or {}).get("multi_product") or {}).get("notes") or {}).get("enabled"))


def _err(e):
    return scrub("%s: %s" % (type(e).__name__, e))[:600]


# ---------------------------------------------------------------------------------------------------------- build

class Build:
    def __init__(self, a):
        self.a = a
        self.t0 = time.monotonic()
        self.deadline = self.t0 + a.budget_min * 60
        self.briefs = [str(b) for b in a.briefs]
        self.slot = a.slot or slug_for(self.briefs)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.trace = Trace(Path(a.trace_dir) / ("%s-%s.jsonl" % (stamp, self.slot)))
        self.out_dir = Path(a.out_dir)
        self.extracted = None        # the spec from extraction
        self.best = None             # (spec, passed, label)
        self.rounds = []
        self.final = {}
        self.srv = self.tun = None
        self._bank_stopped = False   # set once the build ends: a background tunnel retry then gives up

    # -- helpers
    def left(self):
        return self.deadline - time.monotonic()

    def test_left(self):
        return self.left() - FINAL_RESERVE_S

    def log(self, msg):
        print(scrub("[%5.0fs] %s" % (time.monotonic() - self.t0, msg)), file=sys.stderr, flush=True)

    def ev(self, kind, **f):
        self.trace.event(kind, **f)

    def failed(self, phase, e):
        self.ev("phase_failed", phase=phase, error=_err(e))
        self.log("%s failed: %s" % (phase, _err(e)))

    # -- run
    def run(self):
        rc = 1
        try:
            rc = self._run()
        except BaseException as e:  # noqa: BLE001 - the trace must still end
            self.failed("main", e)
            if self.best and not self.final.get("exported"):
                rc = self._final_with_fallback()
            if isinstance(e, KeyboardInterrupt):
                rc = 130
        finally:
            self._stop_bank()
            self.ev("end", ok=bool(self.final.get("exported")), exit_code=rc,
                    duration_s=round(time.monotonic() - self.t0, 1), slot=self.slot,
                    workflow_id=self.final.get("workflow_id"), agent_id=self.final.get("agent_id"),
                    export=self.final.get("export"), spec_label=self.best[2] if self.best else None,
                    selftest=[{"round": r["round"], "passed": r["passed"], "total": r["total"]} for r in self.rounds])
            md = self.trace.write_markdown()
            self.trace.close()
            self.log("trace %s (%s)" % (self.trace.path, md.name))
        return rc

    def _run(self):
        a = self.a
        oa_path, oa_error = None, None
        try:
            oa_path = resolve_openapi(self.briefs, a.openapi)
            openapi = json.loads(Path(oa_path).read_text())
            if not isinstance(openapi, dict) or "paths" not in openapi:
                raise OpenapiNotFound("%s is not an OpenAPI document (no `paths`)" % oa_path)
        except (OpenapiNotFound, OSError, ValueError) as e:
            oa_error = e
        self.ev("start", briefs=self.briefs, slot=self.slot, git_commit=git_commit(), model=sdk.model_name(),
                openapi=str(oa_path) if oa_path else None, budget_min=a.budget_min, selftest=not a.no_selftest,
                now=a.now, out_dir=str(self.out_dir), argv=sys.argv[1:])
        if oa_error is not None:
            self.ev("no_openapi", error=_err(oa_error))
            self.log("no openapi: %s" % _err(oa_error))
            return 2
        self.openapi = openapi
        self.log("build %s: %s" % (self.slot, ", ".join(self.briefs)))
        if not a.no_selftest:
            self._start_bank()

        spec = self._extract()
        if spec is None:
            return 2
        self.extracted = spec
        self.best = (spec, None, "extract")

        if a.no_selftest:
            self.ev("selftest_skipped", why="--no-selftest")
        elif self.tun is None:
            self.ev("selftest_skipped", why="bank or tunnel did not start")
        else:
            try:
                self._selftest(spec)
            except BudgetStop:
                self.ev("budget_stop", phase="selftest", seconds_left=round(self.left(), 1))
                self.log("budget reached: stopping the self-test, keeping the best spec")
            except WorkspaceBusy as e:
                self.ev("selftest_skipped", why="workspace busy: %s" % e)
            except Exception as e:
                self.failed("selftest", e)
        return self._final_with_fallback()

    # -- bank + tunnel
    def _start_bank(self):
        try:
            self.srv = server.serve(0, self.openapi)
            port = self.srv.server_address[1]
            self.tun = Bg(lambda: self._tunnel(port), on_late=lambda v: tunnel.stop(v[1]))
            self.ev("bank_started", port=port)
        except Exception as e:
            self.failed("bank", e)

    def _tunnel(self, port):
        """tunnel.start, up to TUNNEL_TRIES attempts with a short backoff, while the self-test still has time."""
        for k in range(1, TUNNEL_TRIES + 1):
            try:
                return tunnel.start(port)
            except Exception as e:
                if self._bank_stopped:
                    raise
                last = k == TUNNEL_TRIES or self.test_left() < TUNNEL_RETRY_MIN_S
                self.ev("tunnel_failed", attempt=k, error=_err(e), retry=not last)
                if last:
                    raise
                time.sleep(TUNNEL_BACKOFF_S * k)

    def _stop_bank(self):
        self._bank_stopped = True
        if self.tun is not None:
            v = self.tun.abandon()
            if v is not None:
                try:
                    tunnel.stop(v[1])
                except Exception:
                    pass
            self.tun = None
        if self.srv is not None:
            try:
                self.srv.shutdown()
            except Exception:
                pass
            self.srv = None

    # -- extract
    def _extract_once(self, **kw):
        deadline_s = max(60, min(EXTRACT_MAX_S, int(self.left() - FINAL_RESERVE_S - 30)))
        try:
            return bounded(lambda: ex.extract(self.briefs, self.openapi, self.trace, deadline_s=deadline_s, **kw),
                           deadline_s + 90)
        except BudgetStop:
            return None, ["extraction did not return within %d s" % (deadline_s + 90)], {}
        except Exception as e:
            return None, [_err(e)], {}

    def _reconcile(self, spec, stage):
        """Drop banned_before_verify terms the spec's own required pre-verification sentences contain; traced, never silent."""
        try:
            new, removed = reconcile_banned(spec)
        except Exception as e:
            self.ev("banned_reconciled", stage=stage, removed=[], error=_err(e))
            return spec
        if removed:
            self.ev("banned_reconciled", stage=stage,
                    removed=[{k: scrub(str(v))[:200] for k, v in r.items()} for r in removed])
            self.log("banned_before_verify: removed %s (a required pre-verification sentence says it)" %
                     ", ".join(repr(r["term"]) for r in removed))
        return new

    def _extract(self):
        spec, errs, info = self._extract_once()
        if spec is None and self.left() - FINAL_RESERVE_S > EXTRACT_RETRY_MIN_S:
            # one more, lighter attempt (single extraction, no oracle) while the budget still allows a build after it
            self.ev("extract_retry", errors=[scrub(str(x))[:400] for x in errs[:5]], seconds_left=round(self.left(), 1))
            self.log("extraction produced no spec; retrying once (single extraction, no oracle)")
            spec, errs2, info = self._extract_once(dual=False, use_oracle=False)
            errs = list(errs) + list(errs2)
        if spec is None:
            self.ev("no_spec", errors=[scrub(str(x))[:400] for x in errs[:10]])
            self.log("extraction produced no spec: %s" % scrub(str(errs[:3])))
            return None
        self.ev("extracted", seconds=info.get("seconds"), cost_usd=info.get("cost_usd"), errors=len(errs),
                base=info.get("base"), run_dir=info.get("run_dir"))
        try:
            verrs = validate(spec, self.openapi)
        except Exception as e:
            verrs = ["validate crashed: " + _err(e)]
        self.ev("spec_validated", stage="extract", errors=len(verrs), first=[scrub(x)[:300] for x in verrs[:5]])
        spec = self._reconcile(spec, "extract")
        conf = spec.get("conflicts") if isinstance(spec, dict) else None
        conf = conf if isinstance(conf, list) else []
        self.ev("conflicts", count=len(conf), items=[{"field": c.get("field"), "kept": str(c.get("kept"))[:300]}
                                                     for c in conf[:25] if isinstance(c, dict)])
        self.log("spec ready: %d validation errors, %d conflicts" % (len(verrs), len(conf)))
        return spec

    # -- self-test
    def _compile_lint(self, spec, base_url, stage):
        wf = comp.compile(spec, self.openapi, base_url)
        try:
            problems = lint(wf, spec)
        except Exception as e:
            problems = ["lint crashed: " + _err(e)]
        errs = [p for p in problems if not p.startswith("warning:")]
        self.ev("lint", stage=stage, errors=len(errs), warnings=len(problems) - len(errs),
                first=[scrub(p)[:300] for p in errs[:5]])
        return wf

    def _save(self, slot, wf):
        return bounded(lambda: freya.save_workflow(slot, wf, state_file=str(STATE_FILE)),
                       min(FREYA_CALL_S, max(5.0, self.left())))

    def _cancel(self, sid, why, **f):
        """Cancel one simulation this builder started; traced as orphan_cancelled (or phase_failed)."""
        try:
            r = bounded(lambda: freya.cancel_simulation(sid), 30) or {}
            self.ev("orphan_cancelled", id=sid, why=why, success=r.get("success"),
                    cancelled_jobs=r.get("cancelledJobs"), cancelled_iterations=r.get("cancelledIterations"), **f)
        except Exception as e:
            self.failed("cancel %s" % sid, e)

    def _cancel_own_running(self):
        """Busy workspace: cancel running simulations whose id is in SIMS_FILE (ours); trace the others, never cancel them."""
        try:
            sims = bounded(freya.list_simulations, 30) or []
        except Exception as e:
            self.failed("list simulations", e)
            return
        ours = own_sims()
        for s in sims:
            if not isinstance(s, dict) or str(s.get("status")) in FINISHED:
                continue
            if s.get("id") in ours:
                self._cancel(s["id"], "ours, still running at start-up")
            else:
                self.ev("foreign_simulation_running", id=s.get("id"), status=s.get("status"), action="not cancelled")

    def _round(self, k, spec, cs, url):
        wf = self._compile_lint(spec, url + "/v1", "selftest%d" % k)
        saved = self._save(self.slot + "-selftest", wf)
        self.ev("saved", slot=self.slot + "-selftest", workflow_id=saved["workflow_id"], agent_id=saved["agent_id"],
                url_kind="tunnel", round=k)
        self.log("round %d: %d cases on agent %s" % (k, len(cs), saved["agent_id"]))
        t = time.monotonic()
        sim = {"id": None, "abandoned": False}
        lock = threading.Lock()

        def on_created(sid):
            with lock:
                sim["id"] = sid
                late = sim["abandoned"]
            record_sim(sid, self.slot)
            self.ev("simulation_started", round=k, simulation_id=sid, agent_id=saved["agent_id"], cases=len(cs))
            if late:
                self.ev("orphan_simulation", id=sid, round=k, late=True)
                self._cancel(sid, "orphan", round=k)

        def on_busy(waited):
            if waited == 0:
                self.ev("workspace_busy", round=k)
                self.log("workspace busy: another simulation is running")
                self._cancel_own_running()
            if waited >= BUSY_MAX_S:
                raise WorkspaceBusy("workspace busy for %d s" % waited)
        try:
            res = bounded(lambda: selftest.run(saved["agent_id"], cs, url, styles=STYLES, on_created=on_created,
                                               on_busy=on_busy), self.test_left())
        except BudgetStop:
            with lock:
                sim["abandoned"] = True
                sid = sim["id"]
            if sid:
                self.ev("orphan_simulation", id=sid, round=k)
                self._cancel(sid, "orphan", round=k)
            raise
        rows = res.get("rows") or []
        failing = [r["id"] for r in rows if not r.get("pass")]
        self.ev("selftest_round", round=k, passed=res["passed"], total=res["total"], failing=failing,
                failures=[{"id": r["id"], "scenario": r.get("scenario"), "style": r.get("style"),
                           "failed": [scrub(str(x))[:300] for x in (r.get("failed") or [])[:3]]}
                          for r in rows if not r.get("pass")],
                warnings=sum(len(r.get("warnings") or []) for r in rows), agent_id=saved["agent_id"],
                simulation_id=res.get("simulation_id"), status=res.get("status"),
                seconds=round(time.monotonic() - t, 1))
        self.rounds.append({"round": k, "passed": res["passed"], "total": res["total"]})
        res["passed_ids"] = {r["id"] for r in rows if r.get("pass")}
        self.log("round %d: %d/%d passed" % (k, res["passed"], res["total"]))
        return res

    def _cases(self, spec, now):
        n = 11 if _notes_on(spec) else 10
        cs = cases.generate(spec, self.openapi, now, n=n, seed=SEED)
        self.ev("cases", n=n, seed=SEED, now=now, ids=[c["id"] + ":" + str(c.get("scenario")) for c in cs],
                skipped=list(cs.skipped), unpicked=list(cs.unpicked),
                errors={k: scrub(str(v))[:300] for k, v in (getattr(cs, "errors", None) or {}).items()})
        return cs

    def _selftest(self, spec):
        url, _ = self.tun.result(min(TUNNEL_WAIT_S, self.test_left()))
        self.ev("tunnel", url_kind="tunnel")   # never the word trycloudflare: the RUNBOOK secret scan looks for tunnel URLs
        now = resolve_now(self.a.now, spec, self.briefs)
        cs = self._cases(spec, now)
        res1 = self._round(1, spec, cs, url)
        self.best = (spec, res1["passed"], "extract")
        if res1["passed"] >= res1["total"]:
            self.ev("round2_skipped", why="round 1 passed every case")
            return
        if self.test_left() < ROUND2_MIN_S:
            self.ev("round2_skipped", why="%.0f s left for tests, below %d s" % (self.test_left(), ROUND2_MIN_S))
            return
        try:
            budget = min(fix.FIX_S, max(60, self.test_left() - 8 * 60))
            new, info = bounded(lambda: run_fix(spec, res1, cs, self.briefs, self.openapi, self.trace,
                                                get_call=freya.get_call, budget_s=budget), budget + 30)
        except BudgetStop:
            self.ev("fix_round", round=1, changes=[], discarded=[], error="timed out")
            return
        except Exception as e:
            self.failed("fix", e)
            return
        self.ev("fix_round", round=1, changes=info.get("changes") or [], discarded=info.get("discarded") or [],
                warnings=info.get("warnings") or [], sandbox=info.get("sandbox") or [],
                errors=info.get("errors"), kept_original=info.get("kept_original", False),
                cost_usd=info.get("cost_usd"), seconds=info.get("wall_s"))
        if not info.get("changes"):
            self.ev("round2_skipped", why="the fix round changed nothing")
            return
        try:   # M10: a validate crash must not drop round 2
            verrs = validate(new, self.openapi)
        except Exception as e:
            verrs = ["validate crashed: " + _err(e)]
        self.ev("spec_validated", stage="fix1", errors=len(verrs), first=[scrub(x)[:300] for x in verrs[:5]])
        new = self._reconcile(new, "fix1")
        cs2 = self._cases(new, now)
        run2, comparable, extra = round2_cases(cs, cs2)
        self.ev("round2_plan", rerun=len(cs), extra=[c["id"] + ":" + str(c.get("scenario")) for c in extra],
                comparable=sorted(comparable))
        res2 = self._round(2, new, run2, url)
        p1 = len(comparable & res1["passed_ids"])
        p2 = len(comparable & res2["passed_ids"])
        if p2 > p1:
            self.best = (new, res2["passed"], "fix1")
        self.ev("best_spec", label=self.best[2], passed=self.best[1], comparable=len(comparable),
                round1_passed=p1, round2_passed=p2, rule="round 2 must pass more comparable cases; a tie keeps round 1")

    # -- final
    def _lint_errors(self, spec):
        try:
            return [p for p in lint(comp.compile(spec, self.openapi), spec) if not p.startswith("warning:")]
        except Exception as e:
            return ["compile or lint crashed: " + _err(e)]

    def _final_with_fallback(self):
        if self.extracted is not None and self.best[0] is not self.extracted:
            best_errs = self._lint_errors(self.best[0])
            if best_errs and not self._lint_errors(self.extracted):
                self.ev("final_spec_switch", from_label=self.best[2], to_label="extract",
                        why="the best spec has lint errors and the extracted spec has none",
                        errors=[scrub(x)[:300] for x in best_errs[:5]])
                self.best = (self.extracted, None, "extract (lint)")
        try:
            return self._final(self.best[0], self.best[2])
        except Exception as e:
            self.failed("final", e)
        if self.extracted is not None and self.best[0] is not self.extracted:
            try:
                self.best = (self.extracted, None, "extract")
                return self._final(self.extracted, "extract (fallback)")
            except Exception as e:
                self.failed("final", e)
        return 1

    def _final(self, spec, label):
        official = self.openapi["servers"][0]["url"]
        host = urlparse(official).netloc
        self.out_dir.mkdir(parents=True, exist_ok=True)
        spec_path = self.out_dir / ("%s.spec.json" % self.slot)
        spec_path.write_text(json.dumps(spec, indent=1))
        wf = self._compile_lint(spec, None, "final")
        text = json.dumps(wf)
        if "trycloudflare" in text:
            raise RuntimeError("final workflow contains a trycloudflare URL; not saving it")
        if not host or host not in text:
            raise RuntimeError("final workflow does not contain the official host %r; not saving it" % host)
        saved = self._save(self.slot, wf)
        self.final.update(workflow_id=saved["workflow_id"], agent_id=saved["agent_id"])
        self.ev("saved", slot=self.slot, workflow_id=saved["workflow_id"], agent_id=saved["agent_id"],
                url_kind="official", spec=label)
        path = self.out_dir / ("%s.workflow.json" % self.slot)
        bounded(lambda: freya.export_workflow(saved["workflow_id"], str(path)), FREYA_CALL_S)
        self.final.update(exported=True, export=str(path))
        self.ev("export", path=str(path), spec_path=str(spec_path))
        print(json.dumps({"slot": self.slot, "workflow_id": saved["workflow_id"], "agent_id": saved["agent_id"],
                          "export": str(path)}), flush=True)
        return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="python -m builder.main", description=__doc__.split("\n")[0])
    ap.add_argument("briefs", nargs="+", help="brief files or directories")
    ap.add_argument("--slot", help="Freya slot name, used exactly (default: the first brief's folder and name plus a hash of the brief set)")
    ap.add_argument("--openapi", help="the bank's OpenAPI file (default: openapi.json next to the briefs, else the only OpenAPI .json "
                    "there; none or several is an error)")
    ap.add_argument("--no-selftest", action="store_true", help="skip the bank, the tunnel and the self-test")
    ap.add_argument("--budget-min", type=float, default=27.0, help="hard stop, minutes (default 27)")
    ap.add_argument("--now", help="call variable `now` for self-test cases (ISO, e.g. 2026-10-14T11:00)")
    ap.add_argument("--out-dir", default=str(ROOT / "submission"), help="export folder (default submission/)")
    ap.add_argument("--trace-dir", default=str(ROOT / "traces"), help=argparse.SUPPRESS)
    return ap.parse_args(argv)


def main(argv=None):
    return Build(parse_args(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
