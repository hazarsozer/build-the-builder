"""Render docs/spec-guide.md, the readable field guide, from builder/spec_schema.json.

    python -m builder.spec_guide          # rewrite docs/spec-guide.md
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "builder" / "spec_schema.json"
GUIDE_PATH = ROOT / "docs" / "spec-guide.md"


def _deref(schema, node):
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 10:
        name = node["$ref"].split("/")[-1]
        target = dict(schema["$defs"][name])
        extra = {k: v for k, v in node.items() if k != "$ref"}
        target.update(extra)
        node = target
        seen += 1
    return node


def _type(schema, node):
    node = _deref(schema, node)
    if "enum" in node:
        return "one of " + ", ".join("`%s`" % json.dumps(v) if v is None else "`%s`" % v for v in node["enum"])
    if "const" in node:
        return "`%s`" % node["const"]
    if "oneOf" in node:
        return " or ".join(_type(schema, x) for x in node["oneOf"])
    t = node.get("type")
    if isinstance(t, list):
        t = " or ".join(t)
    if t == "array":
        return "list of " + _type(schema, node.get("items", {}))
    if t == "object" and "additionalProperties" in node and isinstance(node["additionalProperties"], dict) \
            and "properties" not in node:
        return "map of name -> " + _type(schema, node["additionalProperties"])
    bits = [t or "any"]
    if node.get("pattern"):
        bits.append("pattern `%s`" % node["pattern"])
    if "minimum" in node:
        bits.append("min %s" % node["minimum"])
    if "maximum" in node:
        bits.append("max %s" % node["maximum"])
    return ", ".join(bits)


def _conditional(schema, node):
    out = []
    for c in node.get("allOf", []) + ([node] if "if" in node else []):
        cond = c.get("if", {}).get("properties", {})
        req = c.get("then", {}).get("required", [])
        if cond and req:
            k, v = next(iter(cond.items()))
            val = v.get("const", v.get("enum"))
            out.append("when `%s` is %s, also required: %s" % (k, val, ", ".join("`%s`" % r for r in req)))
    return out


def _walk(schema, node, path, lines, required):
    node = _deref(schema, node)
    desc = node.get("description", "")
    req = " (required)" if required else " (optional)"
    lines.append("- `%s`%s: %s. %s" % (path, req, _type(schema, node), desc))
    for c in _conditional(schema, node):
        lines.append("  - " + c)
    t = node.get("type")
    if t == "array":
        item = _deref(schema, node.get("items", {}))
        if item.get("type") == "object" and "properties" in item:
            for c in _conditional(schema, item):
                lines.append("  - each item: " + c)
            for k, v in item["properties"].items():
                _walk(schema, v, "%s[].%s" % (path, k), lines, k in item.get("required", []))
    elif t == "object":
        if "properties" in node:
            for k, v in node["properties"].items():
                _walk(schema, v, "%s.%s" % (path, k), lines, k in node.get("required", []))
        elif isinstance(node.get("additionalProperties"), dict):
            ap = _deref(schema, node["additionalProperties"])
            if ap.get("type") == "object" and "properties" in ap:
                for k, v in ap["properties"].items():
                    _walk(schema, v, "%s.<name>.%s" % (path, k), lines, k in ap.get("required", []))


def render(schema):
    lines = [
        "# Policy spec field guide",
        "",
        "Generated from `builder/spec_schema.json` by `python -m builder.spec_guide`. Do not edit by hand.",
        "",
        schema.get("description", ""),
        "",
        "Paths: dotted keys; `[]` marks a list item; `<name>` a map key. In spec paths (facts.maps_to, sources,",
        "conflicts.field) a bracket holds a list index (`bands[1].deadline.n`) or the `id` of a list item",
        "(`events[paid].say`).",
        "",
        "## Fields",
        "",
    ]
    for k, v in schema["properties"].items():
        _walk(schema, v, k, lines, k in schema.get("required", []))
    return "\n".join(lines).rstrip() + "\n"


def main():
    schema = json.loads(SCHEMA_PATH.read_text())
    GUIDE_PATH.write_text(render(schema))
    print("wrote", GUIDE_PATH)


if __name__ == "__main__":
    main()
