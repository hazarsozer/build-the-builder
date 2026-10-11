"""Agent SDK plumbing shared by the extractor, the oracle and the settle session.

Every session runs locked down:
- cwd is a fresh work dir under .work/ holding copies of the inputs, nothing else;
- tools Read, Write, Edit, Glob only (no Bash), permission_mode dontAsk;
- setting_sources [] (no user or project settings, hooks or CLAUDE.md);
- strict MCP config with no servers (no claude.ai connectors);
- deny rules on env.sh and .env files.
- a PreToolUse hook (confine_hook, on by default for every session) denying any path outside the work dir,
  Glob braces and symlinked directory prefixes.
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

def load_openapi(path):
    """The OpenAPI document in a .json, .yaml or .yml file (parsed, not checked). Raises OSError / ValueError."""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ValueError("%s is not valid YAML: %s" % (p.name, e)) from e
    return json.loads(text)


def is_openapi_file(path):
    """True when path is a .json/.yaml/.yml file holding an OpenAPI document (an object with `openapi` and `paths`)."""
    p = Path(path)
    if p.suffix.lower() not in (".json", ".yaml", ".yml") or not p.is_file():
        return False
    try:
        doc = load_openapi(p)
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and "openapi" in doc and "paths" in doc


def _not_brief(p):
    return p.name == "openapi.json" or _secret_file(p.name) or is_openapi_file(p)


def brief_entries(brief_paths):
    """(path, name) per brief file in order. A directory contributes every file under it recursively (doctext order:
    BRIEF.md, README.md, the rest by relative path), named by its relative path; never openapi.json, hidden files,
    secret files (env.sh, .env*) or an OpenAPI document under any name."""
    from builder import doctext
    out = []
    for p in brief_paths:
        p = Path(p)
        if p.is_dir():
            out += [(x, x.relative_to(p).as_posix()) for x in doctext.case_files(p, _not_brief)]
        elif not _not_brief(p):
            out.append((p, p.name))
    return out


def expand_briefs(brief_paths):
    """Brief files in order (see brief_entries)."""
    return [p for p, _ in brief_entries(brief_paths)]


def _secret_file(name):
    """env.sh and .env* never go into a work dir, even when they sit next to a brief."""
    return name == "env.sh" or name.startswith(".env")


COPY_AS_IS = {".md", ".markdown", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml"}


def make_workdir(brief_paths, openapi, label, with_schema=True):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = WORK_ROOT / ("%s-%s-%s" % (stamp, label, uuid.uuid4().hex[:6]))
    work.mkdir(parents=True)
    names = set()
    from builder import doctext
    order = []
    for p, rel in brief_entries(brief_paths):
        convert = p.suffix.lower() not in COPY_AS_IS
        name = rel.replace("/", "__") + (".md" if convert else "")
        if name in names or name in ("openapi.json", "spec_schema.json", "spec-guide.md"):
            name = "%d-%s" % (len(names) + 1, name)
        names.add(name)
        order.append(name)
        if convert:   # Word, PDF, Excel, images, unknown: a Markdown rendering under the document's own name
            (work / name).write_text("# %s\n\n%s\n" % (rel, doctext.to_text(p).strip()), encoding="utf-8")
        else:
            (work / name).write_text(doctext.read_text(p), encoding="utf-8")   # any encoding in, utf-8 out
    (work / ".briefs.json").write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
    (work / "openapi.json").write_text(json.dumps(openapi, indent=1))
    if with_schema:
        shutil.copyfile(ROOT / "builder" / "spec_schema.json", work / "spec_schema.json")
        shutil.copyfile(ROOT / "docs" / "spec-guide.md", work / "spec-guide.md")
    return work


def brief_names(work):
    return json.loads((Path(work) / ".briefs.json").read_text(encoding="utf-8"))


def brief_text(work):
    return "\n\n".join((Path(work) / n).read_text(encoding="utf-8", errors="replace") for n in brief_names(work))


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


_GLOB_CHARS = set("*?[]{}")


def _glob_escape(root, base, pat):
    """Why a Glob pattern could reach outside root, or None. Denied: braces (expansion can produce absolute or ..
    parts), absolute patterns, .. parts, and a literal directory prefix that resolves outside root (a symlink)."""
    if "{" in pat or "}" in pat:
        return "uses braces"
    if os.path.isabs(os.path.expanduser(pat)):
        return "is absolute"
    parts = Path(pat).parts
    if ".." in parts:
        return "contains .."
    base = base if base.is_absolute() else root / base
    prefix = base
    for part in parts:
        if set(part) & _GLOB_CHARS:
            break
        prefix = prefix / part
        if not _inside(root, prefix):
            return "has a directory prefix outside the work dir"
    return None


def confine_hook(work):
    """A PreToolUse hook that denies any tool call whose path resolves outside work.

    can_use_tool would be shadowed here: the SDK auto-approves whole-tool allowed_tools entries before the callback
    runs (claude_agent_sdk.types._get_can_use_tool_shadowed_warning), so a hook is the gate that always runs."""
    root = Path(work).resolve()

    def deny(why):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": why}}

    def check(input_data):
        if not isinstance(input_data, dict):
            return deny("malformed hook input")
        ti = input_data.get("tool_input") or {}
        if not isinstance(ti, dict):
            return deny("malformed tool input")
        for key in ("file_path", "path", "notebook_path", "pattern"):
            if key in ti and ti[key] is not None and not isinstance(ti[key], str):
                return deny("%s must be a string" % key)
        for key in ("file_path", "path", "notebook_path"):
            v = ti.get(key)
            if v and not _inside(root, v):
                return deny("%s is outside the work dir; only files in the current directory may be used" % key)
        pat = ti.get("pattern")
        if input_data.get("tool_name") == "Glob" and pat:
            why = _glob_escape(root, Path(os.path.expanduser(ti.get("path") or str(root))), pat)
            if why:
                return deny("Glob pattern %s; patterns must stay inside the current directory" % why)
        return {}

    async def hook(input_data, tool_use_id, context):
        try:
            return check(input_data)
        except Exception as exc:  # fail closed
            return deny("path check failed (%s); denied" % type(exc).__name__)
    return hook


def options_for(work, model, confine=True):
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
    s = scrub(v if isinstance(v, str) else json.dumps(v, default=str))   # scrub before cutting: no secret prefix
    return s if len(s) <= n else s[:n] + "...(%d chars)" % len(s)


class Session:
    """One Claude conversation in a work dir; ask() continues the same conversation."""

    def __init__(self, work, purpose, trace, client_factory=None, model=None, confine=True):
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
