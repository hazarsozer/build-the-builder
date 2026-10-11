# Task A17 report: bench sme gaps (4/8)

Base 4688f80. Evidence: bench run results/bench-ours-20261011-051255 (code 29369d5), simulation
1beb5a15, transcripts read with freya.get_call (read-only). Fixture: tests/fixtures/bench-sme.spec.json (copy of
results/bench/sme/bench-sme.spec.json).

## Classification and root causes

| Case | Class | Root cause |
|---|---|---|
| SM-01 | extraction, fixed by A16 | The bench spec has no `opening.verbatim` (built before A16), so `safe_opening` rewrote "This call is recorded." into the monitoring sentence. Brief rule 1 says "First sentence, word for word: ..."; the extractor clause (set `verbatim` true only when the brief requires the first sentence word for word) matches it. No code change. Test: the fixture with `verbatim: true` speaks the sentence byte-exact and lints without errors. |
| SM-03 | compile | verify_identity got HTTP 500 once (`function_call_error`). The platform did not retry it (`maxRetries: 1`, failure_count 1, one call) and an HTTP error skips the post-hook, so VERIFY_POST's "technical problem, call again" never reached the model. The only guidance was the failure event's line "If ... verify_identity ... still fails after one retry: apologise and say the bank will call back"; the model said exactly that. The service_error close in identity was an `id_event` value extracted from the customer's message, which never names a tool failure, so nothing routed; the call looped on "Goodbye" until identity's turn cap sent it to close_declined (`not_verified/declined`). |
| SM-05 | compile | One product, but the spec notes the administration event, so it compiles as several products. All attempts refused correctly (set_product: last attempt, phase done) -> wrap_done -> close `no_promise/refused`. But `wrap_targets()` held only the promise, all-paid and *noted* closes; `no_promise|refused` was not a target, so the wrap's `*` fallback became `targets[-1]` = close_in_administration (and it spoke the administration sentence). The event did not fire; the wrap mis-routed. Same bug for wrap_dispute, wrap_hardship, wrap_not_talk (all went to in_administration). |
| SM-06 | compile (two parts) | (1) Identity: the bench code predates A15; the second wrong founding year left `wrong_answers_left: 1`, so the check went on and the third answer passed. A15 (e8dc4cf, in this base) already counts per question; test confirms the second wrong year routes to close_failed. (2) After that, the call reached negotiation, the persona stalled twice, wrap_done -> the SM-05 bug -> in_administration. |

Also found while tracing (same class as SM-05): a confirmed promise whose create_promise failed twice on this
spec closed `no_promise/refused` (the failure note is not noted, so the wrap fell back to refused), and the wrap
said " This concerns your ..." with an empty failure sentence (also on the insurance fixture).

## Fixes (generic; no corpus words; a test greps for sme words)

1. `builder/multi.py` (01abd99)
   - `wrap_fallbacks()`: each wrap's own (outcome, reason); `wrap_targets()` adds them plus the failure note's close,
     so `CLOSE_BY['*']` is each wrap's own close whenever the outcome table has it. finish's default (no close chosen)
     prefers the failure close, then wrap_done's close.
   - `WRAP_CODE_REACH`: only when a product can end with a reason the brief does not note (`close_unnoted()`), the close
     also weighs a product's own settled reason or failed promise that the brief closes on. Briefs that note every
     reason (spec-b, insurance) keep `WRAP_CODE` byte-identical.
   - `FAIL_SAY` defaults to "I am sorry, I could not record your promise." when the failure event has no `say`.
2. `builder/compile.py` (6685172), only when the failure event's stages include identity (`self.id_fail`):
   - identity task line: a failed verify_identity call (error instead of a result) is not a wrong answer; call it again
     at once with the same field and answer, without asking the customer; a second failure ends the call automatically.
   - the failure event leaves identity's extraction enum and guidance (it is no longer listened for in speech).
   - engine route `verify_identity_response.status == error AND verify_identity_failure_count > 1` (the platform
     variables seen in the SM-03 transcript), or a second checked-nothing result reaching the post-hook (`id_tech_n`),
     goes to `close_identity_service_error` (the failure event's outcome/reason and farewell). A dedicated node because
     several-product compiles rewrite the event's close to speak the wrap summary.

Chosen over an engine-run retry node: the retry must return to the conversation with the result, and the model does
not see an api_request node's result; the model-driven retry keeps the existing what_to_do_next flow, and the only
deterministic part needed (closing after the second failure) is engine-routed.

## Output changes (compiled, offline)

- spec-a, spec-b, bench-ids fixture: byte-identical to base.
- bench-insurance fixture: wraps gain targets; wrap_policy_lapsed's declined-offer fallback now goes to
  close_policy_lapsed (`no_promise/policy_lapsed`) instead of close_service_error (was wrong); FAIL_SAY default.
- sme fixture: wraps, finish, identity, new close node.

## Tests

tests/test_a17.py (15): SM-05/06 wrap_done closes refused; every sme wrap closes with its own event; SM-08
administration still noted and closed; failed promise closes service_error and says so; generic property over
sme/insurance/spec-b (every wrap's fallback close is its `*` and a finish target); SM-06 per-question limit routes to
close_failed; SM-03 one HTTP error stays (model retries) then load_account, two errors close service_error, two
checked-nothing results close too; route absent for spec-a/spec-b and when the event does not cover identity;
reference specs lint clean; verbatim opening exact; no sme corpus words in generic text. At base 9 of them fail
(SM-06 identity, SM-08, SM-01, lint pass there). Full suite: 771 passed, 6 skipped (756 + 15).

## Concerns

- SM-03 now depends on the model calling verify_identity again after an error; not verified live (no simulations).
  If it does not, the call still drifts to identity's turn cap. An engine retry would need a probe of how a
  conversation node resumes after an api_request node.
- The failure count is per call, not per answer: one failure on each question also closes as service_error.
- Time-of-day greeting ("Good afternoon" from 12:00) is not implemented: the opening is a fixed TTS, so a verbatim
  opening is always the morning form (SM-03 at 14:00 said "Good morning"). Not one of the four failures.
- Pre-existing lint error on the sme fixture (unchanged by this task): the change note "A recorded promise cannot be
  changed ..." trips the "recorded before a promise is recorded" check in negotiate.
- The identity service-error farewell is the generic "I am sorry, there was a technical problem."; the brief also
  wants "the bank will call back" (that is in the event's conditional instruction, not its `say`).
