"""Fallback builder: the kit's sample agentic builder, hardened and pointed at our tested parts.

    set -a; source env.sh; set +a
    uv run python fallback/agent_builder.py briefs/brief-a.md [--dry]

Differences from examples/agent_builder.py: skill build-phone-agent-v2; no MCP servers at all (strict);
reads of env.sh / .env* denied through both disallowed_tools and a settings deny list; every trace and
console line goes through builder.redact.scrub; the trace also records the end of the run.
Trace: traces/<start time>.jsonl (start, one line per message, end). --dry prints the resolved options and exits.
"""
import asyncio, dataclasses, json, os, pathlib, sys, time

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
    return {"type": type(message).__name__, "t": time.strftime("%H:%M:%S"), **dataclasses.asdict(message)}


def describe(options):
    keys = ("cwd", "model", "skills", "setting_sources", "mcp_servers", "strict_mcp_config", "allowed_tools", "disallowed_tools",
            "settings", "permission_mode", "max_turns", "env")
    return {k: getattr(options, k) for k in keys}


async def main(briefs):
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock, query
    trace = KIT / "traces" / (time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    trace.parent.mkdir(exist_ok=True)
    options = make_options()
    prompt = f"Use the {SKILL} skill to build a phone agent for: " + " ".join(briefs)
    with trace.open("w") as out:
        out.write(trace_line({"start": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "briefs": briefs, "model": options.model, "skill": SKILL}) + "\n")
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


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dry"]
    if "--dry" in sys.argv:
        print(scrub(json.dumps(describe(make_options()), indent=1, default=str)))
        sys.exit(0)
    if not args:
        sys.exit(__doc__)
    asyncio.run(main(args))
