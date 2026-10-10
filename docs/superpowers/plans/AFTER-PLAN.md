# After the plan: what to run until Sunday 16:00

Written Sat 2026-10-10, ~19:30. This list starts when tasks 1–11 of `2026-10-10-spec-generator-builder.md` are merged, or earlier where a line says so. It is ordered by points at risk per minute of the simulation slot.

## Rules for every line

- **One simulation at a time.** Freya allows one per workspace. Never start a second `run_cases`/selftest while one is running.
- **The bar is a live gate.** A change merges only if PUB-A 3 reps is 10/10 and PUB-B 3 reps is 3/3, on the same command as the `gate-green-1` tag. Anything worse is reverted.
- **No brief-specific values in code.** Fix the compiler, schema, extractor prompt or case generator. Never add values that only fit one brief.
- **Log every result.** Every run's summary goes to `results/` (gitignored) and gets a one-line entry in the SDD ledger.

## 1. Full dress rehearsals (needs Task 8)

```
uv run python -m builder.main briefs/brief-a.md --slot dress-A
uv run python -m builder.main briefs/brief-a.md briefs/brief-b.md --slot dress-B
uv run python -m builder.main bench/raw-a/* --slot dress-raw
```

- Time each build. The target is ≤ 15 min, and 27 min is a hard limit.
- Then run `run_cases --reps 3` on dress-A (brief A) and dress-B (brief B).
- For dress-raw, use selftest on our bank with `bench/raw-a/cases.json`.
- **Pass:** results equal to the gate-green-1 scores, and every trace contains start/end, model calls, IDs and test results.

## 2. Customer styles (can start now, on ours-A / ours-B)

```
for s in pieces corrects talkative asks_back in_words; do
  python3 tools/run_cases.py <ours-A> --brief A --reps 3 --style $s
  python3 tools/run_cases.py <ours-B> --brief B --reps 3 --style $s
done
```

- **Pass:** 10/10 and 3/3 for every style.
- For each failure, classify it as agent, judge or platform.
- Fix agent failures generically, for example spoken-number parsing in VERIFY_PRE or a date-word prompt.

## 3. Different "today" dates (needs only selftest)

- Run selftest on our bank with the spec-a and spec-b cases, using these `now` values:
  - a Friday, `2026-10-16T11:00`, for the "this/next Friday" and last-business-day rollover;
  - the day before a holiday, `2026-10-23T11:00`;
  - after hours, `2026-10-14T19:30`, where a callback is not possible today;
  - month end, `2026-10-30T11:00`;
  - late December, `2026-12-23T11:00`, for the holiday cluster.
- **Pass:** every case passes.
- Expected values come from `lib`. If a case and the agent disagree, check the brief by hand before changing anything.

## 4. Mutated briefs (Task 9, the long run, best started before sleep)

- Build the corpus first (subagents), with each `cases.json` reviewed.
- Then run `bench/run.py ours` in the background.
- **Pass:** for each brief, a clean build ≤ 15 min and a selftest pass rate within 1 case of the A/B results.
- Classify each failure:
  - extraction miss → extractor prompt or schema;
  - compile gap → compiler;
  - case bug → cases.

## 5. Variance

- Run PUB-A with `--reps 5` on the final candidate.
- Any case below 5/5 is a reliability bug. Find the prompt-dependent step and move it into code or routing.

## 6. Judge-wording audit (no slot needed)

- Scan every passing transcript in `results/*.json` (via `freya.get_call`). Flag:
  - any `record*` before a successful create_promise;
  - promises of outcomes ("guarantee", "will be removed", "no fees");
  - reply openings repeated 3 or more times;
  - amounts restated after the first offer;
  - the word "debt" or the product before verification.

## 7. Tie-breakers

- **Build time.** Skip round 2 when round 1 passes everything. Start the dual extractions in parallel. Overlap compile and save with the tunnel start-up.
- **Promised amount.** Confirm in transcripts that the first ask is always the full amount and that the floor appears only after "too much".

## 8. Outage resilience

- Simulate a code-node failure with a test that sets `vars` without the computed keys. Check that the conversation still reaches `close_call` with a valid outcome (e.g. `no_promise/service_error`) and never hangs.

## 9. Submission readiness (Sunday morning)

- `freya.export_workflow` round trip: export, re-import into a scratch slot, one chat turn.
- Run `rg -n 'fak_|sk-ant|FREYA_|trycloudflare' traces/ out/ submission/`. It must find nothing.
- Write `RUNBOOK.md` for 20:00–23:00.
- Draft a public README: architecture, how to run, trace location. **Push only on Hazar's go.**
- Apply the organisers' submission-steps mail when it arrives.

## Sunday 16:00 decision (Task 11)

- Run `bench/run.py ours` and `bench/run.py fallback` on the same 4 briefs, plus PUB-A and PUB-B at 3 reps.
- Submit the builder with more passes; on a tie, the faster one.
- Then freeze: tag `final-candidate`. From then on only crash fixes go in.

## Hidden-brief hypothesis matrix (drives Task 9 corpus and extra cases)

Evidence key: **K** = kit README/docs/code, **T** = kickoff transcript, **P** = τ^τ-bench paper, **I** = our inference.

| # | Hypothesis about the hidden briefs | Evidence | Covered by | Status |
|---|---|---|---|---|
| H1 | One brief is raw material: a mail, a policy extract, a memo that changes the policy (possibly unlabelled), a code list, and decoy documents | K, P | bench/raw-a (memo changes a band); add **raw-decoy**: a decoy mail that uses policy words but decides nothing, plus an unlabelled later mail that overrides | todo |
| H2 | Identity questions change: postcode, birth year, company number, founded year; two or three questions; a per-question wrong-answer limit | K (`run_cases.persona_for` knows these fields) | bench/ids; bench/sme | todo |
| H3 | A business (SME) or insurance customer: product names, floor fields and words all change | T ("banking or insurance case"), K | bench/sme (business overdraft), **bench/insurance** (overdue premium, floor = minimum instalment, policy number spoken) | todo |
| H4 | Tool fields and enums change: new or renamed close outcomes and reasons, transfer queues, add_note reasons, extra required fields | K ("fields and allowed values can change") | bench/outcomes; **bench/fields**: one tool takes an extra required enum field, e.g. create_promise.channel | todo |
| H5 | A different calendar: other business weekdays, many holidays, deadlines as calendar days or end of month, snapped back | K | bench/calendar | todo |
| H6 | More bands with different ladders (1/2/3 attempts), floor-first vs full-first | I | bench/bands3 | todo |
| H7 | New situations with their own outcome (bankruptcy, legal, complaint, vulnerable customer, wants it in writing, debt sold) | I, K ("another outcome table") | bench/outcomes (bankruptcy → transfer legal); generic events tested in cases | partly |
| H8 | Up to 3 products on every final customer, with mixed bands and deadlines and shared dates | K | PUB-B + spec-b cases; **3-product cases at several `now` values** | partly |
| H9 | Customers "on several calendar days": Friday, before holidays, after hours, month end | K | AFTER-PLAN §3 | todo |
| H10 | Human-like speech: pieces, corrections, talkative, asks back, numbers and dates in words | K | AFTER-PLAN §2 (all 5 styles × A, B) | todo |
| H11 | Personas: angry, satisfied, scammer, prompt injection | T | injection case exists; **add angry/abusive** (stay calm, policy close) and **"is this a scam"** cases to the generic set | partly |
| H12 | Tool failures beyond create_promise: get_account 500, verify_identity 500, schedule_callback failure, add_note failure | K (every tool documents a 500), I | faults in our bank: **add cases** for get_account and verify_identity failing once, then twice | todo |
| H13 | A brief that allows something A forbids, e.g. a payment plan of two dates, more than one promise per product, a discount within limits | I | **bench/plan2**: allows a 2-part plan (two create_promise calls for one product) → check whether the schema can express it, or it goes to extra_rules | todo (schema risk) |
| H14 | Required fixed sentences that differ: a legal disclosure in the opening, a mini-Miranda, a data-protection line | I, K ("judge checks wording") | bench/strict-wording | todo |
| H15 | Response-field changes in the bank: e.g. `verified` renamed, wrong_answers_left absent | K, P | post-hook tolerance test (Task 4 test_hooks) + **bench/fields** with renamed response fields | partly |
| H16 | The persona tests the third-party rules: a relative offers to pay or asks for the amount | K (PUB) | spec cases: third-party offers to pay | todo |
| H17 | Judge literalism on new words: "guarantee", "I have noted", promising consequences | P, K | AFTER-PLAN §6 audit | todo |

Rule: each "todo" becomes a corpus brief or a case-generator scenario. Each closes when the full builder passes it at 3 reps on our bank, or when the miss is logged with a ruling.
