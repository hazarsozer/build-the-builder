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

- Freya's code sandbox ("snekbox") can go down. In that case a whole run fails, and those runs are discarded.
- The judge reads literally:
  - No `record*` before a successful create_promise. The opening uses "monitored and stored".
  - "Are you a robot?" must never transfer the call.
  - Never restate an amount already offered.
- The organisers' mock bank seeds only the 13 PUB case IDs. Self-tests for any other brief run on our mock (`bank/`) through a Cloudflare quick tunnel. The exported workflow must use openapi `servers[0]`, never the tunnel URL.

## Status at usage limit (Sat ~late evening)
See ledger tail. Next: (1) read results/pubours-t5r5-*.log → merge Task 5 if ≥ gate; (2) Opus review Task 8 (named risks in ledger) → merge; (3) dispatch Task 7b generalisation (field mapping, required tool fields, response aliases, note→close map, disclosure slot, Task 5 residual PIN rule) + bank generalisation; (4) bench/run.py + bench over 10 corpus briefs; (5) AFTER-PLAN.md items.
