"""Agent SDK plumbing shared by the extractor, the oracle and the settle session.

Every session runs locked down:
- cwd is a fresh work dir under .work/ holding copies of the inputs, nothing else;
- tools Read, Write, Edit, Glob only (no Bash), permission_mode dontAsk;
- setting_sources [] (no user or project settings, hooks or CLAUDE.md);
- strict MCP config with no servers (no claude.ai connectors);
- deny rules on env.sh and .env files.
Every trace line goes through redact.scrub().
"""
import asyncio
import json
import os
import shutil
import string
import time
import uuid
from pathlib import Path

from builder.redact import scrub

ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = ROOT / ".work"
PROMPTS = Path(__file__).resolve().parent / "prompts"
DEFAULT_MODEL = "claude-opus-5-5"

TOOLS = ["Read", "Write", "Edit", "Glob"]
DENY = ["Bash", "WebFetch", "WebSearch",
        "Read(**/env.sh)", "Read(**/.env*)", "Edit(**/env.sh)", "Edit(**/.env*)",
        "Read(//**/env.sh)", "Read(//**/.env*)"]

SYSTEM = ("You are a careful analyst working on files in the current directory only. "
          "Use the Read, Write, Edit and Glob tools. Write the files you are asked for, exactly in the "
          "format asked. Do not read anything outside the current directory.")


def model_name():
    return os.environ.get("BUILDER_MODEL") or DEFAULT_MODEL


class NullTrace:
    """Trace interface used by the builder: .event(kind, **fields) and .model_call(model, purpose)."""

    def __init__(self):
        self.events = []
        self.calls = []

    def event(self, kind, **fields):
        self.events.append((kind, fields))

    def model_call(self, model, purpose):
        self.calls.append((model, purpose))


class SessionError(RuntimeError):
    pass


# ------------------------------------------------------------------ files

def expand_briefs(brief_paths):
    """Brief files in order; a directory contributes its files (sorted), except openapi.json,
    hidden files and secret files (env.sh, .env*)."""
    out = []
    for p in brief_paths:
        p = Path(p)
        if p.is_dir():
            out += sorted(x for x in p.iterdir() if x.is_file() and x.name != "openapi.json"
                          and not x.name.startswith(".") and not _secret_file(x.name))
        elif not _secret_file(p.name):
            out.append(p)
    return out


def _secret_file(name):
    """env.sh and .env* never go into a work dir, even when they sit next to a brief."""
    return name == "env.sh" or name.startswith(".env")


def make_workdir(brief_paths, openapi, label, with_schema=True):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = WORK_ROOT / ("%s-%s-%s" % (stamp, label, uuid.uuid4().hex[:6]))
    work.mkdir(parents=True)
    names = set()
    for p in expand_briefs(brief_paths):
        name = p.name
        if name in names or name in ("openapi.json", "spec_schema.json", "spec-guide.md"):
            name = "%d-%s" % (len(names) + 1, name)
        names.add(name)
        shutil.copyfile(p, work / name)
    (work / ".briefs.json").write_text(json.dumps(sorted(names)))
    (work / "openapi.json").write_text(json.dumps(openapi, indent=1))
    if with_schema:
        shutil.copyfile(ROOT / "builder" / "spec_schema.json", work / "spec_schema.json")
        shutil.copyfile(ROOT / "docs" / "spec-guide.md", work / "spec-guide.md")
    return work


def brief_names(work):
    return json.loads((Path(work) / ".briefs.json").read_text())


def brief_text(work):
    return "\n\n".join((Path(work) / n).read_text(errors="replace") for n in brief_names(work))


def read_json(path):
    """(value, error_string_or_None)."""
    path = Path(path)
    if not path.exists():
        return None, "%s was not written" % path.name
    try:
        return json.loads(path.read_text()), None
    except json.JSONDecodeError as exc:
        return None, "%s is not valid JSON: %s" % (path.name, exc)


def load_prompt(name, **subs):
    text = (PROMPTS / name).read_text()
    subs = {k: (", ".join(v) if isinstance(v, (list, tuple)) else str(v)) for k, v in subs.items()}
    return string.Template(text).safe_substitute(subs)


# ------------------------------------------------------------------ session

def _inside(root, p):
    q = Path(os.path.expanduser(str(p)))
    q = q if q.is_absolute() else root / q
    try:
        q.resolve().relative_to(root)
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def confine_hook(work):
    """A PreToolUse hook that denies any tool call whose path resolves outside work.

    can_use_tool would be shadowed here: the SDK auto-approves whole-tool allowed_tools entries before the callback
    runs (claude_agent_sdk.types._get_can_use_tool_shadowed_warning), so a hook is the gate that always runs."""
    root = Path(work).resolve()

    def deny(why):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": why}}

    async def hook(input_data, tool_use_id, context):
        ti = (input_data or {}).get("tool_input") or {}
        for key in ("file_path", "path", "notebook_path"):
            v = ti.get(key)
            if v and not _inside(root, v):
                return deny("%s is outside the work dir; only files in the current directory may be used" % key)
        pat = ti.get("pattern")
        if (input_data or {}).get("tool_name") == "Glob" and pat and (
                os.path.isabs(os.path.expanduser(pat)) or ".." in Path(pat).parts):
            return deny("Glob patterns must stay inside the current directory")
        return {}
    return hook


def options_for(work, model, confine=False):
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
    extra = {"hooks": {"PreToolUse": [HookMatcher(matcher=None, hooks=[confine_hook(work)])]}} if confine else {}
    return ClaudeAgentOptions(
        **extra,
        cwd=str(work),
        model=model,
        system_prompt=SYSTEM,
        tools=list(TOOLS),
        allowed_tools=list(TOOLS),
        disallowed_tools=list(DENY),
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        mcp_servers={},
        max_turns=60,
    )


def _short(v, n=300):
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s if len(s) <= n else s[:n] + "...(%d chars)" % len(s)


class Session:
    """One Claude conversation in a work dir; ask() continues the same conversation."""

    def __init__(self, work, purpose, trace, client_factory=None, model=None, confine=False):
        self.work = Path(work)
        self.confine = confine
        self.purpose = purpose
        self.trace = trace
        self.model = model or model_name()
        self.client_factory = client_factory
        self.client = None
        self.cost = 0.0
        self.turns = 0
        self.api_ms = 0
        self.rounds = 0
        self.t0 = None
        self.init = None

    async def __aenter__(self):
        if self.client_factory is None:
            from claude_agent_sdk import ClaudeSDKClient
            factory = lambda options: ClaudeSDKClient(options=options)  # noqa: E731
        else:
            factory = self.client_factory
        self.client = factory(options_for(self.work, self.model, confine=self.confine))
        self.t0 = time.monotonic()
        await self.client.connect()
        return self

    async def __aexit__(self, *exc):
        try:
            await asyncio.wait_for(self.client.disconnect(), 10)
        except (Exception, asyncio.CancelledError):
            pass
        return False

    def _ev(self, kind, **fields):
        self.trace.event(kind, purpose=self.purpose,
                         **{k: scrub(v) if isinstance(v, str) else v for k, v in fields.items()})

    async def _ask(self, prompt):
        from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock, ToolUseBlock
        self.rounds += 1
        self.trace.model_call(self.model, "%s#%d" % (self.purpose, self.rounds))
        self._ev("prompt", text=_short(prompt, 400))
        await self.client.query(prompt)
        result = None
        async for msg in self.client.receive_response():
            if isinstance(msg, SystemMessage) and getattr(msg, "subtype", "") == "init" and self.init is None:
                data = getattr(msg, "data", {}) or {}
                self.init = {"tools": data.get("tools"), "mcp_servers": data.get("mcp_servers"),
                             "model": data.get("model")}
                self._ev("session_init", info=_short(self.init, 600))
            elif isinstance(msg, AssistantMessage):
                for b in msg.content:
                    if isinstance(b, ToolUseBlock):
                        inp = b.input or {}
                        target = inp.get("file_path") or inp.get("pattern") or ""
                        self._ev("tool", name=b.name, target=str(target))
                    elif isinstance(b, TextBlock) and b.text.strip():
                        self._ev("text", text=_short(b.text, 300))
            elif isinstance(msg, ResultMessage):
                result = msg
        if result is None:
            raise SessionError("%s: no result message" % self.purpose)
        self.cost += result.total_cost_usd or 0.0
        self.turns += result.num_turns or 0
        self.api_ms += result.duration_api_ms or 0
        self._ev("result", turns=result.num_turns, cost_usd=result.total_cost_usd,
                 seconds=round((result.duration_ms or 0) / 1000, 1), is_error=result.is_error,
                 subtype=result.subtype)
        if result.is_error:
            raise SessionError("%s: %s %s" % (self.purpose, result.subtype, scrub(str(result.result or ""))[:300]))
        return result

    async def ask(self, prompt, timeout_s=600):
        try:
            return await asyncio.wait_for(self._ask(prompt), timeout_s)
        except asyncio.TimeoutError:
            self._ev("timeout", seconds=timeout_s)
            try:
                await self.client.interrupt()
            except Exception:
                pass
            raise SessionError("%s: timed out after %ss" % (self.purpose, timeout_s))

    def stats(self):
        return {"cost_usd": round(self.cost, 4), "turns": self.turns, "rounds": self.rounds,
                "api_s": round(self.api_ms / 1000, 1),
                "wall_s": round(time.monotonic() - self.t0, 1) if self.t0 else 0.0,
                "model": self.model}
