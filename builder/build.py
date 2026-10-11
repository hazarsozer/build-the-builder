"""The organisers' run command: build one final case and export a workflow that calls our own mock.

    python3 builder/build.py {case_folder} {mock_url}

  case_folder  the case's folder: brief file(s) plus one OpenAPI document (openapi.json, or the only .json with
               `openapi` and `paths`; none or several is an error, exit 2). Every other file in it is a brief.
  mock_url     our mock, https only, never trycloudflare. The exported workflow's tools call <mock_url>/v1/<tool>:
               the path of the OpenAPI servers[0] URL (e.g. /v1) is appended unless mock_url already ends with it.

Runs builder.main's normal flow (extract, self-test against our local bank through the tunnel, final export) as
    builder.main <case_folder> --openapi <found> --slot final-<folder name> --mock-url <mock_url>
Exit codes are builder.main's (0 exported, 1 final step failed, 2 no spec / no OpenAPI / bad arguments).

Plain python3: when the dependencies (claude_agent_sdk, jsonschema) are not importable, this script re-execs itself
with the repo's .venv/bin/python, else with `uv run --project <repo> python`, else it exits 2 saying so. The env
(FREYA_*, model keys) must already be exported, as for builder.main.
"""
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REEXEC = "BUILDER_BUILD_REEXEC"

# Run as a script, sys.path[0] is builder/, whose modules (trace, compile, ...) would shadow the stdlib's.
_here = str(Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if (Path(p).resolve() if p else Path.cwd()) != Path(_here)]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _deps_ok():
    try:
        import claude_agent_sdk  # noqa: F401
        import jsonschema  # noqa: F401
        return True
    except ImportError:
        return False


def _reexec():
    """Re-run this script in the project environment (once: REEXEC guards against a loop)."""
    if os.environ.get(REEXEC):
        sys.exit("builder/build.py: dependencies missing even in the project environment; run `uv sync` in %s" % ROOT)
    env = dict(os.environ, **{REEXEC: "1"})
    script = str(Path(__file__).resolve())
    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        os.execve(str(venv_py), [str(venv_py), script, *sys.argv[1:]], env)
    uv = shutil.which("uv") or str(Path.home() / ".local/share/mise/shims/uv")
    if Path(uv).exists():
        os.execve(uv, [uv, "run", "--project", str(ROOT), "python", script, *sys.argv[1:]], env)
    sys.exit("builder/build.py: dependencies missing and neither %s nor uv was found" % venv_py)


if __name__ == "__main__" and not _deps_ok():
    _reexec()

import argparse  # noqa: E402


def slot_for(case_folder):
    """final-<folder name>, lower-case, runs of other characters as one '-', at most 40 characters."""
    name = Path(str(case_folder).rstrip("/")).name
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return "final-" + (base or "case")


def parse(argv):
    ap = argparse.ArgumentParser(prog="python3 builder/build.py",
                                 description="Build one final case; the exported workflow calls mock_url.")
    ap.add_argument("case_folder", help="the case folder: brief file(s) plus one OpenAPI .json")
    ap.add_argument("mock_url", help="our mock, https://...; tools are called at <mock_url>/v1/<tool>")
    return ap.parse_args(argv)


def main_argv(argv):
    """The builder.main argv for `build.py case_folder mock_url`; exits 2 on a bad folder, OpenAPI or mock URL."""
    from builder import main as M
    a = parse(argv)
    folder = Path(a.case_folder)
    if not folder.is_dir():
        print("builder/build.py: not a folder: %s" % folder, file=sys.stderr)
        raise SystemExit(2)
    try:
        mock = M.check_mock_url(a.mock_url)
    except ValueError as e:
        print("builder/build.py: %s" % e, file=sys.stderr)
        raise SystemExit(2)
    try:
        oa = M.resolve_openapi([str(folder)], None)
    except M.OpenapiNotFound as e:
        print("builder/build.py: %s" % e, file=sys.stderr)
        raise SystemExit(2)
    return [str(folder), "--openapi", str(Path(oa).resolve()), "--slot", slot_for(folder), "--mock-url", mock]


def run(argv):
    from builder import main as M
    return M.main(main_argv(argv))


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
