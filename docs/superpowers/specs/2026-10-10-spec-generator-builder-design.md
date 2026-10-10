# Spec + generator builder: design

Written 2026-10-10, 17:00 Istanbul. Approved direction: approach 1 (spec + generator), with the improved sample builder as the fallback.

## Goal and success criteria

- **Goal: first place.** That means all 3 reps pass on as many of the 60 hidden cases as possible, on two briefs we have not seen. One of them is raw bank material.
- **Success, measured before Sunday 20:00:**
  1. Brief A and Brief B at `--reps 3`: Brief A 10/10, Brief B 3/3, and no worse with `--style` on.
  2. Our mutated briefs (the fuzz corpus, §6): the builder runs with no manual step and beats the fallback on the same cases.
  3. One build finishes in ≤ 15 minutes. The limit is 30, and build time is the second tie-break.
- **Hard constraints** (kit README):
  - No hidden-brief content goes into code or prompts.
  - No manual step, and no hand edits on Freya.
  - One simulation at a time.
  - `AGENT_LLM` is fixed.
  - Every run leaves a trace: start and end time, each model call (model plus purpose), the IDs it created, and its test results.

## Architecture

```
briefs/*.md + openapi.json
        │
  [1] EXTRACT   LLM (Agent SDK, Opus) ──► policy spec (JSON)
        │        validate: JSON Schema + semantic checks ──► repair loop (≤ 3)
        │        + example vectors: (input → expected) pairs quoted from the brief
  [2] COMPILE   our Python, no LLM ──► workflow.json
        │        library: dates, amounts, product loop, retries, close mapping
        │        lint: banned words, every path ends in close_call, enums ⊆ openapi
  [3] SAVE      freya.save_workflow(slot) ──► agent_id
  [4] SELF-TEST cases made from the spec (§5) ──► run ──► failures
        │        LLM edits the SPEC (never the workflow) ──► back to [2]   (≤ 2 rounds)
  [5] TRACE     traces/<run>.jsonl, keys redacted
```

The key property: **the brief's facts live only in the spec**. The code is generic and tested once, ahead of time. The same spec always compiles to the same workflow, which removes the variance the sample builder had between runs.

## 1. The policy spec (`builder/spec_schema.json`)

These are the dimensions a hidden brief can change, taken from Brief A, Brief B and the README list "numbers and limits, identity questions, outcome table, calendar, tool fields":

| Section | Content |
|---|---|
| `call_variables` | the names and meanings of call variables (e.g. `title`, `last_name`, `now`) |
| `opening` | required facts in the first sentence; words banned before verification; the confirm question; accepted and rejected confirmations; max asks; what happens when it stays unclear |
| `third_party` | what may be said; callback window (days ahead, hours, inclusive); retries; outcomes for wrong number, deceased, no callback, plus fixed sentences |
| `identity` | an ordered list of questions; for each, the openapi `field` value and the answer format (`date` with its day/month order, `digits:n`, `text`); the max number of wrong answers and whether it's counted in total or per question; hints that don't count as wrong (e.g. `day_month_swapped` → re-ask the month as a word); the decline policy |
| `calendar` | the business weekdays and the holiday list |
| `products` | product types; how each is spoken (e.g. a template with `card_last4`); which account field is the **floor** for each |
| `bands` | `days_late` ranges → deadline rule (`nth_business_day`, `calendar_days`, `end_of_month`, …) + number of attempts |
| `negotiation` | first ask; the persuasion ladder (each attempt = amount ∈ {full, floor} × date ∈ {deadline, last business day of week, …}); rules for vague dates; what "next/this weekday" means; how to handle amounts below the floor; banned offers |
| `intents` | for each intent (paid, dispute, restructuring, human, hardship, scam, robot, wants to stop, …): a trigger description, the action (`say`, `transfer:<queue>`, `close`, `note`, `insist_once`), the outcome and reason, and whether it overrides persuasion |
| `confirmation` | the read-back template, the confirm question, retries on tool failure, the required sentence after success, the failure sentence and its outcome |
| `multi_product` | the order products are discussed in; one combined read-back or one per product; the note policy (reason enum, when to note, no notes on transfer); how the close reason is chosen |
| `outcomes` | the full table: event → (outcome, reason), checked against openapi enums |
| `extra_rules` | the **escape hatch**: free-text rules, each tagged with the stage it applies to, injected into that node's prompt; plus `custom_code` (a Python function body with fixed inputs and outputs) for date or amount rules the enums can't express, tested by the example vectors |
| `examples` | concrete pairs from the brief (e.g. "next Friday said on Friday 2026-10-16 → 2026-10-23") used to unit-test the compiled date and amount code |

The extractor must cite the brief line for every value (`source` field). That helps the repair loop and makes the trace easy to read.

## 2. Compiler (`builder/compile.py` + `builder/lib/`)

- The graph is fixed in shape and parameterised: setup → opening → (third_party | identity) → load_account → prepare → negotiate (product loop) → confirm/record (retry) → intent branches → close_* nodes generated from `outcomes`.
- **Code nodes do every calculation:** business days, deadline per product, the ladder's next offer, checking and snapping dates ("next Friday", weekends and holidays), amount bounds, choosing the close reason. The dialogue model only talks and pulls out values.
- **Tool schemas are read from openapi at compile time.** No field name is hard-coded except the seven tool names.
- **Wording:** fixed sentences from the spec are said word for word. A lint step blocks the opening from saying anything the judge may read as a claim, such as "recorded" (the PUB-10 failure).
- **Platform rules** (PLATFORM-NOTES) are built in once:
  - Booleans arrive as strings.
  - No top-level `and` in conditions.
  - Bake values with `default`.
  - Leave `agent_config` out.
  - A post-hook adds `what_to_do_next`.
  - close_call arguments are baked from variables.
- **Starting point:** the sample's `gen_brief_a.py`, which already scores 8–10/10. We generalise it, so we don't start from zero.
- **Tests:**
  - Unit tests for `lib/` (dates, ladder, close reason) using Brief A/B facts and the spec's `examples`.
  - Golden test: spec(A) → workflow, which passes PUB-01..10.

## 3. Extractor (`builder/extract.py`)

- The Agent SDK runs one prompt with the schema, both docs and the raw brief files. Its only output is `spec.json`.
- Raw material (a mail plus a policy extract plus a memo plus a code list): the prompt says that later documents override earlier ones (a memo wins over the policy). Every conflict is listed in `spec.conflicts[]` with the choice made.
- The validator checks:
  - The JSON Schema.
  - Every openapi enum value is mapped or explicitly unused.
  - Every outcome is reachable.
  - Bands cover `days_late` with no gaps.
  - The `examples` pass the compiled code.

  Errors go back to the LLM, at most 3 rounds.
- **Model:** Opus 5.5, for the best reading. Its cost is not scored, only its time.

## 4. Builder entry (`builder/main.py <brief files…>`)

- The runtime is one Python program, so the order of steps is guaranteed rather than left to the LLM to follow from a skill.
- **Time budget:**
  - Extract ≤ 6 min.
  - Compile and save < 1 min.
  - Self-test round 1 ≤ 11 min.
  - Optional round 2 only if failures remain and > 12 min are left.
  - Hard stop at 27 min, keeping the best-scoring version.
- At the end it prints the `workflow_id` and `agent_id` and exports the workflow file.

## 5. Self-test cases from the spec (`builder/cases.py`)

- Cases are written by code from the spec, in `public-test-set.json` format: personas, identity data, products and expected records. That gives one case per intent, per band edge, per date trap (weekend, holiday, "next weekday", after the deadline), per identity failure, and for a multi-product mix.
- Expected records come from the **same `lib/` code**. So the self-test catches dialogue failures, not policy-math failures; those are caught by the `examples` and unit tests.
- About 10 cases are picked per round, weighted toward the hard ones, and run with mixed `--style`.
- **Assumption, verified in plan task 1:** `run_cases.py --cases <our file>` works with made-up customers. The mock bank must seed whatever data the case sends.

## 6. Fuzz corpus (development only, `bench/`)

- 6–8 mutated briefs of our own, written from A and B. Each comes with its own openapi and cases. Mutations:
  - Raw-material format: a mail plus a policy plus a memo that overrides it, plus a code list.
  - Different identity questions and field enums.
  - Other bands, a different deadline rule and a different calendar.
  - Renamed outcomes and reasons.
  - A new intent.
  - Note-policy changes.
  - Notes in Brief A style.
- `bench/run.py` runs the builder on each one, scores it and logs build time. This is the yardstick for every change, both ours and the fallback's.
- None of this goes into the builder's prompts as rules. The mutated briefs are inputs to test the builder, never examples given to it.

## 7. Fallback and decision point

- Tonight: harden the sample builder:
  - Add the wording lint.
  - Redact keys from the trace and deny reads of env.sh.
  - Disable MCP and connectors.
  - Generalise the skill so it reads facts into data.
- **Sunday 16:00:** run both builders on the fuzz corpus. Submit whichever passes more cases. On a tie, pick the faster one.

## Security and trace

- The builder's Agent SDK runs with:
  - `strict_mcp_config` and no MCP servers.
  - A deny rule on `env.sh` and `.env*`.
  - A redaction filter for `fak_…` and team-key patterns on every trace line.
- Traces are scanned before any commit.
- The repo goes public only when Hazar asks.

## Out of scope

- Voice.
- Languages other than English.
- A UI.
- Changing `AGENT_LLM`.
- Hand-tuning against hidden-brief cases after 20:00 (only crash fixes are allowed).

## Schedule

| When | What |
|---|---|
| Sat 17:30–20:00 | Plan; verify the cases assumption; spec schema; extract spec(A) by hand-check; compiler skeleton from `gen_brief_a.py` |
| Sat 20:00–01:00 | Compiler at parity on A (reps 3), then B; lib unit tests; fallback hardening (subagent, in parallel) |
| Sun 09:00–13:00 | Extractor plus validator; fuzz corpus (subagents); cases.py |
| Sun 13:00–16:00 | Full builder runs on the corpus; fix; build time ≤ 15 min |
| Sun 16:00 | Decision: approach 1 or the fallback. Freeze the code |
| Sun 20:00–23:00 | Run on the hidden briefs, export, write traces, submit |
