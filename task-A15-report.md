# Task A15 report: bench generalisation gaps (insurance, ids, fields)

Base 4ee02ac. Branch `worktree-agent-a3c0cfc6a77c856ce`. Evidence: bench run `results/bench-ours-20261011-051255`
(code ec9ad6f), simulations d676b397 (insurance), 1ec642cc (ids), 7b3a7f06 (fields); transcripts read with
`freya.get_call` (read-only). Fixtures: `tests/fixtures/bench-insurance.spec.json`, `tests/fixtures/bench-ids.spec.json`
(the bench's extracted specs, copied unchanged). All tests in `tests/test_a15.py`.

Suite: 731 passed at base, 749 passed (6 skipped) at the tip. spec-a / spec-b compile lint-clean; their compiled
output differs only by the embedded `dates.py` source (close_reason signature, attempt_date dict form), two constants
in the wraps (`PROMISE`, `NO_PROMISE`, same values as the old literals), the identity post-hook's own count
(`MAX_WRONG = 3`, `SCOPE = 'total'`, same limit the bank applies), and the callback rules / third-party task lines of
fix 5. No node added or removed for either reference spec.

## Summary

| Case(s) | Class | Root cause | Commit |
|---|---|---|---|
| IN-01/02/03/07/08 outcome | compile | multi wrap used literal `'promise'` | afe2c9e |
| IDS-03, IDS-05 | compile | `identity.max_wrong` / `wrong_scope` never compiled | e8dc4cf |
| IN-03 date | extraction (schema gap) | no attempt date for "n-th business day" | 88078fc |
| IN-06 | extraction + compile (schema gap) | an account-data event treated as speech | 7c56653 |
| IDS-08 | compile (prompt) | "tomorrow" holiday not named; model moved the day | 3e8fee1 |
| FLD-02 | case | simulated customer self-corrected in one message | 86e73fc |

## 1. IN-01/02/03/07/08: recorded promise closed as no_promise / refused

Evidence (IN-01, call f853f635): create_promise p1 186.4 2026-10-16 returned `ok: true, promise_id pr1`; `wrap_recorded`
ran, `finish` routed `mp_close == 'close_refused'`; close_call `{"outcome": "no_promise", "reason": "refused"}`. Spoken
farewell began "I understand. Your promise ... is recorded". The `finish` node's branches had no promise close at all
(close_paid, close_refused, close_hardship, close_dispute, close_service_error). IN-02/07/08 identical; all records
otherwise right (IN-08 capped p1 to 16 Nov, p2 20 Nov as expected).

Spec (extraction) was right: `outcomes` has `("promise_to_pay", None, "promise_recorded")`.

Root cause (compile): `builder/lib/dates.py:246` `close_reason` returned the literal `('promise', None)` and
`builder/multi.py:773` `wrap_targets` looked up the literal key `"promise|"`; the brief renames promise to
`promise_to_pay`, so no close node matched and `CLOSE_BY['*']` (the refused close) won. `no_promise` and `transferred`
were literals too (dates.py:244-257, multi.py:776/855/977/983/1010).

Fix: `close_reason` takes `promise`, `no_promise`, `transfer` names (defaults unchanged). `Multi` reads the promise row
from the outcome table (event `promise_recorded`) and the no-promise outcome from the refused event (as
`oracle.close_from_notes` already did); wrap targets, fallbacks and the wrap code use them.

Tests: `test_ins_recorded_promise_closes_with_the_briefs_promise_outcome`, `test_ins_two_recorded_promises_close_as_promise_to_pay`,
`test_ins_no_promise_still_closes_refused`, `test_renamed_no_promise_outcome_is_used` (red before, green after).

## 2. IDS-03, IDS-05: two wrong answers to one question did not end the check

Evidence (IDS-03, call 596f4488): two wrong postcodes; the bank (counts 3 in total, bench notes "Limits of our bank")
answered `wrong_answers_left: 2`, then `1`; the agent asked a third time; the customer said "I know my own postcode.
How much"; extraction `declined` -> close `not_verified / declined`. Expected `not_verified / failed` after the second
wrong postcode. IDS-05 same with birth years.

Spec right: `identity.max_wrong: 2, wrong_scope: per_question`.

Root cause (compile): `builder/compile.py` identity_node / VERIFY_POST never read `max_wrong` or `wrong_scope`
(grep: no use anywhere in compile); the only failure route is `id_left == "0"` from the bank's `wrong_answers_left`.

Fix: VERIFY_PRE records the field being answered; VERIFY_POST counts a counted wrong answer (verified false, not a
non-counting hint, not unreadable, not incomplete, not a technical failure) per question or in total, and sets
`wrong_answers_left` to the lower of its own count and the bank's (post-hook runs before response_mapping, so
`id_left` and the existing failure route follow).

Tests: `test_ids_two_wrong_postcodes_end_the_check_though_the_bank_allows_more`, `test_ids_per_question_one_wrong_on_each_is_fine`,
`test_hints_and_unreadable_answers_are_not_counted`, `test_total_scope_counts_across_questions` (red before).

## 3. IN-03: attempt 2 offered on the deadline (4 Nov) instead of the 5th business day (28 Oct)

Evidence (call 79cbf1b8): set_product result "Make attempt 2 now: propose the lower amount 137.00 ..."; agent "Could you
pay 137.00 on Wednesday 4 November 2026?"; promise 137 on 2026-11-04.

Spec: `bands[1].attempts[1] = {"amount": "floor", "date": "deadline"}` plus `custom_code lapse_risk_attempt2_date`, and
a conflict entry: "no attempt date enum for 'the 5th business day after today'". The compiler never runs custom_code.

Root cause (extraction, forced by the schema): `builder/spec_schema.json` `$defs.attempt.date` enum had only today,
last_business_day_of_week, deadline, next_business_day.

Fix: new attempt date `nth_business_day` with `n` (schema with if/then requiring n, regenerated `docs/spec-guide.md`,
one line in `builder/prompts/extract.md`). `dates.attempt_date` accepts the attempt dict (reads `n`, still capped at the
deadline); callers in dates.plan_products, compile product_facts, validate examples, oracle, cases pass the attempt.

Tests: `test_attempt_date_nth_business_day`, `test_nth_business_day_spec_validates_and_needs_n`,
`test_in03_attempt_2_offers_the_5th_business_day` (fixture spec with the new enum: plan says Wednesday 28 October).
Takes effect when the extractor emits the new enum (re-extraction in the bench).

## 4. IN-06: lapsed policy stated as overdue, payment asked, transferred to customer_care

Evidence (call 24432a90): get_account returned life_insurance 74 days overdue; the agent said "I am calling about an
overdue premium ... Can you pay this today?", then "Can you pay the 96.00 today?"; customer "I need to speak to
someone" -> human ask-once -> `transfer_customer_care`, close `transferred / human_request`.

Spec: event `policy_lapsed`, trigger "get_account returns a policy with days_overdue above 60 (lapsed).", stage
negotiate, offer_transfer_else_close to retention; band `lapsed` [61, 100000] with no attempts. Nothing tied the band
to the event.

Root cause (schema gap + compile): an event whose trigger is account data was compiled like every negotiate event, as
an extraction value the customer must say (`event_values("negotiate")`), so `prepare_account` went straight to
`negotiate`, which states the debt and asks for payment (`compile.py` account_nodes / negotiate_multi_node).

Fix: optional `bands[].event` (schema + validate: must be an event id, not note_and_continue; extract prompt line).
`prepare_account` appends ACCOUNT_EVENT_CODE (first product in get_account order whose band has an event sets
`account_event` and `product_words`) and routes to the event before negotiate. offer_transfer_else_close events reuse
the support offer step in account mode: "Thank you, you are verified." + the event sentence + the transfer offer, no
sympathy line, no banned-offer line, a request for a person counts as yes, a named payment closes (no way back to a
payment), and a decline closes straight at the else-close (no wrap, so no notes, as rule 37 says) unless the event
names a per-product note. transfer / close account events route to the transfer / close directly. Account events are
removed from speech extraction. Self-test cases (`cases.s_event`) put the product in that band. Specs without
`bands[].event` compile exactly as before (verified: no node diff for spec-a, spec-b, insurance, ids, fields fixtures).

Tests: `test_band_event_validates`, `test_in06_lapsed_policy_fires_before_the_debt_is_stated`,
`test_lapsed_second_policy_names_that_policy`, `test_self_test_case_for_an_account_event_uses_that_band`,
`test_no_lapsed_policy_negotiates_as_before`. Takes effect when the extractor sets `event` on the band.

## 5. IDS-08: "tomorrow" (a holiday) booked as the next business day without saying so

Evidence (call 55c3983d): now Tue 10 Nov 16:30; brother "She can be reached tomorrow at eleven."; agent called
schedule_callback 2026-11-12 11:00 at once and said "Thank you, we will call back then." Records passed (expected
callback 12 Nov 11:00); the judge failed the check "tomorrow (11 November) is refused because it is a holiday; one more
time is asked".

Spec right (callback days today / next_business_day, holidays include 2026-11-11).

Root cause (compile, prompt): SETUP_CODE `callback_rules` (`compile.py` ~line 200) only named "the next business day,
Thursday 12 November"; nothing said that tomorrow is outside the window, and the third-party task did not forbid
moving the named day, so the model mapped "tomorrow" to the allowed day.

Fix: when tomorrow is not the next business day (and `next_day` is not an allowed callback day), callback_rules says
"Tomorrow, <date>, is not a business day, so tomorrow is outside the window."; the third-party task adds "Never move the
day or time they name to another one yourself ... say so and ask."

Test: `test_ids08_tomorrow_holiday_is_named_outside_the_window`. LLM behaviour; only the bench re-run can confirm it.

## 6. FLD-02 (fields 7/8)

Evidence (call ffa1cc84): customer's first message "It is 30 June 1983. Oh wait, I am sorry, it is 30 June 1982."; the
agent checked 1982-06-30 (correct) and moved on; judge failed "after the wrong date of birth the agent asks the same
question again". Everything else passed (Saturday 24 -> Tuesday 27 Oct, channel app, outcome promise).

Classification: case. The simulated customer gave the wrong date and its correction in one message, so no wrong
answer existed; using only the corrected value is the builder's rule. Fix: the case script now gives the wrong date
alone and the correction only after the agent says it is not correct (`bench/fields/cases.json`).

## Notes and concerns

- Fixes 3 and 4 need the extractor to use the new schema fields; the bench's next run re-extracts, so they depend on
  the model following the schema description and the extract prompt line. A spec without them still fails IN-03 /
  IN-06 as before.
- Not in scope, seen in every transcript: the opening's recording sentence is replaced by the safe sentence
  ("This call is monitored and stored for quality and security purposes.", `lint.py` SAFE_RECORDING_SENTENCE, PUB-10
  lesson), although the insurance brief says the first sentence "word for word". The judge did not fail it this run.
- The insurance event sentence "Your {product_words} policy has lapsed" reads "Your life insurance policy ending 4410
  policy has lapsed" with this brief's product words (an extraction choice); left as is.
