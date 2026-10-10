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
        self.event("model_call", model=model, purpose=purpose)

    def close(self):
        with self._lock:
            if not self._f.closed:
                self._f.close()

    # ------------------------------------------------------------------ markdown

    def write_markdown(self, path=None):
        path = Path(path) if path else self.path.with_suffix(".md")
        path.write_text(scrub(render_markdown(self.events, self.path.name)), encoding="utf-8")
        return path


def _of(events, kind):
    return [e for e in events if e["kind"] == kind]


def _one(events, kind):
    xs = _of(events, kind)
    return xs[-1] if xs else {}


def _cell(v, n=160):
    s = v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False)
    s = " ".join(s.split()).replace("|", "\\|")
    return s if len(s) <= n else s[:n] + "..."


def render_markdown(events, name):
    start, end = _one(events, "start"), _one(events, "end")
    out = ["# Build trace %s" % name, ""]
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
    out += ["## Model calls (%d)" % len(calls), "", "| At (s) | Model | Purpose |", "|---|---|---|"]
    out += ["| %s | %s | %s |" % (c.get("s"), _cell(c.get("model")), _cell(c.get("purpose"))) for c in calls]
    out.append("")

    out += ["## Created on Freya", "", "| Slot | Workflow ID | Agent ID | Base URL kind |", "|---|---|---|---|"]
    out += ["| %s | %s | %s | %s |" % (_cell(s.get("slot")), _cell(s.get("workflow_id")), _cell(s.get("agent_id")),
                                       _cell(s.get("url_kind"))) for s in _of(events, "saved")]
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
    for s in _of(events, "selftest_skipped"):
        out.append("- skipped: %s" % _cell(s.get("why")))
    out.append("")

    fixes = _of(events, "fix_round")
    if fixes:
        out += ["## Spec fix rounds", ""]
        for f in fixes:
            out.append("- round %s: %s change(s), %s discarded" % (f.get("round"), len(f.get("changes") or []),
                                                                  len(f.get("discarded") or [])))
            out += ["  - `%s` (quote: \"%s\")" % (_cell(c.get("path")), _cell(c.get("quote"), 200)) for c in f.get("changes") or []]
        out.append("")

    out += ["## Export", ""]
    out += ["- %s" % _cell(e.get("path")) for e in _of(events, "export")] or ["- (none)"]
    out.append("")

    problems = _of(events, "phase_failed") + _of(events, "no_spec")
    if problems:
        out += ["## Problems", ""]
        out += ["- %s: %s" % (p.get("phase", p["kind"]), _cell(p.get("error") or p.get("errors"), 300)) for p in problems]
        out.append("")
    return "\n".join(out)
