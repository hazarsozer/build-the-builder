# Orchestrator handoff

Read this first if you are a new session taking over the builder. Hazar may be following over Remote Control.

## The goal

First place in the Freya × YC "Build the Builder" hackathon, which comes with a YC interview.

| When (Istanbul) | What |
|---|---|
| Sun 20:00 | Hidden briefs arrive |
| Sun 23:00 | Submission deadline |

**How cases are scored:**
- There are 60 hidden cases, each run 3 times. A case passes only if all 3 runs pass.
- A run passes on three checks: the bank records are correct, the judge finds no policy breach, and the judge finds no honesty breach.

**Tie-breaks, in order:**
1. Total promised amount on passed cases.
2. Build time.
3. Submission time.

Background: `~/Dev/docs/freya-yc-hackathon/KIT-ANALYSIS.md`, `ARXIV-2609.04611.md`, `raw/kickoff-transcript.txt`.

## Where things are

- **Repo:** `~/Dev/freya-builder-kit/build-the-builder`, branch `builder`. It has no remote, and nothing gets pushed without Hazar.
- **Spec:** `docs/superpowers/specs/2026-10-10-spec-generator-builder-design.md`.
- **Plan:** `docs/superpowers/plans/2026-10-10-spec-generator-builder.md` (tasks 1–11).
- **What comes after:** `docs/superpowers/plans/AFTER-PLAN.md`.
- **Ledger:** `.superpowers/sdd/2026-10-10-spec-generator-builder/progress.md`. This is the source of truth for progress and every `Ruling:`. Trust it and `git log` over memory.
- **Safe baseline:** tag `gate-green-1`. PUB-A scored 10/10 and PUB-B 3/3, all at 3 reps.

## How the work runs

- **Execution model:** superpowers:subagent-driven-development.
  - One implementer per task, in an isolated worktree.
  - Then a first-pass review on **Opus**.
  - Fix rounds resume the same implementer. Scoped re-reviews may use Sonnet.
- **Reports:** worktree agents can't write outside their worktree. Copy `task-N-report.md` from the worktree into the main checkout's sdd folder before building the review package.
- **Simulation slot:** only one Freya simulation runs at a time per workspace. The controller owns the slot and runs every live gate itself. Implementers get it only when explicitly told.
- **Merge bar:** before merging anything that touches compile/multi/nodes, re-gate with PUB-A at `--reps 3` (must be 10/10) and PUB-B at `--reps 3` (must be 3/3).
- **Secrets:** run `set -a; source env.sh; set +a`. Never print env.sh or `FREYA_*`.

## Agent slots

Recorded in `.freya-agents.json` (gitignored):

| Slot | Agent ID |
|---|---|
| ours-A | b25de6d8-d9f9-4897-ab42-d2962e55dd9c |
| ours-B | 7984a7cf-4c00-4ecd-b9da-73edae68fd8c |
| ours-A-selftest | b3e2c5b6-c732-4ec7-84b1-145ba74175fd |
| conf-ours | (unused now) |

Reuse slots. The workspace limit is 200 workflows.

## Never without Hazar

- Pushing or making the repo public.
- Deleting anything outside the scratch areas.
- Changing `AGENT_LLM`.
- Editing a Freya agent by hand.
- Anything costing more than about $15 in Freya credits in one go. The balance is about $99 and a 10-case run costs about $0.31.

## Known platform facts

- Cancel a stuck simulation: POST /api/v2/simulations/{id}/cancel (works; not in kit docs). List: GET /api/v2/simulations.
- Freya's code sandbox ("snekbox") can go down. In that case a whole run fails, and those runs are discarded.
- The judge reads literally:
  - No `record*` before a successful create_promise. The opening uses "monitored and stored".
  - "Are you a robot?" must never transfer the call.
  - Never restate an amount already offered.
- The organisers' mock bank seeds only the 13 PUB case IDs. Self-tests for any other brief run on our mock (`bank/`) through a Cloudflare quick tunnel. The exported workflow must use openapi `servers[0]`, never the tunnel URL.

## Successor orchestrator brief (written Sun ~02:30 by dev-14)

You are the successor orchestrator. dev-14 stays open as the **reporter**: it writes REPORT.md for Hazar and discusses it with him in the morning. It does not start builds. Hazar is asleep until morning, so make every decision yourself as a logged `Ruling:` in the ledger. Message dev-14 (SendMessage) with one line at each milestone below, so the report stays current.

**State:** all original-plan code tasks are merged on `builder` @ HEAD (suite: 451 passed). Tag `gate-green-1` is the last perfect live gate (PUB-A 10/10, PUB-B 3/3, all at 3 reps). The final Task 5 code also gave PUB-A 10/10 at 3 reps.

**In flight when you start:**
- (a) Background shell gate in the main checkout: PUB-B at 3 reps, then A-pieces at 3 reps, then B-pieces at 3 reps on agents ours-A/ours-B (Task 5 code). It holds the simulation slot. Logs: `results/t5r5-{B,A-pieces,B-pieces}-reps3.log`. Wait for it; don't start a simulation before it finishes. Check with `GET /api/v2/simulations`.
- (b) The final whole-branch review (Opus subagent of dev-14). dev-14 relays its findings to you. Fix the Critical/Important ones via subagent-driven-development before Sunday 16:00.

**Your queue, in order:**
1. **Merge-bar re-gate of the merged builder** (7b + 8 changed code nodes). Recompile spec-a and spec-b with HEAD into ours-A/ours-B, then PUB-A and PUB-B at 3 reps. Bar: 10/10 and 3/3. On a miss, diagnose from transcripts (`freya.get_call`) and fix generically.
2. **Dress rehearsals:**
   - `uv run python -m builder.main briefs/brief-a.md --slot dress-A`, timed (target ≤ 15 min), then run_cases dress-A brief A at 3 reps.
   - The same with `briefs/brief-a.md briefs/brief-b.md --slot dress-B`, brief B.
   - Check each trace: start/end, model calls, IDs, tests, no secrets.
   - Milestone message to dev-14.
3. **Bench:** `uv run python bench/run.py ours` (10 briefs; plan2 is extraction-only; about 1.5 h; run it in the background). Classify failures as extraction / compile / case bug and fix generically. Milestone message.
4. **AFTER-PLAN.md:**
   - styles at 3 reps (all 5 × A, B; pieces is already being gated);
   - different "today" dates (§3);
   - variance at 5 reps (§5);
   - judge-wording audit (§6);
   - build-time cuts (§7);
   - outage resilience (§8);
   - submission readiness (§9).
5. **Fallback gate (Task 10):** `fallback/agent_builder.py briefs/brief-a.md` and PUB-A at 3 reps, for the Sunday 16:00 comparison (Task 11). The 16:00 decision itself goes to Hazar.

**Rules:** see "How the work runs" above. Before going idle, set your own heartbeat with CronCreate, every 20 min (minutes 13,33,53), idle-only. Its prompt: read the ledger tail and this file, then continue the queue. That way you resume after a usage-limit reset. Never push. Never cancel a simulation you didn't start. Secrets never printed.

Known platform facts and agent IDs are above. Results: `results/` (gitignored). Rulings so far: in the ledger.

## Orchestrator-3 brief (written Sun ~11:50 by orchestrator-2, session dev-2d)

**State:** `builder` @ 1167f06, tag **gate-green-5** (1019 tests). Since gate-green-1 the builder gained: final-review fixes,
outage resilience, multi-product rework (set_product visible + parallel-safe), single-product dates in code (check_date,
fail-closed create_promise), generalisation (outcome names, identity limits, nth business day, account events, verbatim
openings, banned-word reconcile, never_say), talkative/fragment robustness, close_call guard, dashboard layout + labels.
Gates on gate-green-4 and gate-green-5: PUB-A 10/10, PUB-B 3/3 at 3 reps. Styles (3 reps, pre-A21): A pieces/corrects/in_words
10/10, asks_back 9/10, talkative 6/10 (A21 targets it, not re-measured); B 2/3–3/3. Fallback (fallback/agent_builder.py, brief A):
built in 912 s, PUB-A 10/10 at 3 reps (agent ed5aa40a, slot brief-a). Every decision: ledger `Ruling:` lines.

**In flight when you start:** `bench/run.py ours` on the 10 briefs that failed on the Claude spend limit (log
`results/bench-ours-g5.log`, ~until 12:45). Main checkout is frozen while it runs (builds import the working tree).

**Queue:**
1. When the bench ends: `git merge 7c4ebb5` into builder (A22 polling retry, reviewed Opus-approved + docs cleanup/README;
   compile output unchanged → no live gate needed), run `uv run pytest -q` (expect 1028), then re-run the lost brief:
   `uv run python bench/run.py ours fields`. Earlier bench (pre-fixes): 61/80; g4 partial: bands3 8/8, calendar 7/8.
2. 16:00 decision input (Task 11): `uv run python bench/run.py fallback <4 briefs, e.g. insurance ids sme outcomes>` and
   compare with ours on the same briefs + PUB-A/B. Present the table to Hazar; he decides.
3. Freeze: `git tag final-candidate` on the chosen commit; only crash fixes after.
4. 20:00–23:00: RUNBOOK.md, step by step. Always pass `--openapi` and `--slot`.

**Watch-outs:** Claude spend limit stopped work twice (builder extraction uses it — check it works before 20:00). One simulation
at a time; never cancel one you didn't start (ours are in `.freya-sims.json`). Gates compile into ours-A/ours-B from a detached
snapshot worktree (`results/regate.sh <tag> <worktree>`). Never `pgrep -f` a string that also appears later in your own command.
Open for Hazar: Turkish STT/TTS defaults in agent_config (left as platform default); HANDOFF.md/REPORT.md are internal notes —
remove or keep before making the repo public; workspace holds many bench/probe workflows (deleting needs his OK).
