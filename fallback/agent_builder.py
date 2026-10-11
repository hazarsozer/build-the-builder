"""Fallback builder: the kit's sample agentic builder, hardened and pointed at our tested parts.

    set -a; source env.sh; set +a
    uv run python fallback/agent_builder.py briefs/brief-a.md [--dry]
    python3 fallback/agent_builder.py <case_folder> <mock_url>        (a case folder with its own service contract)

Differences from examples/agent_builder.py: skill build-phone-agent-v2; no MCP servers at all (strict);
reads of env.sh / .env* denied through both disallowed_tools and a settings deny list; every trace and
console line goes through builder.redact.scrub; the trace also records the end of the run.
Secrets: the model's Bash needs the FREYA keys to save and test, so env-level secrets cannot be hidden from it. The Bash deny
patterns are best-effort; `scrub` on every trace and console line is the real control (the skill also forbids printing them).
Trace: traces/<start time>.jsonl (start, one line per message, end). --dry prints the resolved options and exits.
"""
import asyncio, dataclasses, json, os, pathlib, re, sys, time

KIT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))
from builder.redact import scrub  # noqa: E402

SKILL = "build-phone-agent-v2"
DENY_READ = ["Read(./env.sh)", "Read(**/env.sh)", "Read(./.env)", "Read(**/.env)", "Read(**/.env.*)", "Read(./.freya-agents.json)",
             "Read(~/.claude/**)", "Read(//proc/**)"]
# Bash cannot be fenced by patterns alone; these block the obvious ways to print the secret files or the environment.
DENY_BASH = ["Bash(cat *env.sh*)", "Bash(cat *.env*)", "Bash(head *env.sh*)", "Bash(tail *env.sh*)", "Bash(less *env.sh*)",
             "Bash(grep * env.sh*)", "Bash(sed * env.sh*)", "Bash(awk * env.sh*)", "Bash(source env.sh*)", "Bash(. env.sh*)", "Bash(cp *env.sh*)",
             "Bash(printenv*)", "Bash(env)", "Bash(env |*)", "Bash(set)", "Bash(echo *KEY*)", "Bash(echo *FREYA*)", "Bash(git push*)",
             "Bash(curl*)", "Bash(wget*)"]
DENY = DENY_READ + DENY_BASH


def make_options():
    from claude_agent_sdk import ClaudeAgentOptions
    return ClaudeAgentOptions(
        cwd=KIT,
        model=os.environ.get("BUILDER_MODEL", "claude-opus-5-5"),
        skills=[SKILL],                          # only our skill is listed; the SDK adds the Skill tool
        setting_sources=["project"],             # this folder's .claude only, never ~/.claude
        mcp_servers={}, strict_mcp_config=True,  # no MCP servers, and none loaded from anywhere else
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        disallowed_tools=DENY,
        settings=json.dumps({"permissions": {"deny": DENY}}),
        permission_mode="dontAsk",               # anything not listed above is refused
        max_turns=150,
        # A test run takes up to 11 minutes. Let a command run that long, and keep it in the foreground:
        # the builder stops when Claude ends its turn, so a test left in the background is never read.
        env={"BASH_DEFAULT_TIMEOUT_MS": "900000", "BASH_MAX_TIMEOUT_MS": "1200000", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},
    )


def trace_line(obj):
    """One JSON line of the trace with every secret replaced."""
    return scrub(json.dumps(obj, default=str))


def message_record(message):
    try:
        body = dataclasses.asdict(message)
    except Exception:
        body = {"repr": repr(message)}
    return {"type": type(message).__name__, "t": time.strftime("%H:%M:%S"), **body}


def describe(options):
    keys = ("cwd", "model", "skills", "setting_sources", "mcp_servers", "strict_mcp_config", "allowed_tools", "disallowed_tools",
            "settings", "permission_mode", "max_turns", "env")
    return {k: getattr(options, k) for k in keys}


def case_mode(args):
    """(case_folder, mock_url) when called as `<case_folder> <mock_url>`, else None (brief files)."""
    if len(args) == 2 and pathlib.Path(args[0]).is_dir() and args[1].startswith("https://"):
        return pathlib.Path(args[0]), args[1].rstrip("/")
    return None


def convert_case(folder, slot):
    """Every document of the case folder as text under out/<slot>/docs/ (PDF, Word, Excel converted); returns the list."""
    from builder import doctext
    docs = KIT / "out" / slot / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    names = []
    for f in doctext.case_files(folder):
        rel = f.relative_to(folder)
        name = str(rel).replace("/", "__") + ("" if f.suffix.lower() in (".md", ".txt", ".json") else ".md")
        (docs / name).write_text(doctext.to_text(f))
        names.append(name)
    return docs, names


def case_prompt(folder, mock_url, slot, docs, names):
    return (f"Use the {SKILL} skill to build a phone agent for the case folder {folder}. This is a case folder with its own "
            f"service contract (the 'Case folder' parts of the skill apply). Its documents, converted to text, are in "
            f"{docs.relative_to(KIT)}/ ({len(names)} files): read every one of them in full before you write anything. "
            f"Slot: {slot}. Mock base URL: {mock_url}. Every tool of the workflow calls {mock_url} followed by the service's "
            f"path from the case's own service contract (the contract's path, not its host). Never use tools/openapi.json "
            f"for this case. Write the workflow to out/{slot}.workflow.json.")


def export_case(slot, mock_url):
    """Export the slot's workflow from the platform to submission/<slot>.workflow.json and describe it for the trace."""
    import freya
    state = json.loads((KIT / ".freya-agents.json").read_text())
    if slot not in state:
        return {"exported": False, "why": "the agent never saved slot " + slot}
    path = KIT / "submission" / f"{slot}.workflow.json"
    path.parent.mkdir(exist_ok=True)
    freya.export_workflow(state[slot]["workflow_id"], str(path))
    w = json.loads(path.read_text())
    nodes = w["workflow"].get("nodes") or {}
    urls = sorted(set(re.findall(r'"url":\s*\{[^{}]*?"default":\s*"([^"]+)"', json.dumps(w["workflow"]))))
    return {"exported": True, "path": str(path.relative_to(KIT)), "workflow_id": w["workflow_id"], "agent_id": w["agent_id"],
            "steps": len(nodes), "tools": len({t["function"]["name"] for n in (nodes.values() if isinstance(nodes, dict) else nodes)
                                               for t in (n.get("tools") or []) if isinstance(t, dict) and t.get("function")}),
            "tool_urls": urls, "only_mock": bool(urls) and all(u.startswith(mock_url + "/") for u in urls)}


async def main(briefs):
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock, query
    trace = KIT / "traces" / (time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    trace.parent.mkdir(exist_ok=True)
    options = make_options()
    case = case_mode(briefs)
    if case:
        sys.path.insert(0, str(KIT / "builder"))
        from builder.build import slot_for
        slot = slot_for(case[0])
        docs, names = convert_case(case[0], slot)
        prompt = case_prompt(case[0], case[1], slot, docs, names)
        print(f"case folder {case[0]} -> slot {slot}; {len(names)} documents converted to {docs.relative_to(KIT)}/; mock {case[1]}", flush=True)
    else:
        prompt = f"Use the {SKILL} skill to build a phone agent for: " + " ".join(briefs)
    with trace.open("w") as out:
        out.write(trace_line({"start": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "briefs": briefs, "model": options.model, "skill": SKILL,
                              **({"slot": slot, "documents": names} if case else {})}) + "\n")
        print(f"start {time.strftime('%Y-%m-%dT%H:%M:%S%z')} model {options.model} (every model call is one Claude Agent SDK session; "
              f"its messages are in {trace.relative_to(KIT)})", flush=True)
        async for message in query(prompt=prompt, options=options):
            out.write(trace_line(message_record(message)) + "\n")
            out.flush()
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(scrub(block.text), flush=True)
                    elif isinstance(block, ToolUseBlock):
                        print(scrub(f"  [{block.name}] {str(block.input)[:120]}"), flush=True)
            elif isinstance(message, ResultMessage):
                out.write(trace_line({"end": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "turns": message.num_turns,
                                      "seconds": message.duration_ms / 1000, "cost_usd": message.total_cost_usd}) + "\n")
                print(f"\ndone: {message.num_turns} turns, {message.duration_ms / 1000:.0f} s, ${message.total_cost_usd or 0:.2f}. Trace: {trace}")
        if case:
            try:
                info = export_case(slot, case[1])
            except Exception as e:
                info = {"exported": False, "why": f"{type(e).__name__}: {e}"}
            out.write(trace_line({"export": info, "end": time.strftime("%Y-%m-%dT%H:%M:%S%z")}) + "\n")
            print(scrub("export: " + json.dumps(info)), flush=True)
            print(f"end {time.strftime('%Y-%m-%dT%H:%M:%S%z')}", flush=True)
            if not info.get("exported") or not info.get("only_mock"):
                sys.exit(1)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dry"]
    if "--dry" in sys.argv:
        print(scrub(json.dumps(describe(make_options()), indent=1, default=str)))
        sys.exit(0)
    if not args:
        sys.exit(__doc__)
    asyncio.run(main(args))
