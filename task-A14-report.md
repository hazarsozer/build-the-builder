# Task A14 report: a refusal counts only for the product the code is offering

Base: fdc11cc (A12 tip; branch reset to it, no work of its own).
Commits: f57dcd6 (mechanism + updated tests), bc30a63 (A14 tests, wording), 6c89923 (post-hook clock copy), plus this report.
Full suite: `uv run pytest -q` gives **726 passed, 6 skipped** (713 at base). `lint()` returns `[]` for spec-a and spec-b.
No simulations. One chat-API conversation on slot `probe-hooks` (call 4c48412b, workflow version 16).

## The live failure (gate a12b, PUB-12, call 01176716, read with freya.get_call)
- Turn 1 answer: "I will pay the credit card tomorrow. I am not paying the personal loan or the car loan." Three parallel calls: p2 refused, p3 refused, p1 agreed. p2/p3 got "Not counted yet. If you already made attempt 1 ... call refused again".
- p2 got attempts 1 and 2 (correct). The result after p2's last attempt said "Next is the car loan ... If the customer already gave an answer for it, call set_product for it now. Otherwise make attempt 1". The model called p3 refused again in the same turn, without an offer. That second call counted (the old `offered=True` escape), so the car loan was settled with no offer. Records were right; the judge failed the attempts check.

## Why a turn clock
"Counts only for the product the code most recently told the model to offer" is not enough on its own: in the live call the code had just named the car loan when the model reported it refused in the same turn. A refusal must also come after the customer has spoken since the instruction. The code needs the customer's turn number for that.

**Probe (call 4c48412b, slot probe-hooks, version 16):**
- Setup: a one-node workflow named `negotiate` with an api_call tool (httpbin). Its pre-hook records `vars.get('negotiate.total_turns')`, plus the same value placed as a header default and as a body default.
- Results:
  - `vars['negotiate.total_turns']` is readable in a pre-hook as an int: 3 on the third customer message, then 4 and 4 for two sequential calls in the fourth message.
  - The variable table in `route_evaluated` events lists it too, along with `negotiate.current_turns`.
  - A placeholder in a header default is substituted as an int and breaks the request ("Cannot serialize non-str key 3"). It is not used.
  - Sequential calls in one turn see the earlier call's writes (call c's pre-hook saw b's write).
- **Conclusion:** no tool-schema change is needed. The hooks read the system variable directly.

## Mechanism (builder/multi.py)
- **Clock** (`_clock`, SET_LIB): `negotiate.total_turns` (`TURNS`, from `NODE = "negotiate"`). Without it (offline, code mode) the clock is the set_product batch counter `mp_seq`.
  - The pre-hook stores the value it read in `mp_clock`. The post-hook uses that copy, so a turn number and a batch number never mix.
  - The probe only saw a pre-hook read the turn.
- **"Offer named at"** (`told`, now in `DYN` in place of `offered`):
  - When `_next` names a product's attempt, the post-hook writes `mp_o<num> = <clock>`. Before, this was `yes`. It is still only that product's flag variable.
  - A counted refusal with attempts left sets that product's `told = clock` in its own `mp_p<num>` ("Make attempt k+1 now").
  - `_load` takes the later of the two.
  - Product 1's attempt 1 is named by the prompt (step 1) for the customer's first answer, so its implicit `told` is 1 (`_told`).
- **`_refuse(st, b, clock)`:** a refusal counts (uses an attempt) only when both of these hold:
  - the product is the first open product in the code's order (`_next` always names that one);
  - `clock > told`, meaning the customer spoke after the instruction.
  
  Withdrawing an **agreed** product still counts, as before. Any other refusal is **not counted**:
  - `Not counted: no offer for it was made before this answer; a no before an offer uses no attempt.` (the current product)
  - `Not counted: its offer is not made yet; this no uses no attempt.` (another product)
  
  The ack sets `own=False`, so the result goes on with `_next`, which is the current product's instruction. There is no "call again" invitation. The "If you already made attempt 1 ... call refused again" text and the escape are removed.
- **Stance (`no`):** a product refused by name before any attempt is remembered. When `_next` reaches it: `Next is the car loan (product_id p3). The customer already said no to it before any offer; that used no attempt. Acknowledge it once, in the same sentence as the offer ("I understand you said no to the car loan; ..."), and make attempt 1 of its plan now: <attempt 1>. Call set_product refused for it only after the customer answers this offer.` Other products get `If the customer already agreed to an amount and a date for it or settled it (dispute, paid), call set_product for it now. Otherwise make attempt k ... A no said before this offer uses no attempt: call set_product refused for it only after the customer answers this offer.` The old "If the customer already gave an answer for it, call set_product for it now" is what invited the live call's extra refusal, and it is gone.
- **refused + ALL** (before: "Go through the products one at a time"): only open products are touched. The current product counts if it qualifies; the others are not counted. No stance is kept, because a plain "no" to the today question is not a refusal by name. Each product gets its own ack.
- **Item 3:** agreed, paid and dispute are unchanged (`_agree`/`_note`). They settle the product at once, and each call still writes only its own `mp_p<num>`, plus the shared `mp_seq` and `mp_clock`, which hold the same values for every call of a batch.
- **Prompt** (compile.py step 3, spec-b 5368 → **5494** chars, budget 5500): `Only the product the last result (or step 1) told you to offer counts; report a no about another product too: it uses no attempt.` In step 4, "Not counted yet" became "Not counted".

## Tests (tests/test_multi.py)
- Sim now has the turn counter: every `say` is a new customer message unless `same_turn=True`, a `batch` is one message, `next_turn()` is a message with no call, and `turns=False` switches to the batch-counter fallback. `refuse()` re-reports after "Not counted:".
- New:
  - `test_a14_replay_live_call_01176716_every_product_gets_its_attempts`: the live sequence including the same-turn extra p3 call (not counted, attempt 1 re-instructed). It ends with p2 tried 2 and p3 tried 1, one promise on p1, and refused notes for p2/p3.
  - `..._without_the_turn_variable_uses_the_batch_counter`
  - `..._parallel_duplicate_refusal_counts_once`
  - `..._refusal_of_a_later_product_mid_negotiation_keeps_the_current_one`
  - `..._up_front_no_for_product_one_gets_its_attempt_one_acknowledged`
  - `..._plain_no_then_prompt_offer_counts_on_the_next_turn`
  - `test_a14_pub11_and_pub13_sequences` (expected promises; PUB-13 capped to 23 Oct)
  - `test_a14_pub12_sequences_one_by_one`
  - `..._up_front_paid_and_dispute_settle_at_once_in_parallel`
  - `..._refused_all_counts_only_the_current_product`
  - `..._hooks_read_the_negotiate_turn_counter_and_the_prompt_says_so` (no escape text in the hooks; budget ≤ 5500)
  - `..._post_hook_uses_the_turn_its_pre_hook_read`
- Updated to the new rule:
  - `test_a9_attempts_counted_in_code_per_refusal`
  - `test_a9_next_names_the_attempt_after_the_used_ones` (now a withdrawn agreement), plus the new `test_a14_out_of_order_refusal_uses_no_attempt`
  - `test_fix1_refusal_before_any_offer_is_not_counted` (the second report of the same no still counts nothing)
  - `test_a11_pub12_replay...`
  - `test_a11_pre_hook_writes_only...` (adds `mp_clock`)
  - the step-4 prompt quote

## Compiled output (diffed against fdc11cc, `scratchpad/a14/cmp.py`)
- **spec-a:** 0 paths differ.
- **spec-b:** 9 paths differ: the negotiate task_message, the set_product pre/post hooks, and the code of negotiate_end, record_plan and the four wraps. The last group is only the `LOAD` change: `mp_o` is parsed as a clock number. Tool schema and system prompt are unchanged.

## Concerns
1. **Product 1 relies on step 1 of the prompt.** Its attempt 1 is taken as named for the customer's first answer, so a refusal of it counts from turn 2 on. Turn 2 counts even if the model answered a question at turn 1 instead of offering. The old escape had a similar gap.
2. **An offer the model makes without being told is not known to the code.** If the model offers a later product on its own and the customer refuses, that refusal is not counted. The result then names the current product, which can repeat an offer. This trade-off is what the brief asked for: the code decides the order.
3. **Only one probe, on the mechanism.** No run of the compiled spec-b tool has happened. Only a gate run can show whether the model follows the acknowledgement wording ("I understand you said no to the car loan") once, without a double offer, and stops re-reporting.
4. **Same-turn counting depends on `negotiate.total_turns`.** It is verified in a pre-hook only, and in a chat-API conversation, not a voice simulation. Without it, the batch counter is the clock: parallel siblings never count, but a sequential same-turn re-report would.
5. **A re-instruction after a not-counted refusal** reads "Next is the personal loan ..." even when that product is already under way. It names the same attempt again, which is accurate but may lead the model to repeat the current offer.
6. **Probe slot.** probe-hooks now holds the probe workflow (version 16, httpbin only). The bank ledger was not touched.

---

# Fix round 1 (review: 1 Critical, 1 Important, 2 Minor)

This round adds commit 2bcd0a1 and this report section. `uv run pytest -q tests/test_multi.py` gives 89 passed and 1 skipped. The full suite gives **731 passed, 6 skipped**. Both specs lint clean. spec-a is unchanged (0 paths differ). spec-b's prompt is still 5494 characters, because this round changed only code and no prompt text.

| # | Fix | Where |
|---|---|---|
| 1 (C) | **A refusal that is not counted no longer re-names the current offer.** `_next(st, cur, clock, again)` checks whether the first open product's attempt was named in an earlier turn (`_told < clock`). If so, and the batch is not a review, the result is `Continue with the customer's answer to your current offer for the <Y> (attempt k of its plan); do not repeat it.` That text has no amount and no date. `mp_o<num>` (`st['_offer']`) is written only when a product is named for the first time (`_told` is None), so a re-naming never moves its clock. A refusal of another product reads `Not counted: that no is for the <X>; it is not its turn yet and uses no attempt.` Review still names the product again (`again`), and it keeps the product's clock too. The reviewer's sequence now works: p1 agreed, then in one turn p3 refused followed by p2 refused. p2 counts (tried 1), gets no `no` stance and does not get "I understand ...". The variant where a later product is paid or agreed first and the current product is refused in the same turn also counts. | multi.py `_next`, `_refuse`, SET_FIN (`clk` read once, passed to `_next`) |
| 2 (I) | **The fallback clock.** A per-product last-counted marker (`lc`, now in `DYN`) means there is never more than one count per clock value. Parallel duplicates in one batch were already idempotent because they read the same snapshot. **Limitation, stated plainly:** without `negotiate.total_turns`, a sequential re-report in the same turn (batch N+1) cannot be told apart from the customer's next-turn refusal (also batch N+1). No other variable separates them: the live call's variable table has no other per-turn signal. `system.iso_utc_now` is the call start in a voice simulation and does not change. The fix therefore relies on the turn variable. The live **voice gate** call 01176716 shows that variable present at every set_product turn (see item 4), so the fallback only runs offline and in code-mode tests. | `_refuse` (`fresh` test), `DYN` |
| 3 (m) | **No `no` stance for the product being offered.** A refusal of the current product, including product 1's at the today question, never sets `no`. Only a product that was never named keeps it. Product 1's today-answer "no" gets the plain attempt-1 instruction, with no "you said no to the credit card". | `_refuse` |
| 4 (m) | **Where the "pay today?" question sits.** The question is spoken by the engine as negotiate's `on_enter` TTS (`verified_lead() + {{debt_statement_text}} {{first_ask_text}}`, `on_enter_first_entry_only`, compile.py `negotiate_multi_node`). The identity answers are counted in the identity node, before load_account and prepare_account run. So the customer's answer to the today question is **negotiate turn 1**. The live gate call 01176716 confirms it: `negotiate.total_turns` was 0 through identity, load_account and prepare_account. It was **1** at "I will pay the credit card tomorrow ..." (the three parallel calls), **2** at the next answer and **3** at the third. Both sequential calls of turn 3 (p2's last attempt and the model's extra p3 report) saw 3. That is exactly the case the same-turn guard catches. This is a voice simulation, which settles concern 4 of the first report for a pre-hook's variable table. | live call 01176716 `route_evaluated` variable tables |

New tests (tests/test_multi.py):
- `test_a14r1_no_for_a_later_product_then_the_current_refusal_in_one_turn` (the reviewer's critical sequence)
- `test_a14r1_later_product_settled_then_the_current_refusal_in_one_turn` (paid and agreed variants)
- `test_a14r1_review_still_names_the_current_product`
- `test_a14r1_turn_path_never_counts_twice_in_one_turn`
- `test_a14r1_fallback_clock_never_counts_twice_in_one_batch`

Updated tests:
- the not-counted wording in five tests;
- `test_a14_up_front_no_for_product_one_uses_no_attempt_and_keeps_no_stance` (renamed: product 1 gets no stance);
- `test_a14_refusal_of_a_later_product_mid_negotiation...`, which now asserts the exact "Continue ..." result.

Remaining concerns:
- Concerns 1–3, 5 and 6 of the first report still stand. Concern 5 is narrower now: a re-naming happens only in the turn the product was named, or on review.
- When the current product was named in an earlier turn but its offer was never actually spoken, "do not repeat it" leaves the customer without an offer. For product 1 this can happen when the model answered a question at turn 1 instead of offering. The model's own offer from step 1 is still the expected path.
