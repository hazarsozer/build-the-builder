"""Workflow building blocks: tools, code nodes, conditions, transfer and close nodes.

Generic: nothing here knows a bank brief. Platform rules followed (docs/workflow-format.md,
docs/tools-and-code.md): no `{{`/`}}` inside code, no top-level `and`, booleans tested as
both "true" and "True", every passthrough node ends with an `always` transition.
"""
import json
import re

CODE_TIMEOUT_MS = 10000
TOOL_TIMEOUT_MS = 12000


# ---------------------------------------------------------------- openapi

def body_schema(openapi, tool):
    op = openapi["paths"]["/" + tool]["post"]
    return op["requestBody"]["content"]["application/json"]["schema"]


def body_enum(openapi, tool, field):
    try:
        return list(body_schema(openapi, tool)["properties"][field]["enum"])
    except KeyError:
        return []


# ---------------------------------------------------------------- tools

def api_tool(name, base_url, openapi, body_props, required, description="", retries=0,
             mapping=None, pre=None, post=None, path=None):
    """An api_call tool for POST <base_url>/<path or name>.

    Each body property starts from its openapi schema (type, enum) and is overridden by
    `body_props[field]` (default, description, enum...). customer_id is always baked.
    """
    path = path or name
    oa_props = body_schema(openapi, path).get("properties", {})
    props = {}
    for field, over in [("customer_id", {"default": "{{customer_id}}"})] + list(body_props.items()):
        p = {k: v for k, v in oa_props.get(field, {}).items() if k in ("type", "enum")}
        p.setdefault("type", "string")
        p.update(over)
        props[field] = p
    t = {
        "type": "function",
        "config": {"timeoutMs": TOOL_TIMEOUT_MS, "maxRetries": retries},
        "function": {
            "name": name,
            "handler": "__function__:api_call",
            "description": description or name,
            "parameters": {"type": "object", "properties": {
                "url": {"type": "string", "default": base_url.rstrip("/") + "/" + path},
                "method": {"type": "string", "default": "POST"},
                "headers": {"type": "object", "properties": {
                    "Content-Type": {"type": "string", "default": "application/json"}}},
                "body": {"type": "object", "properties": props, "required": list(required)},
            }},
        },
    }
    if mapping:
        t["response_mapping"] = mapping
    if pre:
        assert_no_braces(pre, name + " pre")
        t["pre_process_code"] = pre
    if post:
        assert_no_braces(post, name + " post")
        t["post_process_code"] = post
    return t


def assert_no_braces(code, where):
    assert "{{" not in code and "}}" not in code, where


def py_json(obj):
    """A Python expression that evaluates to `obj`, safe to embed in code (no brace pairs)."""
    s = json.dumps(obj, ensure_ascii=True)
    while "}}" in s or "{{" in s:
        s = s.replace("}}", "} }").replace("{{", "{ {")
    return "json.loads(%r)" % s


# ---------------------------------------------------------------- nodes

def code_node(name, code, target, extra_transitions=()):
    assert_no_braces(code, name)
    return {
        "name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": target,
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "pre_actions": [{"type": "function", "function": {
            "name": "code_exec", "handler": "__function__:code_exec",
            "parameters": {"type": "object", "properties": {
                "code": {"type": "string", "default": code},
                "timeout_ms": {"type": "number", "default": CODE_TIMEOUT_MS}}}}}],
        "transitions": list(extra_transitions) + [always(target)],
    }


def always(target):
    return {"target_node": target, "description": "always", "condition": {"operator": "always"}}


def cond_any(*conds):
    return {"logic": "or", "conditions": list(conds)}


def all_of(*conds):
    """AND, nested inside the top-level OR (a top-level `and` is not allowed)."""
    return cond_any({"logic": "and", "conditions": list(conds)})


def eq(var, val):
    return {"variable": var, "operator": "equals", "operand": val}


def gt(var, val):
    return {"variable": var, "operator": "greater_than", "operand": val}


def is_true(*vars_):
    return cond_any(*[eq(v, s) for v in vars_ for s in ("true", "True")])


def go(target, desc, condition):
    return {"target_node": target, "description": desc, "condition": condition}


def transfer_node(name, queue, close, base_url, openapi, tool=None):
    tool = tool or api_tool("transfer_to_agent", base_url, openapi, {"queue": {"default": queue}}, ["queue"], retries=1)
    return {
        "name": name, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": close,
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "pre_actions": [tool],
        "transitions": [always(close)],
    }


# A farewell template uses single-brace placeholders {var}; they are filled from vars at run time.
FILL_CODE = r'''
def _fill(t):
    s = re.sub(r'\{([A-Za-z_][A-Za-z0-9_.]*)\}', lambda m: str(vars.get(m.group(1)) or ''), t)
    s = re.sub(r' +', ' ', s)
    s = re.sub(r' ([,.!?])', r'\1', s)
    return s.strip()
'''


def close_node(name, outcome, reason, farewell):
    """Set outcome/reason (read by the close_call lifecycle tool) and the farewell, then go to goodbye."""
    code = "import re\n"
    code += "vars['outcome'] = %r\n" % outcome
    if reason:
        code += "vars['reason'] = %r\n" % reason
    code += "T = %r\n" % farewell
    code += FILL_CODE
    code += "try:\n    vars['farewell'] = _fill(T)\nexcept Exception:\n    vars['farewell'] = T\n"
    return code_node(name, code, "goodbye")


def goodbye_node():
    return {
        "name": "goodbye", "node_type": "end_call", "on_enter": {"type": "silent"},
        "disable_inactivity_monitor": True, "transitions": [],
        "terminal": {"type": "function", "function": {
            "name": "call_closed", "handler": "__function__:end_call",
            "parameters": {"type": "object", "properties": {
                "message_mode": {"type": "string", "default": "custom"},
                "spoken_message": {"type": "string", "default": "{{farewell}}"}}}}},
    }


FAREWELL_RE = re.compile(r"^T = (.*)$", re.M)
