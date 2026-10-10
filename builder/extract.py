"""Extractor: brief files + openapi -> policy spec.

extract(brief_paths, openapi, trace, deadline_s=...) -> (spec, errors, info)

`extract()` calls asyncio.run(); a caller that is already inside an event loop (async code)
must `await extract_async(...)` instead.

1. Three sessions run concurrently: two independent extractions (each: fact pass, then
   spec.json, then up to 3 validate-repair rounds in the same conversation) and the brief
   oracle (scenarios written without seeing any spec).
2. diff_specs() compares the two specs. Mechanical disagreements (union lists, sentences
   where one side is verbatim in the brief) are resolved in code; the rest, plus every
   oracle mismatch (oracle.check), go to one settle session that must cite a brief quote
   for each change or rejected scenario.
3. The merged spec is validated; if the merge has more errors than the best single spec,
   that spec is kept.

Time budget (deadline_s, from the call): the parallel phase ends after PHASE_S at most;
once the first extraction finishes, the others get SECOND_WAIT_S more. Settle gets
SETTLE_S at most and is skipped when less than SETTLE_MIN_S is left. A session cut off by
the budget never loses a spec it already wrote: its work dir is read back.

Every trace line goes through redact.scrub() (see builder.sdk.Session).
"""
import asyncio
import copy
import json
import re
import time
from pathlib import Path

from builder import sdk
from builder.oracle import check, oracle_async
from builder.specdiff import MISSING, auto_resolve, diff_entries, diff_specs, set_path  # noqa: F401
from builder.validate import validate

REPAIR_ROUNDS = 3
SETTLE_ROUNDS = 2
FIRST_TIMEOUT_S = 480     # per-ask ceiling; the phase budget cancels earlier
REPAIR_TIMEOUT_S = 180
PHASE_S = 270             # all three parallel sessions
SECOND_WAIT_S = 90        # after the first extraction finishes
SETTLE_S = 75             # settle session, all rounds
SETTLE_MIN_S = 60         # skip settle with less than this left
DEADLINE_S = PHASE_S + SETTLE_S + 15
MAX_ERRORS_SHOWN = 40

_ALNUM = re.compile(r"[^a-z0-9]+")


def _loose(s):
    return _ALNUM.sub(" ", str(s).casefold()).strip()


QUOTE_MIN_WORDS = 4
QUOTE_MIN_CHARS = 20


def quote_in_brief(quote, brief, min_words=QUOTE_MIN_WORDS, min_chars=QUOTE_MIN_CHARS):
    """True when quote (normalised: casefold, non-alphanumerics to one space) occurs in the brief on word boundaries
    and is long enough to be evidence: at least min_words words and min_chars characters after normalising."""
    q = _loose(quote)
    if not q or len(q.split()) < min_words or len(q) < min_chars:
        return False
    return (" %s " % q) in (" %s " % _loose(brief))


def _validate(spec, openapi):
    try:
        return validate(spec, openapi)
    except Exception as exc:  # a spec odd enough to crash a semantic check
        return ["validate crashed: %s: %s" % (type(exc).__name__, exc)]


def load_spec(work, openapi):
    """Read spec.json and facts.json from a work dir; facts.json becomes spec['facts']."""
    work = Path(work)
    spec, err = sdk.read_json(work / "spec.json")
    if err:
        return None, [err]
    if not isinstance(spec, dict):
        return None, ["spec.json must be a JSON object"]
    errs = []
    facts, ferr = sdk.read_json(work / "facts.json")
    if ferr:
        errs.append(ferr)
    elif not isinstance(facts, list):
        errs.append("facts.json must be a JSON list of {id, quote, maps_to}")
    else:
        spec["facts"] = facts
    errs = _validate(spec, openapi) + errs
    return spec, errs


def fact_quote_misses(spec, brief):
    return [f.get("id") for f in spec.get("facts") or []
            if isinstance(f, dict) and not quote_in_brief(f.get("quote", ""), brief, min_words=1, min_chars=1)]


def _error_list(errs):
    shown = errs[:MAX_ERRORS_SHOWN]
    more = "" if len(errs) <= MAX_ERRORS_SHOWN else "\n(... %d more)" % (len(errs) - MAX_ERRORS_SHOWN)
    return "\n".join("- " + e for e in shown) + more


def repair_prompt(errs):
    return sdk.load_prompt("repair.md", errors=_error_list(errs))


# ------------------------------------------------------------------ one extraction

async def extract_one(brief_paths, openapi, trace, label, client_factory=None, workdirs=None):
    """One extraction session. Never raises and never loses spec.json already on disk:
    on any failure, including cancellation by the time budget, the work dir is read back
    and the failure is appended to the errors."""
    work = sdk.make_workdir(brief_paths, openapi, "extract-" + label)
    if workdirs is not None:
        workdirs[label] = work
    info = {"label": label, "workdir": str(work), "errors_per_round": []}
    t0 = time.monotonic()
    spec, errs = None, ["spec.json was not written"]
    sess = None
    try:
        prompt = sdk.load_prompt("extract.md", briefs=sdk.brief_names(work))
        async with sdk.Session(work, "extract:" + label, trace, client_factory=client_factory) as sess:
            await sess.ask(prompt, timeout_s=FIRST_TIMEOUT_S)
            spec, errs = load_spec(work, openapi)
            info["errors_per_round"].append(len(errs))
            trace.event("extract_round", label=label, round=0, errors=len(errs),
                        first=[sdk.scrub(e) for e in errs[:5]])
            r = 0
            while errs and r < REPAIR_ROUNDS:
                r += 1
                await sess.ask(repair_prompt(errs), timeout_s=REPAIR_TIMEOUT_S)
                spec, errs = load_spec(work, openapi)
                info["errors_per_round"].append(len(errs))
                trace.event("extract_round", label=label, round=r, errors=len(errs),
                            first=[sdk.scrub(e) for e in errs[:5]])
    except (Exception, asyncio.CancelledError) as exc:  # incl. SessionError: keep what is on disk
        why = "cancelled (time budget)" if isinstance(exc, asyncio.CancelledError) else \
            sdk.scrub("%s: %s" % (type(exc).__name__, exc))
        spec, errs = load_spec(work, openapi)
        errs = errs + ["extraction %s interrupted: %s" % (label, why)]
        info["interrupted"] = why
        trace.event("extract_interrupted", label=label, why=why, spec_on_disk=spec is not None)
    if sess is not None:
        info.update(sess.stats())
        info["init"] = sess.init
    info["seconds"] = round(time.monotonic() - t0, 1)
    if spec is not None:
        info["fact_quote_misses"] = fact_quote_misses(spec, sdk.brief_text(work))
        info["facts"] = len(spec.get("facts") or [])
    return spec, errs, info


# ------------------------------------------------------------------ settle

def apply_settle(base, settle, brief):
    """Apply settle.json decisions to a copy of base. Returns (spec, applied, skipped, rejected)."""
    spec = copy.deepcopy(base)
    applied, skipped = [], []
    if not isinstance(settle, dict):
        return spec, applied, [{"why": "settle.json is not an object"}], []
    for d in settle.get("decisions") or []:
        if not isinstance(d, dict) or not d.get("path"):
            skipped.append({"decision": d, "why": "no path"})
            continue
        if not str(d.get("quote") or "").strip():
            skipped.append({"path": d["path"], "why": "no quote: a spec change must cite the brief"})
            continue
        value = MISSING if d.get("remove") else d.get("value")
        try:
            set_path(spec, d["path"], value)
        except Exception as exc:
            skipped.append({"path": d["path"], "why": "could not set: %s" % exc})
            continue
        applied.append({"path": d["path"], "value": None if value is MISSING else value,
                        "removed": value is MISSING, "quote": d["quote"], "why": d.get("why"),
                        "quote_found": quote_in_brief(d["quote"], brief)})
    rejected = []
    for r in settle.get("rejected") or []:
        if isinstance(r, dict) and r.get("scenario") and str(r.get("quote") or "").strip():
            rejected.append({"scenario": r["scenario"], "quote": r["quote"], "why": r.get("why"),
                             "quote_found": quote_in_brief(r["quote"], brief)})
        else:
            skipped.append({"rejected": r, "why": "a rejected scenario needs a scenario id and a quote"})
    return spec, applied, skipped, rejected


async def settle(brief_paths, openapi, trace, base, other, open_diffs, mismatches, scenarios,
                 client_factory=None, budget_s=SETTLE_S):
    """One settle session within budget_s seconds in total (all rounds)."""
    end = time.monotonic() + budget_s
    work = sdk.make_workdir(brief_paths, openapi, "settle")
    brief = sdk.brief_text(work)
    (work / "spec-a.json").write_text(json.dumps(base, indent=1))
    if other is not None:
        (work / "spec-b.json").write_text(json.dumps(other, indent=1))
    (work / "scenarios.json").write_text(json.dumps(scenarios, indent=1))
    questions = {"disagreements": [{"path": p, "a": None if a is MISSING else a, "a_absent": a is MISSING,
                                    "b": None if b is MISSING else b, "b_absent": b is MISSING}
                                   for p, a, b in open_diffs],
                 "oracle_mismatches": mismatches}
    (work / "questions.json").write_text(json.dumps(questions, indent=1))
    info = {"workdir": str(work), "settle_rounds": []}
    spec, applied, skipped, rejected = copy.deepcopy(base), [], [], []
    async with sdk.Session(work, "settle", trace, client_factory=client_factory) as sess:
        prompt = sdk.load_prompt("settle.md", briefs=sdk.brief_names(work),
                                 n_diff=len(open_diffs), n_mismatch=len(mismatches))
        await sess.ask(prompt, timeout_s=max(1.0, end - time.monotonic()))
        for r in range(SETTLE_ROUNDS):
            data, err = sdk.read_json(work / "settle.json")
            spec, applied, skipped, rejected = apply_settle(base, data or {}, brief)
            errs = ([err] if err else []) + _validate(spec, openapi)
            rej = {x["scenario"] for x in rejected}
            left = check(spec, [s for s in scenarios if s.get("id") not in rej]) if not errs else []
            info["settle_rounds"].append({"errors": len(errs), "mismatches_left": len(left),
                                          "applied": len(applied), "skipped": len(skipped)})
            trace.event("settle_round", round=r, errors=len(errs), mismatches_left=len(left),
                        applied=len(applied), skipped=len(skipped))
            left_s = end - time.monotonic()
            if not errs and not left or r == SETTLE_ROUNDS - 1 or left_s < 15:
                break
            (work / "remaining.json").write_text(json.dumps({"validate_errors": errs[:MAX_ERRORS_SHOWN],
                                                              "oracle_mismatches": left}, indent=1))
            await sess.ask(sdk.load_prompt("settle_repair.md", errors=_error_list(errs) if errs else "(none)",
                                           n_mismatch=len(left)), timeout_s=left_s)
        info.update(sess.stats())
    info.update(applied=applied, skipped=skipped, rejected=rejected)
    return spec, info


# ------------------------------------------------------------------ main entry

def _resolve_mechanical(base, other, brief):
    entries = diff_entries(base, other)
    merged = copy.deepcopy(base)
    auto, open_ = [], []
    for p, a, b in entries:
        r = auto_resolve(p, a, b, brief)
        if r is None:
            open_.append((p, a, b))
        else:
            value, why = r
            set_path(merged, p, value)
            auto.append({"path": p, "decision": why})
    return merged, entries, auto, open_


def _write(run_dir, name, obj):
    (run_dir / name).write_text(json.dumps(obj, indent=1, default=str))


async def _run_phase(tasks, phase_end):
    """Wait for the first extraction (or the phase end), then SECOND_WAIT_S for the rest."""
    ext = [t for k, t in tasks.items() if k in ("a", "b")]
    pending = set(tasks.values())
    first_done = False
    while pending and not first_done:
        left = phase_end - time.monotonic()
        if left <= 0:
            break
        done, pending = await asyncio.wait(pending, timeout=left, return_when=asyncio.FIRST_COMPLETED)
        first_done = any(t in done for t in ext) or not any(t in pending for t in ext)
    if pending and first_done:
        grace = min(SECOND_WAIT_S, max(0.0, phase_end - time.monotonic()))
        if grace > 0:
            _, pending = await asyncio.wait(pending, timeout=grace)
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return pending


async def extract_async(brief_paths, openapi, trace=None, *, client_factory=None, dual=True,
                        use_oracle=True, deadline_s=DEADLINE_S):
    """deadline_s: seconds this call may take in total (main passes what is left of the build)."""
    trace = trace or sdk.NullTrace()
    t0 = time.monotonic()
    end = t0 + deadline_s
    run_dir = sdk.make_workdir(brief_paths, openapi, "run", with_schema=False)
    brief = sdk.brief_text(run_dir)
    info = {"run_dir": str(run_dir), "model": sdk.model_name(), "timings": {}, "deadline_s": deadline_s}
    trace.event("extract_start", briefs=[str(p) for p in brief_paths], model=sdk.model_name(),
                dual=dual, oracle=use_oracle, deadline_s=deadline_s)

    workdirs = {}
    tasks = {"a": asyncio.create_task(extract_one(brief_paths, openapi, trace, "a", client_factory, workdirs))}
    if dual:
        tasks["b"] = asyncio.create_task(extract_one(brief_paths, openapi, trace, "b", client_factory, workdirs))
    if use_oracle:
        tasks["oracle"] = asyncio.create_task(oracle_async(brief_paths, openapi, trace,
                                                           client_factory=client_factory))
    cut = await _run_phase(tasks, t0 + min(PHASE_S, deadline_s))
    if cut:
        trace.event("extract_cut", tasks=[k for k, t in tasks.items() if t in cut])
    info["timings"]["parallel_s"] = round(time.monotonic() - t0, 1)

    results, failures = {}, {}
    for k, t in tasks.items():
        if t.cancelled():
            failures[k] = "cancelled (time budget)"
        elif t.exception() is not None:
            failures[k] = sdk.scrub("%s: %s" % (type(t.exception()).__name__, t.exception()))
        else:
            results[k] = t.result()
    for k in ("a", "b"):
        if k in results and results[k][2].get("interrupted"):
            failures[k] = results[k][2]["interrupted"]
    info["failures"] = failures
    for k, why in failures.items():
        trace.event("extract_failed", task=k, why=why)

    cands = [(k, results[k]) for k in ("a", "b") if k in results and results[k][0] is not None]
    if not cands:
        # last resort: a spec a session wrote before it failed
        for k, work in sorted(workdirs.items()):
            s, e = load_spec(work, openapi)
            if s is not None:
                cands.append((k, (s, e, {"label": k, "workdir": str(work), "recovered_from_disk": True})))
        if cands:
            trace.event("extract_recovered", labels=[k for k, _ in cands])
    info["extract"] = {k: results[k][2] for k in ("a", "b") if k in results}
    for k, (_, _, i) in cands:
        info["extract"].setdefault(k, i)
    scenarios, oinfo = results.get("oracle", ([], {}))
    info["oracle"] = dict(oinfo, scenarios=len(scenarios))
    if not cands:
        errs = ["extraction produced no spec: %s" % failures] if failures else \
            ["extraction produced no spec: %s" % [results[k][1] for k in results if k != "oracle"]]
        info["seconds"] = round(time.monotonic() - t0, 1)
        trace.event("extract_end", ok=False, seconds=info["seconds"])
        return None, errs, info

    # best spec first: fewest validation errors, then A
    cands.sort(key=lambda kv: len(kv[1][1]))
    base_label, (base, _, _) = cands[0]
    other = cands[1][1][0] if len(cands) > 1 else None
    info["base"] = base_label
    for k, (s, e, _) in cands:
        _write(run_dir, "spec-%s.json" % k, s)
        info.setdefault("candidate_errors", {})[k] = e

    if other is not None:
        merged, entries, auto, open_diffs = _resolve_mechanical(base, other, brief)
    else:
        merged, entries, auto, open_diffs = copy.deepcopy(base), [], [], []
    info["diff_paths"] = [p for p, _, _ in entries]
    info["auto_resolved"] = auto
    info["open_diffs"] = [p for p, _, _ in open_diffs]
    _write(run_dir, "diff.json", [{"path": p, "a": a, "b": b} for p, a, b in entries])
    trace.event("dual_diff", paths=len(entries), auto=len(auto), open=len(open_diffs),
                open_paths=info["open_diffs"][:30])

    merged_errs = _validate(merged, openapi)
    mismatches = check(merged, scenarios) if scenarios and not merged_errs else []
    info["oracle_mismatches"] = mismatches
    _write(run_dir, "scenarios.json", scenarios)
    _write(run_dir, "mismatches.json", mismatches)
    trace.event("oracle_check", scenarios=len(scenarios), mismatches=len(mismatches),
                fields=sorted({m["field"] for m in mismatches}))

    final = merged
    if open_diffs or mismatches or merged_errs:
        left = end - time.monotonic()
        if left < SETTLE_MIN_S:
            info["settle_skipped"] = "%.0f s left, below %d s" % (left, SETTLE_MIN_S)
            trace.event("settle_skipped", seconds_left=round(left, 1))
        else:
            t1 = time.monotonic()
            try:
                settled, sinfo = await settle(brief_paths, openapi, trace, merged, other, open_diffs, mismatches,
                                              scenarios, client_factory=client_factory,
                                              budget_s=min(SETTLE_S, left))
                info["settle"] = sinfo
                info["settle_decisions"] = sinfo["applied"]
                info["rejected_scenarios"] = sinfo["rejected"]
                final = settled
            except Exception as exc:
                info["settle_error"] = sdk.scrub("%s: %s" % (type(exc).__name__, exc))
                trace.event("settle_failed", why=info["settle_error"])
            info["timings"]["settle_s"] = round(time.monotonic() - t1, 1)

    # keep whichever of the merged spec and the best single spec has fewer errors
    errs = _validate(final, openapi)
    base_errs = _validate(base, openapi)
    if len(base_errs) < len(errs):
        trace.event("merge_rejected", errors=len(errs), base_errors=len(base_errs),
                    first=[sdk.scrub(e) for e in errs[:5]])
        info["merge_rejected_errors"] = errs
        final, errs = base, base_errs
    if scenarios and not errs:
        rej = {x["scenario"] for x in info.get("rejected_scenarios", [])}
        info["oracle_mismatches_after"] = check(final, [s for s in scenarios if s.get("id") not in rej])
    _write(run_dir, "spec.json", final)
    info["cost_usd"] = round(sum((v.get("cost_usd") or 0) for v in
                                 [*info["extract"].values(), info["oracle"], info.get("settle", {})]), 4)
    info["seconds"] = round(time.monotonic() - t0, 1)
    trace.event("extract_end", ok=not errs, errors=len(errs), seconds=info["seconds"], cost_usd=info["cost_usd"])
    return final, errs, info


def extract(brief_paths, openapi, trace=None, **kw):
    """Synchronous wrapper (asyncio.run). Async callers must use `await extract_async(...)`."""
    return asyncio.run(extract_async(brief_paths, openapi, trace, **kw))
