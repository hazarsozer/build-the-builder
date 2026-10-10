"""LLM fix round: failed self-test calls -> quoted spec changes.

    fix_async(spec, result, cases, brief_paths, openapi, trace, *, get_call, budget_s) -> (spec, info)
    fix(...)  # sync wrapper (asyncio.run)

One short Agent SDK session (builder.sdk: no Bash, setting_sources [], strict MCP, env files denied, and a PreToolUse
hook that refuses any path outside the work dir) reads the brief, spec.json and failures.json (each failed case:
expected records, failure reasons, tools called, and the trimmed tail of its transcript from
get_call(call_id)["messages"]). It writes fix.json decisions {path, value|remove, quote, why}.

Rules enforced here, not left to the model:
- a fix.json with two decisions on the same path is rejected whole;
- a decision is kept only by its own merits (index, not path): it needs a quote found in the brief
  (extract.quote_in_brief: >= 4 words, >= 20 characters, word boundaries), a path below the top level (whole
  `outcomes` excepted), and a place within the first MAX_DECISIONS kept;
- a number or enum-like literal a decision adds that its quote does not contain gives a warning (kept, traced);
- fix.json is read only when it is a plain file inside the work dir; any other file the session changed in the work
  dir is reported (`fix_sandbox` trace event);
- the cases are never edited (failures.json is never read back);
- if the patched spec fails validate() worse than before, one repair ask; still worse: the original spec is kept.

info: {"changes": [{path, quote, why, before, after}], "discarded": [...], "warnings": [...], "errors": n,
       "kept_original": bool, "sandbox": [...], ...session stats}.
"""
import asyncio
import copy
import hashlib
import json
import os
import re
import time
from pathlib import Path

from builder import sdk
from builder.extract import _error_list, _loose, apply_settle, quote_in_brief
from builder.specdiff import _tokens
from builder.validate import resolve_path, validate

FIX_S = 240                # whole session, all asks
REPAIR_MIN_S = 30          # skip the repair ask with less than this left
TRANSCRIPT_CHARS = 5000    # per failed call, the tail is kept
MESSAGE_CHARS = 600        # per message
MAX_DECISIONS = 6          # per round
TOP_LEVEL_OK = ("outcomes",)
VALUE_TRACE_CHARS = 4000   # before/after values in the trace are cut to this
_ENUMISH = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


# ------------------------------------------------------------------ failures.json

def _line(m):
    role = m.get("role") or "?"
    text = m.get("message")
    if not text:
        rest = {k: v for k, v in m.items() if k not in ("role", "message") and v not in (None, "", [], {})}
        text = json.dumps(rest, default=str) if rest else ""
    text = " ".join(sdk.scrub(str(text)).split())     # scrub before cutting: a cut secret would escape the scrub
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


# ------------------------------------------------------------------ decisions

_TOKEN_TEXT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")


def _render(toks):
    out = ""
    for i, (kind, k) in enumerate(toks):
        if kind == "key":
            out += k if i == 0 else "." + k
        else:
            out += "[%s]" % k
    return out


def canonical_path(path, spec):
    """(canonical path, None) or (None, why). The path must round-trip specdiff's tokenizer exactly (the parser the
    edit uses), start with a key, and use plain names; a list index whose item has an id becomes [id]."""
    if not isinstance(path, str) or not path:
        return None, "path is not a non-empty string"
    toks = _tokens(path)
    if not toks or toks[0][0] != "key" or _render(toks) != path:
        return None, "path does not round-trip the spec path syntax (stray dots or brackets)"
    if any(not _TOKEN_TEXT.match(str(k)) for kind, k in toks if kind != "idx"):
        return None, "path has a name with characters outside [A-Za-z0-9_-]"
    canon, cur = [], spec
    for kind, k in toks:
        if kind == "idx" and isinstance(cur, list) and k < len(cur) and isinstance(cur[k], dict) and cur[k].get("id"):
            kind, k = "id", cur[k]["id"]
        canon.append((kind, k))
        if kind == "key":
            cur = cur.get(k) if isinstance(cur, dict) else None
        elif kind == "idx":
            cur = cur[k] if isinstance(cur, list) and k < len(cur) else None
        else:
            hit = [x for x in cur if isinstance(x, dict) and x.get("id") == k] if isinstance(cur, list) else []
            cur = hit[0] if len(hit) == 1 else None
    return _render(canon), None


def _overlaps(a, b):
    return a == b or b.startswith(a + ".") or b.startswith(a + "[") or a.startswith(b + ".") or a.startswith(b + "[")


def screen(data, brief, spec=None):
    """(kept decisions with canonical paths, discarded [{index, path, why}]). Each decision is judged by its own
    index, never by path. A fix.json whose decisions touch the same or nested canonical paths is rejected whole."""
    spec = spec if spec is not None else {}
    if not isinstance(data, dict) or not isinstance(data.get("decisions"), list):
        return [], [{"why": "fix.json has no decisions list"}] if data is not None else []
    decisions = data["decisions"]
    canon, discarded = {}, []
    for i, d in enumerate(decisions):
        path = d.get("path") if isinstance(d, dict) else None
        c, why = canonical_path(path, spec)
        if why:
            discarded.append({"index": i, "path": path if isinstance(path, str) else repr(path), "why": why})
        else:
            canon[i] = c
    cs = list(canon.values())
    clash = sorted({x for x in cs for y in cs if x is not y and _overlaps(x, y)} |
                   {x for x in cs if cs.count(x) > 1})
    if clash:
        return [], [{"why": "fix.json rejected: more than one decision on the same or nested paths", "paths": clash}]
    kept = []
    for i, d in enumerate(decisions):
        if i not in canon:
            continue
        path = canon[i]
        why = None
        if len(_tokens(path)) == 1 and path not in TOP_LEVEL_OK:
            why = "top-level path (only %s may be replaced whole)" % ", ".join(TOP_LEVEL_OK)
        elif not str(d.get("quote") or "").strip():
            why = "no quote: a spec change must cite the brief"
        elif not quote_in_brief(d["quote"], brief):
            why = "quote not found in the brief (needs >= 4 words, >= 20 characters, exact wording)"
        elif len(kept) >= MAX_DECISIONS:
            why = "over the cap of %d decisions per round" % MAX_DECISIONS
        if why:
            discarded.append({"index": i, "path": path, "why": why, "quote": d.get("quote")})
        else:
            kept.append(dict(d, path=path))
    discarded.sort(key=lambda x: x.get("index", -1))
    return kept, discarded


def _literals(v, out=None):
    out = set() if out is None else out
    if isinstance(v, bool) or v is None:
        return out
    if isinstance(v, (int, float)):
        out.add(("number", str(int(v)) if float(v).is_integer() else repr(v)))
    elif isinstance(v, str):
        if _ENUMISH.match(v):
            out.add(("enum", v))
    elif isinstance(v, dict):
        for x in v.values():
            _literals(x, out)
    elif isinstance(v, list):
        for x in v:
            _literals(x, out)
    return out


def _in_quote(kind, lit, quote):
    if kind == "number":
        return re.search(r"(?<![\d.])%s(?![\d])" % re.escape(lit), quote) is not None
    return (" %s " % _loose(lit)) in (" %s " % _loose(quote))


def literal_warnings(path, before, after, quote):
    """Numbers and enum-like strings in `after` that are not in `before` and not in the quote."""
    added = _literals(after) - _literals(before)
    return [{"path": path, "literal": lit, "kind": kind, "why": "added %s is not in the decision's quote" % kind}
            for kind, lit in sorted(added) if not _in_quote(kind, lit, str(quote))]


def _cut(v):
    s = json.dumps(v, default=str)
    return v if len(s) <= VALUE_TRACE_CHARS else s[:VALUE_TRACE_CHARS] + "...(%d chars)" % len(s)


def apply_decisions(spec, data, brief):
    """(patched spec, changes, discarded, warnings)."""
    kept, discarded = screen(data, brief, spec)
    patched, applied, skipped, _ = apply_settle(spec, {"decisions": kept}, brief)
    discarded += skipped
    changes, warnings = [], []
    for a in applied:
        found, before = resolve_path(spec, a["path"])
        before = before if found else None
        after = None if a["removed"] else a["value"]
        changes.append({"path": a["path"], "quote": a["quote"], "why": a.get("why"),
                        "before": _cut(before), "after": _cut(after), "removed": a["removed"]})
        warnings += literal_warnings(a["path"], before, after, a["quote"])
    return patched, changes, discarded, warnings


# ------------------------------------------------------------------ sandbox

def snapshot(work):
    """{relative path: sha256 or 'symlink:<target>'} for everything under work (symlinks not followed)."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(work, followlinks=False):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            rel = str(p.relative_to(work))
            if p.is_symlink():
                out[rel] = "symlink:" + os.readlink(p)
            elif p.is_file():
                out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def sandbox_changes(before, after, allowed=("fix.json",)):
    out = []
    for k in sorted(set(before) | set(after)):
        if k in allowed:
            continue
        if k not in before:
            out.append({"path": k, "change": "created"})
        elif k not in after:
            out.append({"path": k, "change": "deleted"})
        elif before[k] != after[k]:
            out.append({"path": k, "change": "modified"})
    return out


def read_fix_json(work):
    p = Path(work) / "fix.json"
    if p.is_symlink() or (p.exists() and not p.is_file()):
        return None, "fix.json is not a plain file in the work dir; ignored"
    if p.exists() and p.resolve().parent != Path(work).resolve():
        return None, "fix.json resolves outside the work dir; ignored"
    if not p.exists():
        return None, "fix.json was not written"

    def no_dup(pairs):
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate key in fix.json: %s" % sorted({k for k in keys if keys.count(k) > 1}))
        return dict(pairs)
    try:
        return json.loads(p.read_text(), object_pairs_hook=no_dup), None
    except ValueError as exc:
        return None, "fix.json is not valid JSON: %s" % exc


# ------------------------------------------------------------------ session

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
    snap0 = snapshot(work)
    try:
        async with sdk.Session(work, "fix:round%d" % round_no, trace, client_factory=client_factory,
                               confine=True) as sess:
            await sess.ask(sdk.load_prompt("fix.md", briefs=sdk.brief_names(work), n_fail=len(failures),
                                           n_total=result.get("total", len(cases))),
                           timeout_s=max(1.0, end - time.monotonic()))
            data, err = read_fix_json(work)
            patched, changes, discarded, warnings = apply_decisions(spec, data, brief)
            errs = ([err] if err else []) + validate(patched, openapi)
            if len(errs) > before and end - time.monotonic() > REPAIR_MIN_S:
                await sess.ask(sdk.load_prompt("fix_repair.md", errors=_error_list(errs)),
                               timeout_s=max(1.0, end - time.monotonic()))
                data, err = read_fix_json(work)
                patched, changes, discarded, warnings = apply_decisions(spec, data, brief)
                errs = ([err] if err else []) + validate(patched, openapi)
            info.update(sess.stats())
    finally:
        info["sandbox"] = sandbox_changes(snap0, snapshot(work))
        if info["sandbox"]:
            trace.event("fix_sandbox", round=round_no, changed=info["sandbox"])
    if err:
        discarded = discarded + [{"why": err}]
    if len(errs) > before:
        info.update(kept_original=True, rejected_errors=[sdk.scrub(e) for e in errs[:10]],
                    rejected_changes=changes, changes=[], discarded=discarded, warnings=warnings, errors=before)
        return copy.deepcopy(spec), info
    info.update(changes=changes, discarded=discarded, warnings=warnings, errors=len(errs))
    return patched, info


def fix(*a, **kw):
    return asyncio.run(fix_async(*a, **kw))
