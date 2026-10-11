# Task A21 report: cut-off fragments and small talk; close_call guard (item 7)

Base a70441f (candidate-2). Branch `worktree-agent-a9737cbf1b80c9c6b`. Commits cbad906..6a25609 (6), plus this report.
Not merged. No simulations. One chat-API probe on slot `probe-hooks` for item 7 (call 3a362703, httpbin only, no bank).

| Commit | Item |
|---|---|
| cbad906 | 2 + 6. negotiate: small talk is no refusal; "Go on." never twice in a row |
| ef4203d | 1. identity: fragments and small talk never decline; the decline closes only after the explanation |
| 3cf2f6c | 3. opening: `chatter` value, code ask counter, cut-off rule |
| 0b5b9d2 | 4. third party: cut-off rule, turn cap 12 -> 20 |
| 439a1af | labels: after_record_step / `*_hold_*` step names |
| 6a25609 | 7. close_call: code-set outcome only, read-only route until a close node ran |

Tests: new `tests/test_a21.py` (29 cases), updated `tests/test_a10.py` (1), `tests/test_multi.py` (node set),
`tests/test_outage.py` (close_call harness). Full suite **1005 passed, 6 skipped** (base 974). Lint is clean for
spec-a and spec-b. Corpus-word tests pass. Embedded code has no `{{`/`}}` and uses only json/re/datetime (none new).

Prompt sizes (task_message, chars, base -> new):

| step | spec-a | spec-b |
|---|---|---|
| opening | 1049 -> 1390 | 1049 -> 1390 |
| third_party | 1460 -> 1722 | 1460 -> 1722 |
| identity | 1586 -> 1937 | 1586 -> 1937 |
| negotiate | 4415 -> 4620 (limit 4800) | 5479 -> 5694 (limit 5800) |

Node count: spec-a 44 -> 45, spec-b 71 -> 72 (new `opening_ask`).

## Shared cut-off rule (item 6)

`CUT_OFF` (compile.py:52) now ends with: `Never twice in a row: after your own "Go on.", ask your last question again
in a few words.` It is now used in the opening, third-party and identity steps as well (before A21: negotiate only).
Negotiate uses its own variant `NEG_CUT_OFF` (compile.py:56), which also covers small talk (item 2).
This is a prompt rule only; the platform has no per-turn code hook that could count "Go on." turns.

## 1. Identity

- Prompt (compile.py:1966-1967), added to the explanation rule and a new line:
  `Small talk is not a refusal: never explain for it; ask your question again in a few words.` + `CUT_OFF`.
- `id_event` `continue` description (compile.py:2171) adds: `A message cut off mid-phrase, or only small talk with no
  refusal words, is continue, never declined.`
- New extraction `id_explain_said` (yes/no, compile.py:2173), only when the spec has a `declined` event:
  `yes if an earlier agent message in this conversation said "These questions protect your account.". Otherwise no.`
- `ID_DECLINE_GATE` (compile.py:823). The code flag `id_explained` is set from `id_explain_said` and kept once seen.
  Order: a new answer goes back to identity (A10); otherwise, if no explanation has been given, it goes back with
  `id_gate=explain`, `id_event=continue`; otherwise it closes. The explain path is capped at 3 bounces by its own
  counter (`id_explain_n`), because a return to identity may restart the node's turn cap. Gate transition:
  compile.py:2148. Label: labels.py:29.
- Effect: a fragment read as `declined` can no longer close the call before the explanation (PUB-03 e2199549, PUB-07
  43c11304: no explanation had been said in either).
- Tests: `test_identity_prompt_and_extraction_rules`, `test_pub07_replay_...` (7 continue, then declined -> back to
  identity; declined after the explanation -> close_declined), `test_pub03_replay_...` (two wrong dates, fragments,
  declined -> back; third wrong answer -> close_failed), `test_identity_explanation_flag_is_kept_once_seen`,
  `test_identity_decline_without_explanation_is_bounded_by_the_gate`, `test_no_explanation_flag_without_a_declined_event`.
  test_a10 `test_identity_decline_without_new_answer_still_closes` now sets `id_explain_said=yes`.

## 2. Negotiate (single and multi)

- `NEG_CUT_OFF` (compile.py:56) replaces the cut-off bullet in both prompts:
  `A message cut off mid-phrase (...) or small talk with nothing about paying is not an answer or a refusal and uses no
  attempt: after a cut-off say only "Go on." and read it with the next message, never twice in a row; otherwise ask
  about your current offer again in a few words, no amount or date ("So, can you do that?").`
- Refusal rule, single (compile.py:1996): "a flat refusal or a non-answer (including ...)" became `a flat refusal or
  an evasive reply about paying ("I can't", "not now", "no" to paying today with no date and no amount, refusing the
  floor, refusing the deadline)`. Multi (compile.py:2378): the same wording for the current product. The A9 "no to
  today with no date and no amount" and A12 rules are kept.
- The `cannot_pay_or_too_much` extraction (compile.py:2242, :2405) adds `or only small talk` to the "is never ... it is
  continue" sentence.
- Tests: `test_negotiate_small_talk_is_no_refusal_and_reasks_without_a_figure`, `test_negotiate_go_on_never_twice_in_a_row`,
  `test_negotiate_keeps_a9_a12_rules`, `test_negotiate_small_talk_is_never_cannot_pay`, `test_negotiate_sizes`.
  The single-product attempt plan is model-tracked, so PUB-05 can only be pinned at the prompt level.

## 3. Opening

- `line_status` gets the value `chatter` (compile.py:2080): `only small talk, or a message cut off mid-phrase, with no
  answer to the question and no question back.` It routes nowhere, so the node stays and asks again.
- The asks are counted in code. `not_confirmed` goes to the new code node `opening_ask` (compile.py:2065, :2089;
  code `OPENING_ASK_CODE` compile.py:808). It increments `op_asks` and resets `line_status` to `pending`, so a failed
  extraction is not counted twice. At `max_asks` it closes as unclear; otherwise it goes back to the opening. The old
  `opening.total_turns` route is gone. Turn cap: `max_asks + 1 + 5` (8 for spec-a), then close_unclear.
- Prompt (compile.py:1870-1871): `- Small talk with no answer uses no ask: reply in a few words, then ask again.` + `CUT_OFF`.
- Label (labels.py:28). Test node set updated (test_multi.py).
- Tests: `test_opening_chatter_value_and_prompt`, `test_pub02_replay_two_small_talk_turns_do_not_close` (9a7b8c8e),
  `test_opening_two_unanswered_asks_still_close_as_unclear` (chatter in between is not counted),
  `test_opening_extraction_miss_after_an_ask_is_not_counted_twice`, `test_opening_chatter_is_bounded_by_the_turn_cap`,
  `test_opening_max_asks_follows_the_spec`.

## 4. Third party

- `CUT_OFF` line in the task (compile.py:1897). Turn cap `TP_TURN_CAP = 20` (compile.py:805, used at :2115).
- Tests: `test_third_party_cut_off_rule_and_larger_cap`, `test_pub02_replay_twelve_fragments_...` (14bc0a61: 12
  continue turns no longer close; a booked callback then closes as callback), `test_third_party_cap_still_ends_the_step`.

## Labels (cosmetic)

labels.py:50-74 `STEP_NAMES` / `step_words`. Edge labels and code-step descriptions now name the steps:
`Next: hardship hold after record` -> `Next: hardship reply, after-recording step`; `Next: human hold opening` ->
`Next: human reply, opening step`; `Next: after record` -> `Next: after-recording step`; `Otherwise: after record
done` -> `Otherwise: notes and close after recording`; "(human, after record step)" -> "(human, after-recording step)".
Test: `test_after_record_steps_have_readable_names`.

## 7. close_call (controller item)

**Mechanism, from the evidence.** In PUB-03 ac424c99 the model did not call close_call during the conversation. The
agent never replied to "It is 12 July 1991." (a 60 s stall). The customer hung up at 64 s, and close_call ran at
64.9 s as the end_of_call lifecycle run, after the call ended. No close node had run, so `{{outcome}}`/`{{reason}}`
were unset. docs/tools-and-code.md:561 says an unresolved parameter is filled by one model call, so the model guessed
`not_verified/failed`. The same happened in PUB-13 9ce9db6c (`service_error` guessed) and PUB-12 f242b760 (diag R2).

**Probe (call 3a362703, probe-hooks version 17, httpbin).** It used a one-node workflow with an end_of_call api_call
tool, an unset `{{outcome}}`, and `reason` set as a variable. The chat was ended with chat/end. Results:
- The pre-hook **runs** on the end_of_call run. It rewrote the url (`/anything/rerouted`) and the body, and httpbin
  echoed the rewritten request.
- The unset outcome reached the pre-hook already filled by the model (`'no_promise'`). `vars['outcome']` was `None`.
- `function_call_start` args show the request after the pre-hook.

**Fix** (fails closed, like A12's create_promise guard):
- (a)+(b) `CLOSE_PRE` (compile.py:1191). It ignores the body's outcome and reason. Only when `close_ready == 'yes'`
  and `vars['outcome']` is set does it point the request at `/close_call`. The body is then customer_id, the outcome
  from vars, and the reason from vars (left out when empty or for a reasonless outcome). Otherwise the request stays
  on the tool's own url, the read-only `/get_account` (compile.py:2465), with a body of customer_id only.
  `CLOSE_POST` (compile.py:1208) answers `Not now: the call is not finished; continue the conversation.` when no close ran.
- `close_ready='yes'` is written by every close node in the same `vars.update` as outcome, reason and farewell
  (nodes.py:163). Every close goes through `close_node`: single closes, transfers' closes, multi wraps via `finish`,
  and the identity-failure close. So `close_ready` is set exactly when a close node ran.
- (c) The end_of_call run after a close records the close node's outcome. An early hang-up sends nothing (A8 ruling).
- (d) Description: `Records how the call ended. Run by the system at the end of the call; the assistant must never call it.`
- Tests (test_a21.py): `test_close_call_tool_is_system_run_and_fails_closed`, `test_pub03_replay_hangup_in_identity_...`,
  `test_model_call_before_any_close_is_answered_not_now`, `test_every_close_node_sends_its_own_outcome_never_the_body`
  (every close node in A and B, against a model-guessed body), `test_pub10_create_promise_retry_closes_with_the_right_outcome`
  (retry worked -> promise; failed again -> no_promise/service_error), `test_multi_wraps_close_with_the_wrap_outcome`
  (PUB-12 promise, PUB-13 paid+refused -> no_promise/refused), `test_multi_hangup_at_the_readback_sends_no_guessed_close`.
  test_outage: the harness now models the platform's model-fill of unresolved fields (`close_request`), so the
  existing hang-up and close tests run against a guessed body. `test_close_call_prehook_drops_an_empty_reason` now
  reads the values from vars.

## Concerns

- Item 7 does not fix PUB-03 ac424c99 itself. That call failed because the agent stalled for 60 s after the first
  answer (platform or model latency). The fix only stops a false bank close afterwards. On a hang-up the bank now has
  **no** close_call record at all (A8 ruling), and the transcript still shows a `close_call` function call, sent to
  get_account. A grader that requires exactly one close_call per call would count that as missing on a hang-up.
- The end_of_call run still makes one model call to fill the unused parameters. It is harmless (the pre-hook ignores
  them) but adds latency after the call. Pre-writing `outcome=''` in setup would avoid it, but it is untested whether
  an empty value resolves.
- Identity: `id_explain_said` is model-extracted (an observable fact in the transcript), and the gate decision is in
  code. If the extractor never sees the explanation, a decline closes only after 3 bounces or at the 20-turn cap.
- Opening: re-entry after `opening_ask` relies on the engine answering the latest message on return. This is the
  same as the A10 gate and the ask-once back nodes. Whether `max_node_turns` counts per entry or per call is
  unverified, so the chatter cap may be per ask.
- "Go on." once, then re-ask, and the negotiate re-ask without a figure, are prompt rules. Live effect unproven
  (the diag gave low confidence for PUB-13).
- probe-hooks now holds the A21 probe workflow (version 17). The bank was not touched.
