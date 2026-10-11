"""Write mock-worker/config.json (the Worker's contract + test customers) from a case folder.

    python3 mock-worker/make_config.py <case_folder> [--openapi FILE] [--cases FILE] [--idle-reset-s 600]

Contract: --openapi, else <folder>/openapi.json, else the one .json in the folder with "openapi" and "paths".
Customers: --cases, else the .json files in the folder holding a list of cases (objects with "identity" and
"products", public-test-set.json format; a case may carry "seed" and "customer_id"). Several files are merged.
Redeploy after writing (`pnpm dlx wrangler deploy` in mock-worker/): the config is bundled at deploy time.
"""
import argparse, json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent


def load(p):
    try: return json.loads(pathlib.Path(p).read_text())
    except (OSError, ValueError): return None


def is_cases(d):
    return isinstance(d, list) and d and all(isinstance(c, dict) and "identity" in c and "products" in c for c in d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--openapi")
    ap.add_argument("--cases", action="append")
    ap.add_argument("--idle-reset-s", type=int, default=600)
    ap.add_argument("--out", default=str(HERE / "config.json"))
    a = ap.parse_args()
    folder = pathlib.Path(a.folder)
    files = sorted(folder.rglob("*.json")) if folder.is_dir() else []
    if a.openapi: openapi = load(a.openapi)
    elif (folder / "openapi.json").is_file(): openapi = load(folder / "openapi.json")
    else:
        found = [d for d in map(load, files) if isinstance(d, dict) and "openapi" in d and "paths" in d]
        if len(found) != 1: sys.exit(f"need exactly one OpenAPI file in {folder}, found {len(found)}; pass --openapi")
        openapi = found[0]
    if not isinstance(openapi, dict) or "paths" not in openapi: sys.exit("the openapi file has no paths")
    cases = []
    for p in (a.cases or [f for f in files if is_cases(load(f))]):
        d = load(p)
        if not is_cases(d): sys.exit(f"{p}: not a list of cases with identity and products")
        cases += d
    pathlib.Path(a.out).write_text(json.dumps({"openapi": openapi, "cases": cases, "idle_reset_s": a.idle_reset_s}, indent=1))
    print(f"{a.out}: {len(openapi['paths'])} tools, {len(cases)} cases")


if __name__ == "__main__":
    main()
