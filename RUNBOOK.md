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

Put each brief, with its own API file, in its own folder: `final/<name>/brief*.md` plus `final/<name>/openapi.json`. A raw-material brief means every file in the folder.

For each brief, run:

```
uv run python -m builder.main final/<name>/ --slot final-<name> --openapi final/<name>/openapi.json \
    > results/final-<name>.log 2>&1
```

- **Always pass `--openapi`.** The builder fails loudly when it finds no API file, or more than one, next to the briefs. It never falls back to the kit's own file.
- **Always pass `--slot`.** The default slot name contains a path hash, which is unreadable in the dashboard and in the submission.
- When Brief B builds on Brief A, pass both: `final/a/ final/b/`, the way `briefs/brief-a.md briefs/brief-b.md` was passed for the practice briefs.
- Run the briefs one after the other. Each run uses the one simulation slot for its self-test.
- Exit 0 means exported, 1 means a spec existed but the final step failed, and 2 means there was no spec (or no API file). On 1 or 2, read the `.md` trace, fix only a crash, and re-run.

## After each build

1. Read `traces/<UTC>-final-<name>.md`. It must show the start and end time, every model call, the workflow and agent IDs, the self-test result, and the export path.
2. The exported workflow is at `submission/final-<name>.workflow.json`, and the spec next to it is `final-<name>.spec.json`.
3. Secret scan. Both commands must print nothing (rg exits 1). The first matches only real secret shapes: a key after
   `fak_` or `sk-ant-`, a raw `FREYA_*=value` assignment, a tunnel URL. It does not match the trace's
   `{"kind": "tunnel", "url_kind": "tunnel"}` event or a `[REDACTED:FREYA_API_KEY]` marker. The second looks for this
   shell's own secret values (`:?` stops it if one is unset, because an empty pattern matches every line).
   ```
   rg -n 'fak_[A-Za-z0-9]{8}|sk-ant-[A-Za-z0-9]|FREYA_[A-Z_]+=[A-Za-z0-9_-]{8}|https://[a-z0-9-]+\.trycloudflare\.com' traces/ submission/
   rg -nF -e "${FREYA_API_KEY:?}" -e "${FREYA_BANK_TEAM_KEY:?}" traces/ submission/
   ```
   Any line printed is a leak. Do not submit that file; tell Hazar.
4. Check that the exported JSON has no tunnel URL and does carry the `servers[0]` host of that brief's API file. The builder asserts this, so this step is only a manual double check.

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
