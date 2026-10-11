# Builder report: where we stand (Sun 11 Oct, ~02:45)

Written by dev-14 (the first orchestrator) for Hazar. It is kept up to date while the successor session, dev-2d ("builder orchestrator 2"), works overnight.

## 1. The short version

> **Latest (Sun ~06:15):** bench partial 42/56 on unseen test briefs. The weak spot is insurance at 2/8: renamed close outcomes aren't used, and A15 is fixing it. A14 gate: PUB-B 3/3, PUB-A 9/10, the miss being a platform flake. Earlier (~06:00): A12 passed (PUB-A 10/10, PUB-B 3/3). The bench so far, on test briefs the builder has never seen: bands3 8/8, calendar 8/8, fields 7/8. Before that (~04:50): the full builder, run from the brief alone on A+B, produced an agent that scored **PUB-B 3/3 at 3 runs** (build 631 s). Earlier: `builder` is at tag `gate-green-2`. On the practice set, organisers' bank, 3 runs: Brief A 10/10, Brief B 3/3. It now includes every overnight fix. Still to run: dress rehearsals on this version, the bench, and the style checks.

- **The builder exists end to end and is merged.** `python -m builder.main <brief files>` reads a brief and writes a policy spec, compiles a Freya workflow, tests it on our own copy of the bank, fixes the spec, then exports the workflow and writes a trace. Code: branch `builder`, 451 tests passing.
- **Live results on the organisers' bank were perfect on the practice set at the `gate-green-1` tag.** Later checks with pieces-style customers show gaps (see section 10):

  | | Score | Detail |
  |---|---|---|
  | Brief A | 10/10 | every case passed 3 of 3 runs |
  | Brief B | 3/3 | every case passed 3 of 3 runs |
  | Kit's sample builder | 8/10 | its own agent, 3 runs |
  | Kit's base agent | 0/10 | |

- **The extractor reads a brief into a spec with 0 graded differences** against our hand-checked specs. That holds for Brief A and for Brief A+B, 2 of 2 runs each, about 3–4.5 min per run.
- **Not yet proven:** the whole pipeline in a single command (the "dress rehearsal"), and the bench of 10 mutated briefs we built to look like the hidden ones. dev-2d runs both overnight.

## 2. How far we went

| Plan task | What | State |
|---|---|---|
| 1 | Date and money library (sandbox-safe) | ✅ merged |
| 2 | Policy-spec schema, hand-checked specs for A and B, validator | ✅ merged |
| 3 | Our own mock bank and Cloudflare tunnel; conformance with the official bank | ✅ merged |
| 4 | Compiler, single product | ✅ merged |
| 5 | Compiler, several products, plus live-found fixes (wording, robot question, amounts said twice, answers given in pieces) | ✅ merged after 5 fix rounds; the remaining gaps were ruled on |
| 6 | Self-test customers generated from the spec | ✅ merged |
| 7 | Extractor: fact pass, two extractions in parallel, a settle step, a brief oracle | ✅ merged |
| 7b *(new)* | Generalisation: renamed account fields, extra call variables, extra required tool fields, renamed bank responses, a note-reason→close-reason map, a fixed sentence after verification, bank options | ✅ merged |
| 8 | Orchestrator: one command, the trace, secret scrubbing, export, budget stop, the fix round, cancelling our own stuck simulations | ✅ merged after 3 security fix rounds |
| 9 | Bench: 11 mutated briefs (88 cases, each reviewed by hand) and `bench/run.py` | ✅ merged; **bench run pending** (dev-2d) |
| 10 | Fallback builder (the kit sample, hardened) | ✅ merged; **live gate pending** |
| 11 | Sunday 16:00 decision and freeze | ⏳ Sunday, **your call** |

Task 7b was not in either plan. Our test briefs showed that an insurance-style or raw-material hidden brief would have broken the pipeline outright, so it jumped the queue.

## 3. Results in detail

**Live, on the organisers' bank** (a case counts only when it passes all 3 runs):

| Run | Score | Note |
|---|---|---|
| Kit sample builder's agent | 8/10 (A) | the baseline to beat |
| Our compiler, first live check | runs 8, 9, then 5 | run 3 was a Freya sandbox outage; 3 real failures fixed afterwards |
| `gate-green-1` (Task 5 code before its fix rounds) | **A 10/10, B 3/3** | tagged as the safe baseline |
| Task 5 final | **A 10/10** | B and the pieces style are re-running; the modem restart cut them off |

**Customer speaking styles** (1 run each):
- **Before the fixes:** "pieces" (answers given in two parts) scored A 3/10 and B 0/3. The agent never joined the parts.
- **After the fixes**, on our bank: pieces A 9/10, B 3/3. Numbers said in words A 10/10. Self-correcting customers B 3/3.
- **Still to run:** all 5 styles at 3 runs each (dev-2d).

**Extractor:**
- Brief A: 2 of 2 runs with 0 graded field differences.
- Brief A+B: 2 of 2 runs with 0 graded field differences.
- Time per run: 186–267 s. Cost: about $3 per run.
- On the raw-material Brief A (a mail, a policy, an unlabelled later memo and a code list), it took the memo's change and logged the decoy mail as rejected.

**Our mock bank versus the official bank:** 0 differences across 70 ledgers and 315 replayed calls. One live run gave the same records on all 10 practice cases.

**Self-test machinery:** the first live round on our bank scored 9/11 in about 4 minutes. Both failures were real: one a test-wording bug, the other the robot/human bug, since fixed.

**Build time:** extraction plus compile without self-test took 193 s. The full build with self-test is not measured yet; that is the dress rehearsal.

## 4. How the builder works

```
brief files + openapi.json
   │  (tunnel to our mock bank starts in parallel)
   ▼
EXTRACT     2 independent Claude sessions → spec.json each; a settle step resolves differences by quoting the brief;
            a separate "oracle" session (never sees the spec) writes test scenarios from the brief alone
   ▼
VALIDATE    JSON Schema + openapi enums + every brief fact mapped + worked examples recomputed
   ▼
COMPILE     deterministic Python: all dates, amounts, ladders, product loops and close reasons in code nodes;
            the model only talks and extracts. Judge-safe wording rules (no "record" before success, etc.)
   ▼
SELF-TEST   ~10 customers generated from the spec, run on OUR bank through the tunnel (organisers' bank
            seeds only the 13 practice cases)
   ▼
FIX (≤1)    Claude may edit the spec only with a verbatim brief quote; tests are never edited; keep the better spec
   ▼
EXPORT      compile with the official bank URL → save → export_workflow → trace (every line scrubbed of keys)
```

**Why this shape:** a case counts only if all 3 runs pass, so anything left to the model's judgement is a risk. Code makes the same decision every time. The τ^τ-bench paper found the same: the best builders added fixed code guards.

## 5. How we worked

- **Subagent-driven development.** Each task went to a fresh implementer in its own git worktree, then a reviewer: Opus for the first review, Sonnet for narrow re-reviews. Findings went back to the same implementer, for at most 5 rounds, and anything still open after that I ruled on myself.
- **One simulation at a time.** Freya allows one simulation per workspace, so only the orchestrator ran live checks.
- **Merge bar:** a change touching the compiler merges only if Brief A at 3 runs is 10/10 and Brief B at 3 runs is 3/3.
- **The ledger** (`.superpowers/sdd/…/progress.md`) records every task, review, live result and ruling. `HANDOFF.md` and `docs/superpowers/plans/AFTER-PLAN.md` carry the state between sessions.
- **The test corpus** was built from a written list of guesses about the hidden briefs (17 hypotheses with their evidence, in `AFTER-PLAN.md`). An independent reviewer re-derived every expected record by hand.
- **Security:** a background scanner flagged 8 possible issues. Each one was probed; 4 were real and are fixed:
  - fix-round quote bypass
  - path-parser trick
  - unsandboxed sessions
  - a partial key leaking through truncation

## 6. Platform lessons (new facts we found)

- **The judge reads literally.**
  - Any "record…" word in the opening fails the honesty check, even "recorded for quality and security purposes". Ours says "monitored and stored".
  - Restating an amount already offered counts as offering it twice.
  - "Are you a robot?" must never lead to a transfer.
- **The organisers' mock bank went down** (HTTP 500 for hours) and Freya's code sandbox also had an outage. Our own bank kept testing alive.
- **A stuck simulation blocks the workspace.** `POST /api/v2/simulations/{id}/cancel` clears it; this is not in the kit docs. The builder now cancels only simulations it started itself.
- **Freya keeps both the pre-hook's and the post-hook's variable writes.** We probed this; our identity fix depends on it.

## 7. Risks, ranked (what could still cost points on the hidden briefs)

1. **The full pipeline is unproven.** Every part is tested, but extract + compile + self-test + fix + export has not run end to end with self-test yet. This is dev-2d's first item.
2. **Hidden-brief shapes our spec can't express:**
   - a split payment plan (two promises for one product). The official mock rejects this, so it's unlikely.
   - callback windows that differ by weekday
   - a "5th business day" attempt rule
   - a policy-lapse style event

   `extra_rules` (free-text rules) catch these only softly.
3. **Judge literalism on new wording**, such as a required disclosure. Our wording rules cover what we have seen.
4. **Freya or mock-bank outages during the 20:00–23:00 window.** Mitigation: our bank for self-test, the cancel endpoint, and a budget stop that always exports something.
5. **Customer styles at 3 runs.** Pieces was the biggest gap. It is fixed, with 3-run confirmation running now.
6. **Build time.** Probably 8–15 min. It is tie-break 2. Not measured yet.

## 8. Decisions that are yours

- **Sunday 16:00 (Task 11):** submit our builder or the fallback, decided on the same bench. My recommendation: our builder, unless the bench shows it failing whole briefs.
- **Public repo:** the submission needs a repo URL and commit. Nothing has been pushed. It needs your go, a README, and a final secret scan of traces.
- **The organisers' submission-steps mail:** forward it when it arrives.

## 9. Every ruling I made on your behalf

Each line gives what I decided, why, and what it costs if wrong. The full list is also in the ledger.

- redact.py moved to Task 1, because Tasks 8 and 10 both reuse it. Cost if wrong: none.
- `tests/conftest.py` puts the repo root and `tools/` on the import path. Cost: none.
- Parallel implementers were allowed only on separate directories, each in its own worktree, because strict one-at-a-time was too slow for the time left. Cost if wrong: a cheap merge conflict.
- Live simulations are run by the orchestrator, not by implementers, because there is only one slot.
- The reference opening sentence was "…This call is being recorded for quality and security purposes." This was later replaced: the opening now never contains "record*" and says "monitored and stored", because the judge failed two different wordings. Cost if wrong: a hidden check for the exact word "recorded".
- The mock bank takes its allowed close reasons from the spec, and is lenient when none are given. Cost if wrong: a reason the official bank would reject passes self-test, though the scorer still catches it.
- An event allowed at "any" stage means opening, identity and negotiation only, never a third party. Cost if wrong: a hidden brief that wants third-party transfers must say so explicitly.
- Restating rules were pruned from the reference specs. Cost if wrong: a dropped rule; the live gate stayed perfect.
- Live-gate fixes went to the Task 5 implementer, to avoid conflicting edits. Cost: a larger Task 5.
- A repeated reply opening is a warning in self-test, not a failure. Cost if wrong: a phrasing-judge failure class goes uncaught.
- Several-product specs get 11 self-test customers, so the "customer insists on a human" path keeps a test. Cost: one more call per round.
- The fallback builder keeps the keys in its shell; trace scrubbing is the control. Cost if wrong: a key in a channel that isn't scrubbed. Traces are scanned before commit.
- All first reviews run on Opus. Cost: minutes and tokens.
- The read-back of the promise is spoken by the engine, not the model. Cost: a graph change.
- Products are reported through a code tool rather than a dummy bank call. Cost if wrong: extra bank-log lines.
- A customer who declines hardship support gets Brief B's closing rule. Cost if wrong: a judge might read Brief A rule 32 as winning instead.
- Field-name generalisation became Task 7b. Cost: about 1 hour.
- Split payment plans were deprioritised (extraction-only test). Cost if wrong: a split-plan hidden brief fails.
- The extractor waits 90 s for its second extraction. Cost: up to 45 s more build time.
- Identity digits must have exactly the required length; a full phone number makes the agent ask for the last four. Cost: one extra turn.
- A year on its own is not a complete date. Cost: none.
- Answer parts are combined only when they are digits only. Cost: one rare wrong-answer count.
- "Last value wins" applies only with a correction word ("sorry", "I mean"). Cost: one extra turn.
- Years must fall between 1900 and now.
- `freya.py` gained optional parameters, with the defaults unchanged. Cost: it differs from the kit's copy.
- The 4000/4600-character prompt limits are our own budget, not a platform limit.
- Bench builds reuse one slot per brief, which keeps the workflow count bounded.
- Leftover Task 5 findings were adjudicated: two carried into Task 7b and fixed, and one parked as the year range check above.

## 10. Overnight (dev-2d), filled in as results arrive

- [x] 3-run checks on the Task 5 code (before the 7b and 8 merges), on the organisers' bank. **Not perfect.**
  - **Brief B: 2/3.** PUB-12 failed once: in run 3 all 3 products were noted refused, but the expected record was a 510 promise on p1.
  - **Brief A with pieces-style customers: 7/10.** PUB-07 failed 0/3: the customer offers 26 Oct, a holiday, and the agent should offer the next business day. PUB-02 and PUB-04 each failed 1 of 3.
  - **Brief B with pieces-style customers: 1/3.** PUB-12 and PUB-13 each failed 1 of 3.
  - **Diagnosis by dev-2d:**
    - PUB-07: the agent offered Mon 2 Nov instead of the next business day, Tue 27 Oct.
    - PUB-04: one run took 400 instead of 1250.
    - PUB-02: one run failed because the platform returned no analysis.
    - An Opus diagnosis is running.
- [x] All 10 final-review fixes, plus outage resilience (§8), were reviewed clean and combined on branch `candidate-1` @ 58c3477. The suite passes at 538. It is not merged to `builder` until it passes the 3-run gate, which is next.
- [x] Judge-wording audit (§6): no breaches found. Wording polish is queued.
- [x] Re-check of `candidate-1` on the reference specs, 3 runs: **Brief A 8/10, Brief B 3/3.** The merge bar is not met, so nothing has merged yet. PUB-05 hit a sandbox parse-error flake. PUB-06 re-offered the floor amount after a message was cut off.
- [x] Dress rehearsal on Brief A (on `candidate-1`): **build 363 s** (extraction 173 s), trace complete, export clean.
  - Self-test: 8/11.
  - The dress agent on the organisers' bank, 3 runs: **8/10**.
  - PUB-09 failed on the platform, which returned no analysis.
  - PUB-05: the customer's "no" to hardship support was extracted at confidence 0.49 and dropped. The agent then kept persuading and the call ended as refused. Next fix: lock the hardship state in code.
  - Further fixes in progress: A9 (the multi-product tool made visible to the model, attempts counted in code, prompt fixes) and A10 (the hardship lock, plus robustness to the corrects, in_words and asks_back styles).
- [x] **A9 and A10 reviewed clean** (588 tests; the gate run is queued).
  - A9: the multi-product tool is now visible to the model, and attempts are counted in code.
  - A10: a hardship lock routes through the support offer in code. A backup close is added for paid/dispute. A customer who declines but also gives an answer returns to identity once. A date the customer names past the deadline moves to the deadline.
- [x] `RUNBOOK.md` for Sunday 20:00–23:00 is committed on `builder` (submission readiness, part of AFTER-PLAN §9).
- [x] **Gate on A9 (with fixes), organisers' bank, 3 runs:**
  - **Brief A:** 10/10 in run 1 and 10/10 in run 2. Run 3 was lost to the platform: the simulation stuck at "running" for 30 min and was cancelled.
  - **Brief B: 2/3.** Root cause of PUB-12: the model made 3 product calls in parallel. They overwrote each other's state, so one agreed product was lost and later noted as refused. Fix A11 is in progress and makes those updates safe when calls run in parallel.
- [x] **MERGE BAR MET: `builder` @ d77a197, tag `gate-green-2`.** It contains:
  - the final-review fixes
  - outage resilience (A8)
  - multi-product fixes (A9)
  - the hardship lock and style fixes (A10)
  - product updates made safe when calls run in parallel (A11)

  Gate on the reference specs, organisers' bank, 3 runs: **Brief A 10/10, Brief B 3/3**, with 0 sandbox errors. The suite passes at 594. One run was lost to a stuck Freya simulation, which we cancelled.
- [ ] A12, in review: the date verdict for a single product moves into code, through a check_date tool. This fixes PUB-07, where the customer said "Monday the twenty-sixth" and the agent offered 2 Nov.
- [x] **Dress rehearsal on Brief A+B, on `gate-green-2`:** the full builder exported in 631 s, using the extracted spec and the full self-test. **PUB-B on that agent, 3 runs: 3/3.**
- [x] A13, merged (`builder` @ 29369d5, 606 tests): removes a false "account" check that forced an unneeded fix round. This should cut about 3.5 min from each build.
- [x] A12 (date checks for a single product in code; the promise guard fails closed): 5 fix rounds, final review clean.
  - Gate on an earlier A12 build, 3 runs: **PUB-A 10/10, PUB-B 2/3.** The PUB-12 run had the right records, but the judge failed it: the model refused one product without making it any offer.
  - Fix A14 (a refusal counts only after an offer) is in progress.
  - **A12 final gate (a12c), 3 runs: PUB-A 10/10, PUB-B 3/3.** It merges after the bench run.
- [ ] Bench over the test briefs. Partial: **42/56 cases on 7 briefs simulated so far** (1 run each; plan2 checks extraction only). Every build exited cleanly with 0 spec errors, in 170–300 s.

  | Brief | Score | What failed |
  |---|---|---|
  | bands3 | 8/8 | — |
  | calendar | 8/8 | — |
  | fields | 7/8 | judge: the DOB question was not repeated after a wrong answer |
  | ids | 5/8 | the per-question wrong-answer limit closed as declined instead of failed; one callback |
  | **insurance** | **2/8** | the renamed close outcome `promise_to_pay` was never used (the call closed no_promise/refused); the lapsed-policy event had the wrong reason and queue; one deadline was off |
  | multi-raw | 6/8 | a restructuring request and a promise were mixed up in 2 cases |
  | outcomes | 6/8 | the agent said two sympathy sentences instead of one |

  Fix A15 (generalising renamed outcomes and the remaining gaps) is in progress. raw-decoy, sme and strict-wording are still to run.
- [x] Final whole-branch review: **Ready with fixes**. No crash before export; self-test and exported builds are identical apart from the URL; about 770 fuzzed specs crashed nothing; no secret leaks. There is 1 Critical (a brief's API file not named `openapi.json` silently falls back to Brief A's file; on Sunday, always pass `--openapi`) and 9 Important, about 1.5–2 h of fixes. Details are in `.superpowers/sdd/…/final-review.md`, and dev-2d is fixing them before the dress rehearsals.
- [ ] Customer styles at 3 runs, different "today" dates, variance at 5 runs
- [ ] Fallback live check (for the 16:00 decision)
