# Sunday runbook: hidden briefs, 20:00 to 23:00 (Istanbul)

The rules that bind this run (kit README, "The final" and "Rules"):
- One unattended builder run per final brief, at most 30 minutes, with no manual step. We may re-run and submit any one run, together with that run's trace.
- After 20:00, only crash fixes go into the builder. Nothing from a final brief may go in: no rule, no value, no name.
- Never edit a phone agent by hand, and never change `AGENT_LLM`.
- Submit three things per brief: the exported workflow (`<brief>.workflow.json`), the repo commit, and one trace.

## Before 20:00

1. `git status` must be clean on `builder`, and `git log -1` must be the tagged `final-candidate`. The tag does not exist until freeze: create it then with `git tag final-candidate` on the frozen commit (until then the latest tag is `gate-green-4`).
2. Run `set -a; source env.sh; set +a`. Never print `env.sh` or any `FREYA_*` value.
3. Check that the simulation slot is free: `uv run python -c "import freya; print([s['status'] for s in freya.list_simulations()[:5]])"`.
4. Check that `uv run pytest -q` passes.
5. Check the Freya credit balance. It needs at least $10 left.

## 20:00: briefs arrive

The organisers' run command (submission page) is `python3 builder/build.py {case_folder} {mock_url}`. In the final the
phone agent calls only our own mock, so the exported workflow's tools must point at our mock, not at the API file's
`servers[0]`. `builder/build.py` does that.

Put each case in its own folder: `final/<name>/brief*.md` plus its API file (`openapi.json`, or the only `.json` with
`openapi` and `paths`). A raw-material brief means every file in the folder.

For each case, from the repo root, run:

```
python3 builder/build.py final/<name>/ https://<our-mock>.<our-subdomain>.workers.dev > results/final-<name>.log 2>&1
```

- It finds the API file in the folder (none or several: exit 2, nothing built), names the slot `final-<name>`, and runs
  the normal builder flow: `builder.main final/<name>/ --openapi <found> --slot final-<name> --mock-url <mock_url>`.
- **Mock URL joining.** Tools are called at `<mock_url>/v1/<tool>`: the path of the API file's `servers[0]` (`/v1`) is
  appended unless the mock URL already ends with it. `https://m.workers.dev` and `https://m.workers.dev/v1` give the same
  result. The mock URL must be `https`, never a trycloudflare tunnel, with no query or fragment (exit 2 otherwise).
- The self-test is unchanged: it still runs against our local bank through the tunnel. Only the final, exported
  workflow uses the mock URL. The trace records it (`start.mock_url`, and `saved` with `url_kind: "mock"` and `base_url`).
- Plain `python3` works: when the dependencies are missing, `build.py` re-execs itself with `.venv/bin/python`, else
  with `uv run --project <repo> python`. The env must already be sourced (`set -a; source env.sh; set +a`).
- When Brief B builds on Brief A, `build.py` takes one folder only: use `builder.main` directly with both folders,
  `--openapi`, `--slot final-<name>` and `--mock-url <mock_url>`.
- Run the cases one after the other. Each run uses the one simulation slot for its self-test.
- Exit 0 means exported, 1 means a spec existed but the final step failed, and 2 means there was no spec, no API file
  or a bad argument. On 1 or 2, read the `.md` trace, fix only a crash, and re-run.
- `submission.example.json` is the shape of `submission.json` (placeholders only).

## After each build

1. Read `traces/<UTC>-final-<name>.md`. It must show the start and end time, every model call, the workflow and agent IDs, the self-test result, and the export path.
2. The exported workflow is at `submission/final-<name>.workflow.json`, and the spec next to it is `final-<name>.spec.json`.
3. Secret scan. Expected output of both commands: no file names. Both print file names only, never the matching
   line, so a leak is never echoed to the terminal.
   - The first command matches only real secret shapes: a key after `fak_` or `sk-ant-`, a raw `FREYA_*=value`
     assignment, a tunnel URL. It does not match the trace's `{"kind": "tunnel", "url_kind": "tunnel"}` event or a
     `[REDACTED:FREYA_API_KEY]` marker.
   - The second command reads this shell's secret values from the environment, so no value is typed on the command
     line. These are `FREYA_*` and `*_KEY`/`*_TOKEN`/`*_SECRET`/`*_PASSWORD`, as `builder/redact.py` defines them, of 8
     characters or more. It prints only the names of files that contain one of them. If no such variable is set, it
     says so on stderr and exits 1: source `env.sh` first.
   ```
   rg -l 'fak_[A-Za-z0-9]{8}|sk-ant-[A-Za-z0-9]|FREYA_[A-Z_]+=[A-Za-z0-9_-]{8}|https://[a-z0-9-]+\.trycloudflare\.com' traces/ submission/
   python3 -c 'import os,sys,pathlib as P; v=[x for k,x in os.environ.items() if (k.startswith("FREYA_") or k.endswith(("_KEY","_TOKEN","_SECRET","_PASSWORD"))) and len(x)>=8]; v or sys.exit("no secret variables set: source env.sh first"); [print(f) for d in ("traces","submission") for f in sorted(P.Path(d).rglob("*")) if f.is_file() and any(x in f.read_text(errors="ignore") for x in v)]'
   ```
   Any file name printed is a leak. Do not submit that file; tell Hazar. Do not open the file in the terminal.
4. Check that the exported JSON has no tunnel URL, carries our mock host, and no longer carries the `servers[0]` host of that brief's API file. The builder asserts this, so this step is only a manual double check.

## Submission (by 23:00, earlier is better: it is the third tie-break)

- Commit (no brief content in code): `git add submission/ traces/` only if the organisers want them in the repo. Otherwise attach them to the form.
- Pushing or making the repo public is Hazar's call. The form asks for a repo URL and a commit.
- One trace per final brief: the `.jsonl` plus its `.md` summary.

## If something breaks

| Symptom | Action |
|---|---|
| A simulation hangs and blocks the slot | It is one of ours only if its id is in `.freya-sims.json`. Cancel it with `POST /api/v2/simulations/{id}/cancel` (`freya.cancel_simulation`). Never cancel one we did not start. |
| The code sandbox ("snekbox") is down: every call fails | Wait, and re-run the self-test later. The builder still exports the best spec it has. |
| The tunnel won't come up | The builder retries 3 times, then skips the self-test and exports the round-0 spec. |
| The official mock bank returns 500 | Self-tests run on our own bank (`bank/`), so only `run_cases` against PUB cases is affected. |
