"""Validate a policy spec against its JSON Schema, the bank's openapi and itself.

validate(spec, openapi) -> list of error strings; an empty list means valid.
"""
import datetime
import json
import re
from pathlib import Path

import jsonschema

from builder.lib import dates

SCHEMA = json.loads((Path(__file__).resolve().parent / "spec_schema.json").read_text())

# Names a sentence may use besides call_variables and get_account product fields.
RUNTIME_NAMES = {"product_words", "past_due", "days_late", "floor", "deadline", "amount", "date", "product"}
# close_call outcomes whose reason may be null are read from the openapi close_call `reason` description
# ("Required for every outcome except X and Y"); see reasonless_outcomes().
# Keys whose string values are not sentences said to the customer.
NOT_SENTENCES = {"sources", "facts", "examples", "custom_code", "conflicts"}

_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[([^\[\]]+)\]")
_PLACEHOLDER = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")
_DOUBLE = re.compile(r"\{\{\s*[A-Za-z_][A-Za-z0-9_.]*\s*\}\}")


# ------------------------------------------------------------------ openapi helpers

def _body(openapi, tool):
    op = openapi["paths"]["/" + tool]["post"]
    return op["requestBody"]["content"]["application/json"]["schema"]["properties"]


def _enum(openapi, tool, field):
    try:
        return list(_body(openapi, tool)[field]["enum"])
    except KeyError:
        return None


def _product_props(openapi):
    try:
        resp = openapi["paths"]["/get_account"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        return resp["properties"]["products"]["items"]["properties"]
    except KeyError:
        return {}


def reasonless_outcomes(openapi):
    """close_call outcomes that need no reason, parsed from the `reason` description ("... except X and Y."), kept
    only when they are in the outcome enum. None when the description names none: the check is then skipped."""
    try:
        desc = str(_body(openapi, "close_call")["reason"].get("description") or "")
    except (KeyError, TypeError, AttributeError):
        return None
    m = re.search(r"\bexcept\s+(?:for\s+)?(.+?)(?:\.\s|\.$|;|$)", desc)
    if not m:
        return None
    outcomes = set(_enum(openapi, "close_call", "outcome") or [])
    words = [w.strip(" `'\"") for w in re.split(r",|\band\b|\bor\b", m.group(1))]
    got = {w for w in words if w in outcomes}
    return got or None


# ------------------------------------------------------------------ path resolution

def resolve_path(spec, path):
    """Resolve "bands[1].deadline.n" or "events[paid].say" in spec. Returns (found, value).

    A bracket holds a list index, or the `id` of a list item.
    """
    if not isinstance(path, str) or not path.strip():
        return False, None
    pos = 0
    cur = spec
    for m in _PATH_TOKEN.finditer(path):
        if m.start() != pos and path[pos:m.start()] != ".":
            return False, None
        pos = m.end()
        key, idx = m.group(1), m.group(2)
        if idx is not None:
            if not isinstance(cur, list):
                return False, None
            if idx.isdigit():
                if int(idx) >= len(cur):
                    return False, None
                cur = cur[int(idx)]
            else:
                hit = [x for x in cur if isinstance(x, dict) and x.get("id") == idx]
                if len(hit) != 1:
                    return False, None
                cur = hit[0]
        else:
            if not isinstance(cur, dict) or key not in cur:
                return False, None
            cur = cur[key]
    if pos != len(path):
        return False, None
    return True, cur


# ------------------------------------------------------------------ checks

def _schema_errors(spec):
    v = jsonschema.Draft202012Validator(SCHEMA)
    out = []
    for e in sorted(v.iter_errors(spec), key=lambda e: list(map(str, e.absolute_path))):
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        out.append("schema: %s: %s" % (where, e.message))
    return out


def _openapi_errors(spec, openapi):
    errs = []
    outcomes = _enum(openapi, "close_call", "outcome") or []
    queues = _enum(openapi, "transfer_to_agent", "queue") or []
    note_reasons = _enum(openapi, "add_note", "reason") or []
    fields = _enum(openapi, "verify_identity", "field") or []
    product_types = (_product_props(openapi).get("type") or {}).get("enum") or []
    product_props = set(_product_props(openapi))
    reasonless = reasonless_outcomes(openapi)

    for i, row in enumerate(spec["outcomes"]):
        if row["outcome"] not in outcomes:
            errs.append("outcomes[%d]: outcome %r not in openapi close_call enum %s" % (i, row["outcome"], outcomes))
        if reasonless is not None and row["reason"] is None and row["outcome"] not in reasonless:
            errs.append("outcomes[%d]: outcome %r needs a reason (only %s may have none)"
                        % (i, row["outcome"], " and ".join(sorted(reasonless))))
    for o in spec.get("unused_outcomes", []):
        if o not in outcomes:
            errs.append("unused_outcomes: %r not in openapi close_call enum" % o)
    used = {row["outcome"] for row in spec["outcomes"]} | set(spec.get("unused_outcomes", []))
    for o in outcomes:
        if o not in used:
            errs.append("openapi outcome %r appears neither in outcomes nor in unused_outcomes" % o)

    for e in spec["events"]:
        if "queue" in e and e["queue"] not in queues:
            errs.append("events[%s]: queue %r not in openapi transfer_to_agent enum %s" % (e["id"], e["queue"], queues))
        for k in ("outcome", "else_outcome"):
            if e.get(k) is not None and e[k] not in outcomes:
                errs.append("events[%s]: %s %r not in openapi close_call enum" % (e["id"], k, e[k]))
        note = e.get("per_product_note")
        if note is not None and note not in note_reasons:
            errs.append("events[%s]: per_product_note %r not in openapi add_note reason enum" % (e["id"], note))

    notes = spec["multi_product"]["notes"]
    for r in notes["reasons"]:
        if r not in note_reasons:
            errs.append("multi_product.notes.reasons: %r not in openapi add_note reason enum %s" % (r, note_reasons))
    close_reasons = {row["reason"] for row in spec["outcomes"] if row.get("reason")}
    for nr, cr in (notes.get("close_reason_map") or {}).items():
        if nr not in notes["reasons"] and nr not in note_reasons:
            errs.append("multi_product.notes.close_reason_map: key %r is not a note reason" % nr)
        if cr not in close_reasons:
            errs.append("multi_product.notes.close_reason_map[%s]: %r is not a reason in outcomes" % (nr, cr))
    pr = notes.get("paid_note_reason")
    if pr is not None and pr not in notes["reasons"] and pr not in note_reasons:
        errs.append("multi_product.notes.paid_note_reason: %r is not a note reason" % pr)

    for i, q in enumerate(spec["identity"]["questions"]):
        if q["field"] not in fields:
            errs.append("identity.questions[%d]: field %r not in openapi verify_identity enum %s" % (i, q["field"], fields))

    for name, p in spec["products"].items():
        if product_types and name not in product_types:
            errs.append("products: type %r not in openapi get_account type enum %s" % (name, product_types))
        if product_props:
            for key, default in (("floor_field", None), ("amount_field", "past_due"), ("days_late_field", "days_late")):
                f = p.get(key) or default
                if f is not None and f not in product_props:
                    errs.append("products.%s: %s %r not a get_account product field%s"
                                % (name, key, f, "" if key in p else " (the default; name the field with %s)" % key))
    for t in product_types:
        if t not in spec["products"]:
            errs.append("products: openapi product type %r has no entry" % t)
    return errs


# Request fields the compiler fills itself, per tool (customer_id is always baked).
COMPILED_FIELDS = {"verify_identity": {"field", "answer"}, "get_account": set(), "create_promise": {"product_id", "amount", "date"},
                   "add_note": {"product_id", "reason"}, "schedule_callback": {"date", "time"}, "transfer_to_agent": {"queue"},
                   "close_call": {"outcome", "reason"}}
# Tools the engine calls without the dialogue model: an extra required field needs a fixed value or a default.
ENGINE_TOOLS = {"get_account", "add_note", "transfer_to_agent", "close_call"}


def _request(openapi, tool):
    try:
        return openapi["paths"]["/" + tool]["post"]["requestBody"]["content"]["application/json"]["schema"]
    except (KeyError, TypeError):
        return None


def _tool_field_errors(spec, openapi):
    errs = []
    tf = spec.get("tool_fields") or {}
    for key, g in tf.items():
        tool, _, field = key.partition(".")
        sc = _request(openapi, tool)
        props = (sc or {}).get("properties") or {}
        if field not in props:
            errs.append("tool_fields: %r is not an openapi request field (tool %s has %s)" % (key, tool, sorted(props)))
            continue
        if field == "customer_id" or field in COMPILED_FIELDS.get(tool, set()):
            errs.append("tool_fields: %r is filled by the compiler; remove it" % key)
        enum = props[field].get("enum")
        for k in ("default", "value"):
            if enum and g.get(k) is not None and g[k] not in enum:
                errs.append("tool_fields[%s].%s: %r not in the openapi enum %s" % (key, k, g[k], enum))
    for tool, m in (spec.get("responses") or {}).items():
        try:
            rprops = openapi["paths"]["/" + tool]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
        except (KeyError, TypeError):
            errs.append("responses: tool %r has no openapi 200 response schema" % tool)
            continue
        for canon, name in m.items():
            if name not in rprops:
                errs.append("responses.%s.%s: %r is not a field of the %s response (%s)" % (tool, canon, name, tool, sorted(rprops)))
    for tool in COMPILED_FIELDS:
        sc = _request(openapi, tool)
        if not sc:
            continue
        for field in sc.get("required") or []:
            if field == "customer_id" or field in COMPILED_FIELDS[tool] or field not in (sc.get("properties") or {}):
                continue
            g = tf.get(tool + "." + field) or {}
            if tool in ENGINE_TOOLS and g.get("value") is None and g.get("default") is None:
                errs.append("tool_fields: openapi requires %s.%s and the engine calls %s without the model; give it a value or a default"
                            % (tool, field, tool))
    return errs


def _event_errors(spec):
    errs = []
    ids = [e["id"] for e in spec["events"]]
    for d in sorted({i for i in ids if ids.count(i) > 1}):
        errs.append("events: duplicate id %r" % d)
    by_id = {e["id"]: e for e in spec["events"]}
    rows = {}
    for i, row in enumerate(spec["outcomes"]):
        if row["event"] not in by_id:
            errs.append("outcomes[%d]: event %r is not an events[].id" % (i, row["event"]))
            continue
        rows.setdefault(row["event"], set()).add((row["outcome"], row["reason"]))
    for e in spec["events"]:
        pairs = set()
        if e.get("outcome") is not None:
            pairs.add((e["outcome"], e.get("reason")))
        if e.get("else_outcome") is not None:
            pairs.add((e["else_outcome"], e.get("else_reason")))
        have = rows.get(e["id"], set())
        for p in pairs - have:
            errs.append("events[%s]: (%s, %s) has no outcomes row" % (e["id"], p[0], p[1]))
        for p in have - pairs:
            errs.append("outcomes: row (%s, %s) does not match event %r" % (p[0], p[1], e["id"]))
    fe = spec["confirmation"]["failure_event"]
    if fe not in by_id:
        errs.append("confirmation.failure_event %r is not an events[].id" % fe)
    return errs


def _band_errors(spec):
    errs = []
    bands = sorted(spec["bands"], key=lambda b: b["days_late"][0])
    for b in bands:
        lo, hi = b["days_late"]
        if lo > hi:
            errs.append("bands[%s]: days_late %d > %d" % (b["name"], lo, hi))
    for a, b in zip(bands, bands[1:]):
        if b["days_late"][0] > a["days_late"][1] + 1:
            errs.append("bands: gap between %s (ends %d) and %s (starts %d)"
                        % (a["name"], a["days_late"][1], b["name"], b["days_late"][0]))
        elif b["days_late"][0] <= a["days_late"][1]:
            errs.append("bands: overlap between %s (ends %d) and %s (starts %d)"
                        % (a["name"], a["days_late"][1], b["name"], b["days_late"][0]))
    return errs


def _band_for(spec, days_late):
    for b in spec["bands"]:
        if b["days_late"][0] <= days_late <= b["days_late"][1]:
            return b
    return None


def _example_errors(spec):
    errs = []
    cal = spec["calendar"]
    for i, ex in enumerate(spec["examples"]):
        tag = "example[%d] (%s, today %s, days_late %s)" % (i, ex["kind"], ex["today"], ex["days_late"])
        band = _band_for(spec, ex["days_late"])
        if band is None:
            errs.append("%s: no band covers days_late" % tag)
            continue
        try:
            today = datetime.date.fromisoformat(ex["today"])
            dl = dates.deadline(today, band["deadline"], cal)
            if ex["kind"] == "deadline":
                got = dl
            else:
                k = ex["attempt_index"]
                if k > len(band["attempts"]):
                    errs.append("%s: band %s has only %d attempt(s), no attempt %d"
                                % (tag, band["name"], len(band["attempts"]), k))
                    continue
                got = dates.attempt_date(today, band["attempts"][k - 1], dl, cal)
        except Exception as exc:  # bad rule or date
            errs.append("%s: could not compute: %s" % (tag, exc))
            continue
        if got.isoformat() != ex["expect"]:
            errs.append("%s: expected %s, computed %s" % (tag, ex["expect"], got.isoformat()))
    return errs


def _strings(node, path=""):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            if path == "" and k in NOT_SENTENCES:
                continue
            yield from _strings(v, "%s.%s" % (path, k) if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _strings(v, "%s[%d]" % (path, i))


def _placeholder_errors(spec, openapi):
    errs = []
    known = set(spec["call_variables"]) | RUNTIME_NAMES | set(_product_props(openapi))
    for path, s in _strings(spec):
        if _DOUBLE.search(s):
            errs.append("%s: double brace placeholder %r; use single braces {name}" % (path, _DOUBLE.search(s).group(0)))
        for name in _PLACEHOLDER.findall(s):
            if name not in known:
                errs.append("%s: unknown placeholder {%s}" % (path, name))
    return errs


def _path_errors(spec):
    errs = []
    for i, f in enumerate(spec.get("facts", [])):
        mt = f.get("maps_to")
        if not mt or not str(mt).strip():
            errs.append("facts[%d] (%s): maps_to is empty; every fact must map to a spec field or extra_rules"
                        % (i, f.get("id")))
        elif not resolve_path(spec, mt)[0]:
            errs.append("facts[%d] (%s): maps_to %r does not resolve in the spec" % (i, f.get("id"), mt))
    for k in spec["sources"]:
        if not resolve_path(spec, k)[0]:
            errs.append("sources: key %r does not resolve in the spec" % k)
    return errs


def validate(spec, openapi):
    errs = _schema_errors(spec)
    if errs:
        # Facts are checked even when the shape is wrong, so an extractor sees them in one round.
        if isinstance(spec, dict) and isinstance(spec.get("facts"), list):
            for i, f in enumerate(spec["facts"]):
                if isinstance(f, dict) and not resolve_path(spec, f.get("maps_to"))[0]:
                    errs.append("facts[%d] (%s): maps_to %r does not resolve in the spec" % (i, f.get("id"), f.get("maps_to")))
        return errs
    errs += _openapi_errors(spec, openapi)
    errs += _tool_field_errors(spec, openapi)
    errs += _event_errors(spec)
    errs += _band_errors(spec)
    if not any(e.startswith("bands") for e in errs):
        errs += _example_errors(spec)
    errs += _placeholder_errors(spec, openapi)
    errs += _path_errors(spec)
    return errs
