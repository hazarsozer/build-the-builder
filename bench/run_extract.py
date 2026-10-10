"""Run the extractor live and grade the result (development gate; costs model time).

    python -m bench.run_extract --ref specs/reference/spec-a.json briefs/brief-a.md
    python -m bench.run_extract --openapi bench/raw-a/openapi.json bench/raw-a
    flags: --single (no dual), --no-oracle, --oracle-only SPEC (run the oracle and check SPEC)

Writes results/extract/<stamp>/{spec.json,info.json,trace.jsonl}. Trace lines are scrubbed.
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from builder import sdk
from builder.extract import extract_async
from builder.oracle import check, oracle_async
from builder.redact import scrub
from bench.grade_spec import grade

ROOT = Path(__file__).resolve().parent.parent


class JsonlTrace:
    """Minimal trace for dev runs; Task 8 provides the real one."""

    def __init__(self, path):
        self.f = open(path, "w")
        self.t0 = time.monotonic()

    def _w(self, obj):
        line = scrub(json.dumps(obj, default=str))
        self.f.write(line + "\n")
        self.f.flush()
        print(line[:220], flush=True)

    def event(self, kind, **fields):
        self._w({"t": round(time.monotonic() - self.t0, 1), "kind": kind, **fields})

    def model_call(self, model, purpose):
        self._w({"t": round(time.monotonic() - self.t0, 1), "kind": "model_call", "model": model, "purpose": purpose})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("briefs", nargs="+")
    ap.add_argument("--openapi", default=str(ROOT / "tools/openapi.json"))
    ap.add_argument("--ref")
    ap.add_argument("--single", action="store_true")
    ap.add_argument("--no-oracle", action="store_true")
    ap.add_argument("--oracle-only")
    a = ap.parse_args()
    out = ROOT / "results" / "extract" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    trace = JsonlTrace(out / "trace.jsonl")
    openapi = json.load(open(a.openapi))
    if a.oracle_only:
        scen, info = asyncio.run(oracle_async(a.briefs, openapi, trace))
        spec = json.load(open(a.oracle_only))
        mism = check(spec, scen)
        (out / "scenarios.json").write_text(json.dumps(scen, indent=1))
        (out / "mismatches.json").write_text(json.dumps(mism, indent=1))
        print(json.dumps({"scenarios": len(scen), "mismatches": mism, "info": info}, indent=1, default=str))
        return
    t0 = time.monotonic()
    spec, errs, info = asyncio.run(extract_async(a.briefs, openapi, trace, dual=not a.single,
                                                 use_oracle=not a.no_oracle))
    info["wall_s"] = round(time.monotonic() - t0, 1)
    (out / "spec.json").write_text(json.dumps(spec, indent=1))
    (out / "info.json").write_text(scrub(json.dumps(info, indent=1, default=str)))
    print("\n=== %s: %.0f s, $%.2f, validate errors %d" % (out, info["wall_s"], info.get("cost_usd") or 0, len(errs)))
    for e in errs[:20]:
        print("  ERR", e)
    print("diff paths:", info.get("diff_paths"))
    print("open diffs:", info.get("open_diffs"))
    print("oracle mismatches:", [(m["scenario"], m["field"]) for m in info.get("oracle_mismatches", [])])
    print("settle decisions:", [(d["path"], d["value"]) for d in info.get("settle_decisions", [])])
    print("rejected:", [r["scenario"] for r in info.get("rejected_scenarios", [])])
    print("conflicts:", json.dumps((spec or {}).get("conflicts"), indent=1)[:2000])
    if a.ref and spec:
        g = grade(spec, json.load(open(a.ref)))
        (out / "grade.json").write_text(json.dumps(g, indent=1, default=str))
        print("graded differences: %d" % len(g))
        for label, got, want in g:
            print("  %-32s got  %s\n  %-32s want %s" % (label, json.dumps(got), "", json.dumps(want)))
    sys.exit(0 if spec is not None and not errs else 1)


if __name__ == "__main__":
    main()
