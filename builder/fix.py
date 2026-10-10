"""LLM fix round: failed self-test calls -> quoted spec changes.

    fix_async(spec, result, cases, brief_paths, openapi, trace, *, get_call, budget_s) -> (spec, info)
    fix(...)  # sync wrapper (asyncio.run)

One short Agent SDK session (builder.sdk: no Bash, setting_sources [], strict MCP, env files denied) reads the brief,
spec.json and failures.json (each failed case: expected records, failure reasons, tools called, and the trimmed tail
of its transcript from get_call(call_id)["messages"]). It writes fix.json decisions {path, value|remove, quote, why}.

Rules enforced here, not left to the model:
- a decision without a quote, or whose quote is not found in the brief, is discarded;
- the cases are never edited (they are not in the work dir as an editable input that is read back);
- if the patched spec fails validate(), one repair ask is made; if it still fails, the original spec is kept.

info: {"changes": [{path, quote, why}], "discarded": [...], "errors": n, "kept_original": bool, ...session stats}.
"""
import asyncio
import copy
import json
import time

from builder import sdk
from builder.extract import _error_list, apply_settle
from builder.validate import validate

FIX_S = 240                # whole session, all asks
REPAIR_MIN_S = 30          # skip the repair ask with less than this left
TRANSCRIPT_CHARS = 5000    # per failed call, the tail is kept
MESSAGE_CHARS = 600        # per message


def _line(m):
    role = m.get("role") or "?"
    text = m.get("message")
    if not text:
        rest = {k: v for k, v in m.items() if k not in ("role", "message") and v not in (None, "", [], {})}
        text = json.dumps(rest, default=str)[:MESSAGE_CHARS] if rest else ""
    text = " ".join(str(text).split())
    if len(text) > MESSAGE_CHARS:
        text = text[:MESSAGE_CHARS] + "..."
    return "%s: %s" % (role, text)


def trim_transcript(messages, max_chars=TRANSCRIPT_CHARS):
    """'role: text' lines, the tail of the call kept within max_chars."""
    lines = [_line(m) for m in messages or [] if isinstance(m, dict)]
    out, size = [], 0
    for ln in reversed(lines):
        if size + len(ln) > max_chars:
            break
        out.append(ln)
        size += len(ln)
    if len(out) < len(lines):
        out.append("(%d earlier messages cut)" % (len(lines) - len(out)))
    return [sdk.scrub(x) for x in reversed(out)]


def failures_for(result, cases, get_call):
    by_id = {c["id"]: c for c in cases}
    out = []
    for r in result.get("rows") or []:
        if r.get("pass"):
            continue
        c = by_id.get(r["id"], {})
        msgs = []
        if r.get("call_id"):
            try:
                msgs = (get_call(r["call_id"]) or {}).get("messages") or []
            except Exception:
                msgs = []
        out.append({"id": r["id"], "scenario": r.get("scenario") or c.get("scenario"), "title": c.get("title"),
                    "customer_script": c.get("script"), "checks": c.get("checks"), "expected": c.get("expected"),
                    "failed": r.get("failed"), "warnings": r.get("warnings"), "tools_called": r.get("tools"),
                    "transcript": trim_transcript(msgs)})
    return out


def _apply(spec, data, brief):
    patched, applied, skipped, _ = apply_settle(spec, data or {}, brief)
    bad = [a for a in applied if not a["quote_found"]]
    if bad:  # re-apply without decisions whose quote is not in the brief
        keep = {a["path"] for a in applied if a["quote_found"]}
        data = {"decisions": [d for d in (data or {}).get("decisions") or []
                              if isinstance(d, dict) and d.get("path") in keep]}
        patched, applied, skipped2, _ = apply_settle(spec, data, brief)
        skipped = skipped + skipped2
    discarded = skipped + [{"path": a["path"], "why": "quote not found in the brief", "quote": a["quote"]} for a in bad]
    changes = [{"path": a["path"], "quote": a["quote"], "why": a.get("why")} for a in applied]
    return patched, changes, discarded


async def fix_async(spec, result, cases, brief_paths, openapi, trace, *, get_call, client_factory=None,
                    budget_s=FIX_S, round_no=1):
    end = time.monotonic() + budget_s
    work = sdk.make_workdir(brief_paths, openapi, "fix")
    brief = sdk.brief_text(work)
    failures = failures_for(result, cases, get_call)
    (work / "spec.json").write_text(json.dumps(spec, indent=1))
    (work / "failures.json").write_text(json.dumps(failures, indent=1, default=str))
    before = len(validate(spec, openapi))
    info = {"workdir": str(work), "failures": len(failures), "errors_before": before, "kept_original": False}
    out = copy.deepcopy(spec)
    async with sdk.Session(work, "fix:round%d" % round_no, trace, client_factory=client_factory) as sess:
        await sess.ask(sdk.load_prompt("fix.md", briefs=sdk.brief_names(work), n_fail=len(failures),
                                       n_total=result.get("total", len(cases))),
                       timeout_s=max(1.0, end - time.monotonic()))
        data, err = sdk.read_json(work / "fix.json")
        patched, changes, discarded = _apply(spec, data, brief)
        errs = ([err] if err else []) + validate(patched, openapi)
        if len(errs) > before and end - time.monotonic() > REPAIR_MIN_S:
            await sess.ask(sdk.load_prompt("fix_repair.md", errors=_error_list(errs)),
                           timeout_s=max(1.0, end - time.monotonic()))
            data, err = sdk.read_json(work / "fix.json")
            patched, changes, discarded = _apply(spec, data, brief)
            errs = ([err] if err else []) + validate(patched, openapi)
        info.update(sess.stats())
    if len(errs) > before:
        info.update(kept_original=True, rejected_errors=[sdk.scrub(e) for e in errs[:10]],
                    rejected_changes=changes, changes=[], discarded=discarded, errors=before)
        return out, info
    info.update(changes=changes, discarded=discarded, errors=len(errs))
    return patched, info


def fix(*a, **kw):
    return asyncio.run(fix_async(*a, **kw))
