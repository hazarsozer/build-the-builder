# Task A18 report

Base 567eb9a. Commits f6b95ea..52a77cc (10 commits, plus this report). No simulations, no workflow saves.
I read the transcripts with freya.get_call for the failing runs: outcomes c75e4365 (OUT-03/04), multi-raw 8f7f5b59
(MRW-03/07) and strict-wording e5ccab15 (STW-05).

## 1. Reconcile x verbatim (A13 x A16)
- `lint.required_pre_verify_sentences` now passes `opening.verbatim` to `safe_opening`. A verbatim first sentence is
  checked as written, so a banned term it contains is reconciled away. Without verbatim, the rephrased sentence is checked, as before.
- `cases.Ctx.banned()` lists only the terms that `reconcile_banned` keeps, so a self-test check never forbids a word the
  agent has to say. For spec-a/spec-b the output is unchanged (tested).

## 2. OUT-03 / OUT-04: two sympathy sentences
Evidence: in both calls the hardship_hold reply was "I am so sorry for your loss. I am sorry to hear that. Would you like ...".
Root cause: the hold prompt had step 1 "Exactly one short sentence of sympathy" and step 2 "Say <event.say>". The
extracted say ("I am sorry to hear that. Would you like me to connect you ...?") already contains a sympathy sentence,
so the model said two.
Fix (compile.support_nodes):
- When the fixed sentence matches `SYMPATHY` (sorry / condolence / sympath*), there is no extra sympathy step. The say
  step adds "This is your one sentence of sympathy: add no other, before or after it."
- A fixed sentence that is a question already makes the offer, so the separate "Offer to connect ... yes/no" step is dropped.
- Without a fixed sentence, the prompt still asks for exactly one sympathy sentence.

## 3. Multi-product restructuring
- **MRW-07.** Evidence: after the agent offered 450.00 for the loan, the customer said "I'll take the instalment amount
  for the personal loan", and neg_event extracted `restructuring` (0.96), which led to a transfer. Root cause: extraction
  read accepting the offered instalment as a request for lower instalments. It was neither the spec nor the multi.py
  routing. Fix: the description of each negotiate transfer event now says "Accepting an amount or a date the agent
  offered, even by its kind ("I'll take the instalment", "the minimum is fine"), is NOT <id>: it is continue."
- **MRW-03.** Evidence: after the read-back the engine went record_1 -> wrap_recorded -> note_1 (add_note p2 declined)
  -> finish -> close -> goodbye. It spoke "...is recorded. ... Goodbye." in one end_call. The persona's script asks for
  lower instalments *after* hearing "recorded", so it never got a turn. Root cause: the graph has no listening turn after
  the recording. The extraction was right (restructuring is a transfer event, notes timing at_close, on_transfer none).
  Fix (multi.after_record): when at least one promise was recorded (`mp_recorded`, set in WRAP_CODE) and negotiate has
  a `transfer` event, wrap_recorded goes to the conversation node `after_record`. That node speaks `{{mp_said}}` and
  then "Is there anything else I can help you with?":
  - A transfer request goes to the transfer node. The recorded promise stays, no notes are written, and the call closes as transferred/<reason>.
  - Any other reply, "nothing" or the turn cap goes to `after_record_done`, which clears mp_said so the close does not
    repeat it. The notes are then written and the call closes as before.
  - A question stays in the node (answer, then ask again).
- Tests: the MRW-03 state machine (wrap -> after_record -> transfer -> close_restructuring; nothing -> note_1). In
  test_multi.py a spec-b Sim chain (PUB-12 shape plus restructuring after recording: promise kept, notes [],
  transferred/restructuring). The test_multi Sim.chain now answers `after_record` (default "nothing").

## 4. STW-05: "guarantee"
Evidence: the customer asked "Can you guarantee you will stop calling me if I pay?" and the agent replied "I cannot
provide a guarantee." Root cause: the spec had no structured field for forbidden words. The extractor put rule 48 into
`extra_rules` (stage any), so the rule reached only the global system prompt and was never in the step prompts.
Fix:
- New optional top-level `never_say` (list of str, uniqueItems) in the schema, with an extract.md line and a regenerated
  spec-guide. specdiff merges it by union, and an absent field counts as [].
- compile: a `never_say_line()` goes into the system prompt and is appended to **every** conversation step (negotiate,
  hold, ask-once, readback, after_record, opening, identity, third party). Every post-verification step that lacks its own
  "cannot offer" line also gets "Never offer <banned_offers>" (deduped). The negotiate steps already carry their own line,
  which keeps spec-b's negotiate within the prompt budget.
- lint section 6: a never_say word in any form (the last word may take an ending) is an error when it appears in an
  engine tts line, a close farewell or a quoted sentence in a prompt. Bare quoted words in the prompt's own list are not flagged.
- cases: self-test cases get a check line "the agent never says any of these ..." when never_say is set.

## Changes to compiled output
- **spec-a** (no never_say): `human_hold_negotiate` gains "Never offer a discount, a waiver or an instalment plan."; the
  negotiate neg_event description gains the accept-an-offer sentence on restructuring. System prompt identical. Lint clean.
- **spec-b**: the same two changes, plus "Never offer ..." on `human_hold_readback` and `readback`. New nodes
  `after_record` and `after_record_done`. wrap_* code gains `vars['mp_recorded']`, and wrap_recorded gains a first
  transition to after_record. Lint clean.
- **Bench specs** (outcomes, multi-raw, strict-wording): hold prompts changed as in section 2, plus the items above.
  Lint output is unchanged from base.

## Tests
`uv run pytest -q`: 788 passed, 6 skipped (base 768). New: tests/test_a18.py (19), one test in test_multi.py.
Fixtures: tests/fixtures/bench-{outcomes,multi-raw,strict-wording}.spec.json, copied from results/bench. Adjusted tests:
- test_compile `test_banned_offer_overlapping_transfer_trigger_is_not_excluded_from_it`: now checks only the
  banned-offer list sentence. The new example names "instalment".
- test_multi Sim.chain: handles the after_record node.
The corpus-word test passes.

## Concerns
- never_say helps only if the extractor fills it. Extraction was not run (offline only). The bench strict-wording spec
  from 11 Oct has no never_say, so a rebuild is needed to see the effect.
- after_record adds one turn to every several-product call that records a promise when a transfer event exists. A
  simulation should confirm that personas answer "no" cleanly and that the judge accepts the extra question.
- The single-product flow still ends right after "recorded", the same gap as MRW-03. No bench case covers it, so it was left unchanged.
- The `after_record` reply covers `transfer` events only. A human request or a hardship mention after recording goes to the normal close.
- Pre-existing, not touched: the multi-raw bench spec lints with an error. confirmation.change_note says "recorded" in
  the negotiate prompt before success. That is the A12/A17 area.
- Possible overlap with A17: compile.py (event_values, support_nodes, system_prompt/build), multi.py (after_record,
  WRAP_CODE), lint.py, cases.py, spec_schema.json/extract.md/spec-guide.md.
