"""Spec-free lint of a workflow file: python3 fallback/lint_cli.py out/<slot>.workflow.json

Runs builder.lint.lint with an empty spec and drops the one check that needs a spec (the outcome table).
Prints one problem per line, or OK. Exit 1 if any line does not start with "warning".
"""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from builder.lint import lint


def problems(workflow):
    return [e for e in lint(workflow, {}) if "spec outcome table" not in e]


if __name__ == "__main__":
    errs = problems(json.load(open(sys.argv[1])))
    print("\n".join(errs) or "OK")
    sys.exit(any(not e.startswith("warning") for e in errs))
