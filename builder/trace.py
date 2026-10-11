"""Run trace: one JSON object per line, every line passed through redact.scrub() before it is written.

    t = Trace("traces/20261011T200000Z-brief-a.jsonl")
    t.event(kind, **fields)          # {"t": UTC ISO time, "s": seconds since start, "kind": kind, **fields}
    t.model_call(model, purpose)     # an event of kind "model_call"
    t.write_markdown()               # traces/<same name>.md: a human-readable summary of the same events

The extractor, oracle, settle and fix sessions call .event / .model_call through builder.sdk.Session.
Lines are flushed as they are written, so a crash leaves a usable trace.
"""
import datetime
import json
import os
import re
import threading
import time
from pathlib import Path

from builder.redact import scrub


def _utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Trace:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.t0 = time.monotonic()
        self.events = []          # scrubbed copies, for the markdown summary
        self._lock = threading.Lock()
        self._f = open(self.path, "a", encoding="utf-8")

    def event(self, kind, **fields):
        row = {"t": _utc(), "s": round(time.monotonic() - self.t0, 1), "kind": kind, **fields}
        line = scrub(json.dumps(row, default=str, ensure_ascii=False))
        with self._lock:
            self.events.append(json.loads(line))
            if not self._f.closed:
                self._f.write(line + "\n")
                self._f.flush()

    def model_call(self, model, purpose):
        self.event("model_call", model=model, purpose=purpose, about=describe(purpose))

    def close(self):
        with self._lock:
            if not self._f.closed:
                self._f.close()

    # ------------------------------------------------------------------ markdown

    def write_markdown(self, path=None):
        path = Path(path) if path else self.path.with_suffix(".md")
        path.write_text(scrub(render_markdown(self.events, self.path.name)), encoding="utf-8")
        return path


def describe(purpose):
    """One-line description of a model call from its session purpose ("extract:a#1", "oracle#1", "fix:round1#2")."""
    name, _, n = str(purpose).partition("#")
    first = n in ("", "1")
    kind, _, label = name.partition(":")
    if kind == "extract":
        return ("read the brief into a policy spec (independent extraction %s)" % label if first
                else "repair extraction %s's spec from validator errors" % label)
    if kind == "oracle":
        return "write test scenarios with expected records from the brief alone (never sees the spec)"
    if kind == "settle":
        return ("settle disagreements between the two extractions and oracle mismatches, each with a brief quote"
                if first else "settle: fix remaining validator errors and mismatches")
    if kind == "fix":
        return ("propose quoted spec fixes from failed self-test calls (%s)" % label if first
                else "repair the self-test fix after validator errors")
    return str(purpose)


def _of(events, kind):
    return [e for e in events if e["kind"] == kind]


def _one(events, kind):
    xs = _of(events, kind)
    return xs[-1] if xs else {}


def _cell(v, n=160):
    s = scrub(v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False))   # scrub before cutting
    s = " ".join(s.split()).replace("|", "\\|")
    return s if len(s) <= n else s[:n] + "..."


REPO = Path(__file__).resolve().parents[1]
# the saved event's url_kind, in words (the self-test runs against a local copy of the mock bank)
BANK = {"tunnel": "local mock bank (self-test)", "official": "official mock bank"}


def relative_paths(text):
    """No absolute local paths in the summary: repo and working-directory paths become relative, home becomes ~."""
    for base in sorted({str(REPO), os.getcwd()}, key=len, reverse=True):
        if base and base != os.sep:
            text = text.replace(base + os.sep, "")
            text = re.sub(re.escape(base) + r"(?![\w.-])", ".", text)
    home = str(Path.home())
    return text.replace(home + os.sep, "~" + os.sep) if home and home != os.sep else text


def _saved_rows(events):
    """One row per saved agent: a self-test agent reused for several rounds is listed once, with its rounds."""
    rows, order = {}, []
    for s in _of(events, "saved"):
        key = (s.get("slot"), s.get("workflow_id"), s.get("agent_id"), s.get("url_kind"))
        if key not in rows:
            rows[key] = []
            order.append(key)
        if s.get("round") is not None:
            rows[key].append(str(s.get("round")))
    out = []
    for key in order:
        slot, wf, agent, kind = key
        rounds = rows[key]
        use = ("self-test round%s %s" % ("s" if len(rounds) > 1 else "", ", ".join(rounds)) if rounds
               else "final agent" if kind == "official" else "-")
        out.append("| %s | %s | %s | %s | %s |" % (_cell(slot), _cell(wf), _cell(agent), _cell(BANK.get(kind, kind or "")), use))
    return out


def render_markdown(events, name):
    return relative_paths(_render(events, name))


def _render(events, name):
    start, end = _one(events, "start"), _one(events, "end")
    stem = name.rsplit(".", 1)[0]
    run, _, label = stem.partition("-")
    out = [("# Build trace: %s (run %s)" % (label, run)) if label else "# Build trace %s" % stem, ""]
    out += ["| | |", "|---|---|",
            "| Started (UTC) | %s |" % start.get("t", "?"),
            "| Ended (UTC) | %s |" % end.get("t", "(no end event)"),
            "| Duration | %s s |" % end.get("duration_s", "?"),
            "| Result | %s |" % ("ok" if end.get("ok") else "FAILED" if end else "?"),
            "| Briefs | %s |" % _cell(start.get("briefs")),
            "| Slot | %s |" % _cell(start.get("slot", "")),
            "| Git commit | %s |" % _cell(start.get("git_commit", "")),
            "| Builder model | %s |" % _cell(start.get("model", "")),
            "| Openapi | %s |" % _cell(start.get("openapi", "")), ""]

    calls = _of(events, "model_call")
    out += ["## Model calls (%d)" % len(calls), "", "| At (s) | Model | Purpose | What it does |", "|---|---|---|---|"]
    out += ["| %s | %s | %s | %s |" % (c.get("s"), _cell(c.get("model")), _cell(c.get("purpose")), _cell(c.get("about", ""))) for c in calls]
    out.append("")

    out += ["## Created on Freya", "", "| Slot | Workflow ID | Agent ID | Bank service | Used for |", "|---|---|---|---|---|"]
    out += _saved_rows(events)
    out.append("")

    out += ["## Spec", ""]
    for v in _of(events, "spec_validated"):
        out.append("- validated (%s): %s errors" % (v.get("stage", "extract"), v.get("errors")))
    for c in _of(events, "conflicts"):
        out.append("- conflicts recorded by the extractor: %s" % c.get("count"))
        out += ["  - %s: kept %s" % (_cell(x.get("field")), _cell(x.get("kept"), 200)) for x in c.get("items") or []]
    for lt in _of(events, "lint"):
        out.append("- lint (%s): %s errors, %s warnings" % (lt.get("stage"), lt.get("errors"), lt.get("warnings")))
    out.append("")

    rounds = _of(events, "selftest_round")
    out += ["## Self-test runs on our own agent (%d)" % len(rounds), ""]
    if rounds:
        out += ["| Round | Passed | Agent | Simulation | Failing cases |", "|---|---|---|---|---|"]
        out += ["| %s | %s/%s | %s | %s | %s |" % (r.get("round"), r.get("passed"), r.get("total"), _cell(r.get("agent_id", "")),
                                                  _cell(r.get("simulation_id", "")), _cell(", ".join(r.get("failing") or []) or "-"))
                for r in rounds]
    for r in rounds:
        if r.get("void"):
            out.append("- round %s: %d void, not counted (platform retried the run): %s"
                       % (r.get("round"), len(r["void"]), _cell(", ".join(r["void"]))))
    for s in _of(events, "selftest_no_signal"):
        out.append("- round %s: no signal (%s)" % (s.get("round"), _cell(s.get("why"))))
    for s in _of(events, "selftest_skipped"):
        out.append("- skipped: %s" % _cell(s.get("why")))
    out.append("")

    fixes = _of(events, "fix_round")
    if fixes:
        out += ["## Spec fix rounds", ""]
        for f in fixes:
            out.append("- round %s: %s change(s), %s discarded" % (f.get("round"), len(f.get("changes") or []),
                                                                  len(f.get("discarded") or [])))
            for c in f.get("changes") or []:
                out.append("  - `%s` (quote: \"%s\")" % (_cell(c.get("path")), _cell(c.get("quote"), 200)))
                if "before" in c or "after" in c:
                    out.append("    - before: %s" % _cell(c.get("before"), 200))
                    out.append("    - after: %s" % _cell(c.get("after"), 200))
            out += ["  - warning `%s`: %s %s" % (_cell(w.get("path")), _cell(w.get("why")), _cell(w.get("literal")))
                    for w in f.get("warnings") or []]
        out.append("")

    notes = []
    for e in events:
        k = e["kind"]
        if k == "simulation_started":
            notes.append("- simulation %s started (round %s)" % (_cell(e.get("simulation_id")), e.get("round")))
        elif k == "orphan_simulation":
            notes.append("- ORPHAN simulation %s (round %s) left running at the budget stop" % (_cell(e.get("id")), e.get("round")))
        elif k == "workspace_busy":
            notes.append("- workspace busy: another simulation was running (round %s)" % e.get("round"))
        elif k == "fix_sandbox":
            notes.append("- fix session changed files other than fix.json: %s" % _cell(e.get("changed"), 300))
        elif k == "final_spec_switch":
            notes.append("- final spec switched to %s: %s" % (_cell(e.get("to_label")), _cell(e.get("why"))))
        elif k == "best_spec":
            notes.append("- best spec: %s (comparable cases %s: round 1 %s, round 2 %s)" % (
                _cell(e.get("label")), e.get("comparable"), e.get("round1_passed"), e.get("round2_passed")))
    if notes:
        out += ["## Simulations and decisions", ""] + notes + [""]

    out += ["## Export", ""]
    out += ["- %s" % _cell(e.get("path")) for e in _of(events, "export")] or ["- (none)"]
    out.append("")

    problems = _of(events, "phase_failed") + _of(events, "no_spec")
    if problems:
        out += ["## Problems", ""]
        out += ["- %s: %s" % (p.get("phase", p["kind"]), _cell(p.get("error") or p.get("errors"), 300)) for p in problems]
        out.append("")
    return "\n".join(out)
