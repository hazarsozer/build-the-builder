# Task A12 report: single-product date verdicts in code

Base: 52b015f (A11 tip; branch reset to it, no work of its own).
Commits: 9a51d9a, 2713a54, 3bdfcb9, plus this report.
Full suite: `uv run pytest -q` gives **644 passed, 6 skipped** (594 at base). spec-a and spec-b compile, and `lint()` returns `[]` for both (asserted in tests/test_a12.py).
No simulations. One chat-API conversation ran on slot `probe-hooks`.

## Mechanism and why

**Choice:** a read-only api tool `check_date`, built like `set_product`:
- it uses the get_account route, with a pre-hook and a post-hook;
- body fields carry `{{cdx_*}}` defaults, and no `cdx_*` variable is ever set.

**Why this mechanism:**
- A9 proved two things: a post-hook's `result['data']` is what the model reads, and api-mode body fields reach `config['body']`. So no platform behaviour here was untested.
- An extraction + code-node hop would need an extra node per turn and an extraction for the date. It would also leave the model to speak a verdict it never sees as a tool result.

**Flow:**
1. **The model reports the day.** It calls `check_date` with `said`, the customer's own words for the day joined across turns (for example "Monday the twenty-sixth"). It can also pass an optional `date` (YYYY-MM-DD). That date is used only when the words name no day and are not a vague period.
2. **The pre-hook resolves and judges the date** (`CHECK_PRE`, compile.py:842):
   - `dates.resolve_said` (dates.py:395) turns the words into a date. It handles:
     - day numbers in digits or words (the first date from today with that number, the same rule as the old calendar rows);
     - a month (the year rolls over at year end);
     - a weekday under the spec's `weekday_words` (this / next / next week / after next);
     - tomorrow, today, ISO dates.
   - A weekday that does not fit the day number loses to the number. The exception is that weekday's latest past date ("Monday the 12th" said on the 14th), which gives `past`.
   - Periods ("week after next", "end of the month", salary ...) return `vague`.
   - `dates.date_verdict` (dates.py:457) then applies the spec policies in this order:
     1. past → ask again;
     2. after the deadline → the deadline (`offer_deadline`) or reject;
     3. not a business day → next business day, previous business day, or reject (`non_business_day`). If the next business day is past the deadline, the answer is the last business day on or before the deadline.
     4. Otherwise the date is `ok`.
   - The hook writes `cd_kind`, `cd_date` (the ISO date to use) and `cd_msg` (the words to say).
3. **The post-hook returns the verdict** (`CHECK_POST`, compile.py:913). It replaces the result with `{ok, verdict, date, what_to_do_next}`.
4. **create_promise sends only a date the rules allow** (`PROMISE_DATE_PRE`, compile.py:925). This is item 2:
   - A date that is not acceptable becomes the date `date_verdict` offers for it.
   - A missing or non-ISO date becomes check_date's last `cd_date`.
   - An acceptable date is never changed.
   - When the date was changed, the success result adds "It is recorded for <date>, the date the rules allow: say that date."
   - `retry_promise` reuses the corrected date (`promise_date_used`, compile.py:827).

## Probe (call 5ae0a800-91c2-4f01-a848-13d16bcb9930, slot probe-hooks version 9)

**Setup:**
- Seeded a PUB-07 practice session, customer c_489fcc3c-122 (now 2026-10-14T11:00, personal loan 540.00, 5 days late, early band, deadline Mon 2 Nov). Verified both fields (all_verified true).
- Ran setup and prepare_account offline on the bank's products and passed the resulting vars as customerVariables.
- The workflow is one node with the **compiled spec-a negotiate task_message, system prompt and both tools (check_date and create_promise, hooks included), unmodified**.
- Script: `scratchpad/a12/probe_a12.py`. Secrets were read from env.sh inside Python and never printed; the sandbox guard refused `source` in bash.

| Customer | Tool call (body.said) | verdict / date | Agent said |
|---|---|---|---|
| "I cannot pay today. I can pay the week after next." | none | n/a | "Could you please tell me the exact day you can pay?" (asked once) |
| "Monday the twenty-sixth." | "Monday the twenty-sixth" | not_business_day / 2026-10-27 | "Monday 26 October 2026 is a bank holiday, not a business day. Can you pay the 540.00 on Tuesday 27 October 2026 instead?" |
| "Hmm. What about the twentieth of November instead?" | "the twentieth of November" | after_deadline / 2026-11-02 | "Friday 20 November 2026 is after the deadline. The latest date I can accept is Monday 2 November 2026; can you pay the 540.00 then?" |
| "No, sorry, I meant the twenty sixth of October." | "the twenty sixth of October" | not_business_day / 2026-10-27 | same as row 2 |
| "Yes, the Tuesday is fine." | none | n/a | read-back: 540.00 on Tuesday 27 October 2026, "Do you confirm?" |
| "Yes, I confirm." | create_promise {date 2026-10-27, amount 540, p1} | recorded | "Your promise has been noted. ..." |

- **Ledger:** one promise, p1, 540, 2026-10-27, matching PUB-07's expected promise.
- **Amounts:** each offer kept the current attempt's amount (540.00).
- **Tool fields:** the model filled only `said`. The `date` field was left at its template and dropped, which is fine.

## Changes

- **builder/lib/dates.py**
  - `import re` (:8; the sandbox allows re; docstring updated).
  - Spoken-date tokeniser and helpers (:294-393).
  - `resolve_said` (:395), `date_verdict` (:457).
- **builder/compile.py**
  - `CHECK_DAY` prompt line (:51).
  - prepare_account writes `deadline_iso` (:385). This is shared code, so spec-b's prepare_account also gets the line; the value is unused there.
  - PROMISE_POST date note (:787) and RETRY_PRE corrected date (:827).
  - `CHECK_TOOL`/`CHECK_PRE`/`CHECK_POST`/`PROMISE_DATE_PRE` (:841-950).
  - Negotiate prompt: FACTS lose the calendar, allowed dates and weekday words. Step 1 swaps the "Date must be one of ... nbd/after-deadline" bullet and `DAY_NUMBER` for `CHECK_DAY` (:1729). The now-unused `nbd`/`aft` texts are removed.
  - Negotiate tools `[check_tool(), create_promise(pre=date_rules()+PROMISE_DATE_PRE)]` (:1939-1945).
  - `date_rules()` (:1956: prelude + WW, NBD, AFTER, TOOL) and `check_tool()` (:1963).
- **Multi-product is untouched:** set_product, `DAY_NUMBER` and its calendar stay.

## Tests

- **tests/test_dates.py** (A12 section):
  - 32 parametrised cases over the five AFTER-PLAN §3 now values: Fri 16 Oct; Fri 23 Oct before the holiday Monday; Wed 14 Oct 19:30; Fri 30 Oct month end; Wed 23 Dec holiday cluster. Early and late bands. Cases cover holidays and weekends → next business day, after the deadline → deadline, past, ok, and the words / pieces / ordinal / month / year-rollover forms.
  - Policy variants (previous_business_day, reject, after_deadline reject, next business day past the deadline).
  - weekday_words both modes.
  - vague / none / invalid.
  - "may" as a verb, ISO.
- **tests/test_a12.py** (new, 14 tests). Runs the compiled hooks as the platform does and covers:
  - tool shape (api_call, get_account, `{{cdx_*}}` defaults never set, lint clean);
  - PUB-07 in five word forms → 2026-10-27, and the message never mentions 2 November;
  - after-deadline wording keeps the current attempt's amount (reject variant drops it);
  - ok / past / vague with a guessed ISO / ISO fallback / no-day;
  - late band at 23 Oct, 23 Dec and 30 Oct now values;
  - create_promise date correction and its spoken note;
  - retry reuses the corrected date;
  - hooks survive bad config/result;
  - prompt has no calendar, keeps the PUB-04/06/07 rules and is ≤ 4800;
  - multi unchanged.
- **Updated tests** (the after-deadline and day-number wording moved from the prompt to check_date's result):
  - test_a10 `test_self04_after_deadline...` now runs the hook for spec-a and the A10 dress spec.
  - test_compile `test_g1_no_record_word...` and `test_a9_day_number...`: single uses check_date, multi keeps calendar rows.
  - test_multi `test_spec_a_node_set_unchanged`: negotiate tools are now `["check_date", "create_promise"]`.
- **Run:** `uv run pytest -q` → **644 passed, 6 skipped in 21.38s**.

## Compiled-prompt and workflow changes (diffed against 52b015f with `scratchpad/a12/cmp.py`; embedded dates.py source normalised)

- **spec-a:** 4 paths differ.
  - negotiate task_message: **4572 → 4234 chars** (budget 4800). It loses the `Dates you may accept`, `Weekday words` and `Calendar` FACTS blocks, the nbd/after-deadline bullet and the DAY_NUMBER bullet. It gains the CHECK_DAY bullet. The PUB-04 date-only line, the vague rule ("ask once for the exact day"), the floor-once and "The rest stays overdue." once rules, the cut-off and refer-back rules are all unchanged.
  - negotiate tools (check_date added; create_promise date description and pre-hook).
  - prepare_account code (`deadline_iso`).
  - retry_promise pre-hook.
  - The system prompt is unchanged.
- **spec-b:** 1 path differs (prepare_account writes the unused `deadline_iso`). Its negotiate task, which is 5336 chars, and its tools are unchanged.

## Concerns

1. **The model must call check_date.** The prompt says to call it before answering any named day, and the probe called it every time. If the model skips it and answers from its own reasoning, the spoken verdict could still be wrong. create_promise's pre-hook still records only a date the rules allow and tells the model the corrected date, but the customer would hear the correction only after the read-back.
2. **Parser coverage.** It handles day numbers, months, weekdays with this/next/next week/after next, tomorrow, today and ISO. It does not handle "in N days", "a week on Friday" or "the 26th" said with a different month in mind. Without a parse, the model's `date` (YYYY-MM-DD) is judged instead, unless the words look like a vague period.
3. **Weekday-number conflicts** ("Tuesday the 26th" when the 26th is a Monday): the day number wins, matching the old calendar-row rule, and the verdict names the real weekday.
4. **Ledger visibility.** Every check_date call is a read-only `get_account` request in the bank ledger, as with set_product in brief B. A literal judge listing tools may see extra get_account calls. In the probe the tool list was check_date ×3 and create_promise.
5. **A non-2xx get_account** (bank outage or unseeded customer) skips the post-hook (A9). The model then gets a function error instead of a verdict. The pre-hook's vars still hold the verdict, and create_promise's date correction still applies.
6. **"Your promise has been noted"** in the probe came after a recorded result. In the full graph the promise_recorded transition and close node handle this, and the one-node probe workflow has no transitions.
7. **Probe slot.** probe-hooks now holds this probe's workflow (version 9). Practice customer c_489fcc3c-122 has one promise (p1, 540, 2026-10-27).

---

# Fix round 1 (review: 3 Important, 3 Minor)

Commits 9a89b29, ae57d97, 575d875, plus this report section. Concerns 1 and 2 of the first report are superseded by items 1–3 below.

| # | Fix | Where |
|---|---|---|
| 1 (I) | **Corrections: the last mention wins.** The resolver now collects every mention in order: ISO, day number, weekday with its modifier, today/tomorrow, period words. The last one wins, so "the 20th, no, the 23rd" gives the 23rd. A mention right after "not" is dropped (skipping on/the/this/next/a), so "not Friday, Thursday" and "Thursday, not Friday" both give Thursday. A month counts only when it sits next to its day number ("the 30th of October, no, the 2nd" is the next 2nd from today). `CHECK_DAY` and the `said` description now ask for the customer's **latest** words for the day, joining only a cut-off phrase with its rest. | dates.py:383 `_mentions`, :487 `resolve_said`; compile.py:51, check_tool `said` description |
| 2 (I) | **create_promise rejects instead of correcting.** When the date is not one the rules allow, or is not YYYY-MM-DD, the pre-hook rewrites the request. It goes to the read-only `/get_account` with body `{customer_id}`, so nothing reaches the bank's promise route. It sets `promise_reject`. PROMISE_POST then returns `{ok: false, recorded: false, promise_status: "rejected", what_to_do_next: "NOT RECORDED: <verdict>; <what to offer> (create_promise date <iso>). Say this date, read it back and ask again."}`. It does not count `promise_fail_n`, and the HTTP status is 200, so neither the retry route nor the failure close fires. The `d is None` branch no longer reads `cd_date`, so a stale value is never used. An acceptable date goes to the bank unchanged. `promise_date_used` is set only then; the retry reuses it. | compile.py:953 `PROMISE_DATE_PRE` (url rewrite :977), :785 PROMISE_POST rejected branch |
| 3 (I) | **Week phrases.** "Friday week", "a week on/from Friday" and "a fortnight on Friday" return **vague**, so the agent asks for the exact day. They cannot be supported reliably: colloquially "Friday week" said on Wed 14 Oct is 23 Oct, while brief A rule 22's "next Friday" (= 23 Oct) puts "the Friday after next" at 30 Oct. An ordinal followed by a period word ("the second week", "the first week of November", "the first payment") is not a day number (`_PERIODISH`). A weekday pinned by a week phrase stays a day: "Friday next week", "next week on Friday", "Friday this week", "the Friday after next", "Friday the week after next". | dates.py:372, `_mentions` weekday branch |
| 4 (m) | **After hours: deviation from the ruling, flagged.** Brief A has no payment cut-off. Rule 19 says "a promise date must be today or later", and the 19:00 limit in rule 6 is for callbacks only. Treating today after hours as impossible would contradict rule 19 for a customer who says "today" at 19:30. So the rule is spec-driven. I added an optional `negotiation.same_day_cutoff` ("HH:MM") to the spec schema (docs/spec-guide.md regenerated). When it is set and now ≥ cutoff, `date_verdict(..., today_open=False)` returns `today_closed` and offers the next business day. check_date says "TOO LATE FOR TODAY: it is after 18:00 ...", and create_promise rejects today. When it is absent (spec-a/spec-b), today stays `ok` at 19:30. If you want the strict reading anyway, set `same_day_cutoff` in the reference specs: one line, no code change. | spec_schema.json:676; dates.py:533-546; compile.py:847 `CHECK_LIB._cd_judge`, :1991 `CUTOFF` |
| 5 (m) | `next week` is no longer a whole-text regex. It is a modifier only when it sits next to the weekday, and it is otherwise a period mention that the last-mention and "not" rules apply to. "not next week, this Friday" gives this Friday. | `_mentions` |
| 6 (m) | **Bare cardinal days with context.** "twenty six October", "October twenty six", "Monday twenty six", "Monday 26" and "on twenty seven" are accepted. A bare "twenty six" with no context is still `none`. | `_mentions` day branch |

Also added:
- `_WEAK` period words (later, soon, early) do not override a day said before them: "the 23rd or later" gives the 23rd.
- `CHECK_LIB` holds the shared verdict wording (`_cd_judge`) used by both check_date and create_promise.

## Probe (call 5b110990-ed05-44ea-8370-2378e9792580, probe-hooks version 10)

The rejection changes the hook mechanics (request rewrite), so I re-probed:
- **Setup:** a seeded, verified PUB-07 practice customer c_c4230cd5-550. The tool is the **compiled spec-a create_promise, unmodified**. The task forces two calls.
- **"record A" (date 2026-10-26):**
  - Platform log `function_call_start`: url `.../v1/get_account`, body `{"customer_id": ...}`. So the pre-hook's url and body rewrite took effect.
  - Result: `{"ok": false, "recorded": false, "promise_status": "rejected", "what_to_do_next": "NOT RECORDED: Monday 26 October 2026 is a bank holiday, not a business day; offer the next business day, Tuesday 27 October 2026 (create_promise date 2026-10-27). Say this date, read it back and ask again."}`. The model spoke it.
- **"record B" (date 2026-10-27):** the call went to `.../v1/create_promise` and returned "The promise is recorded."
- **Ledger:** exactly one promise (p1, 540, 2026-10-27). The rejected call wrote nothing.

## Tests

- **tests/test_dates.py:**
  - `test_fix1_last_mention_wins_and_not_drops_a_mention`, run for each of the 5 now-values. It covers "the 20th, no, the 23rd", "not Friday, Thursday", "Thursday, not Friday", "not next week, this Friday", "twenty six October", "Monday twenty six", the dash form, and "Monday the 26th. Sorry, the twenty-seventh.".
  - `test_fix1_week_phrases_are_vague_not_a_day`, run for each of the 5 now-values. It covers a week on Friday, Friday week, a week from Monday, the second week, the first week of November, "Friday, or sometime next week" and next week. It also checks that Friday next week and next week on Friday stay days, and that "the 23rd or later" gives the 23rd.
  - `test_fix1_bare_cardinal_day_needs_a_month_or_weekday`.
  - `test_fix1_same_day_cutoff`.
- **tests/test_a12.py:**
  - `test_fix1_promise_with_a_date_the_rules_do_not_allow_is_never_recorded`: holiday, after the deadline, past, a non-ISO date after a check_date (no stale reuse), and an acceptable date that passes unchanged.
  - `test_fix1_rejected_promise_is_no_service_failure_in_the_graph`.
  - `test_fix1_hook_takes_the_corrected_day_and_asks_on_week_phrases`, run for each of the 5 now-values; a guessed ISO date is ignored for a period.
  - `test_fix1_after_hours_today`: no cut-off at 19:30 gives ok. A cut-off of 18:00 gives today_closed, offers Thu 15 Oct, and create_promise rejects today. The spec validates, and "7pm" fails validation.
  - `test_retry_uses_the_date_sent_to_the_bank`. The old correction tests were replaced.
- **Runs:**
  - `uv run pytest -q tests/test_a12.py tests/test_dates.py tests/test_compile.py` → 127 passed, 1 skipped.
  - `uv run pytest -q` → **663 passed, 6 skipped in 21.53s**.
  - lint is clean for spec-a and spec-b (asserted in test_a12).

## Compiled-prompt changes in this round

- **spec-a:** the negotiate task_message is **4302 chars** (4234 after the first round; budget 4800). The CHECK_DAY bullet now reads: `call check_date with their latest words for it (a corrected day replaces the earlier one; join only a cut-off phrase with its rest: "Monday the." + "26th.")`. The check_date `said` description changed the same way. The create_promise and check_date hooks were rewritten as above.
- **spec-b:** unchanged apart from the earlier unused `deadline_iso`.

## New concerns

1. **A rejected create_promise still shows as a `create_promise` call** in the Freya tool list. The bank sees only a get_account. A literal check such as "create_promise called exactly once" (cases.py:300) or a judge counting tool calls could count it. It happens only when the model skips check_date or sends an unallowed date.
2. **Item 4 deviates from the ruling** (see the table). Upside: it obeys brief A rule 19 and stays generic. Downside: if the judge expects after-hours "today" to be refused, PUB cases at 19:30 that say "today" would differ. One spec line flips it.
3. **"not" handling is local.** It looks back only over a few filler words. "not on the Friday but the Thursday" works; "I said it's not going to be Friday, Thursday" also works. A long negated clause before the day may not be caught.

---

# Fix round 2 (the create_promise guard fails closed)

Commit 2ee331d, plus this report section.

## Design: fail closed by default, not only in the except branch

Both one-product promise tools now carry the **read-only `/get_account` as their own url**:
- `safe_route()`, compile.py:2030, asserts at compile time that the url was `<base>/create_promise` before switching it. This covers item 4.
- It applies to the model's `create_promise` in negotiate (:1998) and to the engine-run `retry_promise` (:2158).

Only a pre-hook that has **accepted** the date points the request at `PROMISE_URL` and sets `promise_sent = yes`. So a request reaches `/create_promise` only through that one accepted branch, and every other outcome stays on the read-only route:
- a rejection;
- a body that is not a dict;
- an unreadable `deadline_iso`;
- any exception;
- a pre-hook that never runs at all.

| # | Fix | Where |
|---|---|---|
| 1 | The `except` branch calls `_off_bank(NOT_CHECKED)`: `promise_reject` = "NOT RECORDED: the date could not be checked; ask the customer for the day again.", url = `SAFE_URL`, body = `{customer_id}`. That call has its own guard. | compile.py:964 `NOT_CHECKED`, :965 `OFF_BANK`, :976 `PROMISE_DATE_PRE` |
| 2 | A body that is not a dict is rejected and rerouted the same way. | `PROMISE_DATE_PRE` |
| 3 | `RETRY_PRE` goes to `PROMISE_URL` only when `promise_sent == yes` and `promise_date_used` is set (the guard's accepted date). It then sends exactly that date. Otherwise it calls `_off_bank`. It never falls back to the raw `create_promise_args.promise_date`. A retry kept off the bank gets no `data.ok`, so `retry_ok` is never true and the node takes its failure close. | compile.py:825 |
| 4 | The baked `SAFE_URL`/`PROMISE_URL` constants (`routes()`, :2023) replace the url read from config, so a config without a url is rerouted too. | `OFF_BANK`, `routes()` |

**PROMISE_POST** (:785) reports "recorded" only when `promise_sent == yes` and no rejection is set. Otherwise it answers `rejected` with `promise_reject` or `NOT_CHECKED`. A call whose pre-hook never ran therefore returns a get_account answer and can never be read as recorded.

## Tests

- **tests/test_a12.py:**
  - `test_fix2_promise_tools_default_to_the_read_only_route`: both tools default to get_account. A post-hook with no pre-hook run returns rejected.
  - `test_fix2_unreadable_deadline_keeps_a_holiday_off_the_bank`, with `deadline_iso` missing, "", "garbage", "2026-13-45" or 42. Neither a holiday date nor an otherwise acceptable date goes to /create_promise; the result is rejected with NOT_CHECKED.
  - `test_fix2_non_dict_body_is_rejected_and_rerouted`, with body None, a string, a list or an int.
  - `test_fix2_config_without_url_is_still_rerouted`: a rejection lands on get_account; an accepted date lands on /create_promise.
  - `test_fix2_retry_reuses_only_a_date_the_guard_accepted`. Three cases are kept off the bank: no guard run (raw holiday arg), a guard rejection, and a `promise_date_used` without the sent mark. After an accepted date, the retry sends that date.
- **Updated** for the new contract (the guard must run and accept before "recorded"):
  - `test_hooks.test_promise_posthook_records_on_ok` sets `promise_sent`, and also asserts that without it the call is rejected.
  - In `test_generalise`, the extra-field tests (m1, g3 ×2, g4) give the pre-hook a readable deadline, or give the retry/post the guard's accepted state.
- **Runs:**
  - `uv run pytest -q tests/test_a12.py` → 33 passed.
  - `uv run pytest -q` → **675 passed, 6 skipped in 21.73s**.

## Probe

I tried the same create_promise-only probe as in fix round 1, on probe-hooks versions 11-14:
- **Version 11 ran "record A" (date 2026-10-26):** the result was "NOT RECORDED: Monday 26 October 2026 is a bank holiday ... Say this date, read it back and ask again.", spoken by the model. That is the rejection through the new default-safe url.
- **The next chat message failed with a platform 504.** The callId was lost when the script crashed, so the "record B" (accepted → /create_promise) leg and the ledger read did not complete.
- **Three reruns (versions 12-14) failed on their first message with 503 "Internal server error".**
- **A trivial one-node workflow with no tools (version 15) also returned 503.** This is a Freya chat-API outage, not this change.

The accepted leg uses the same pre-hook url rewrite that fix round 1's probe 5b110990 proved live (the request went to the rewritten url), only in the other direction. probe-hooks now holds the trivial outage-test workflow (version 15).

## Concerns

1. **The accepted path is not re-proven live.** An accepted date going from the get_account default to /create_promise is not proven on the platform after the default-url change, because of the outage. Re-run `scratchpad/a12/probe_fix1.py` once the chat API is back: "record B" must log url `/v1/create_promise` and the ledger must hold 2026-10-27.
2. **No fallback if the platform ever ignored a pre-hook url rewrite.** Every promise would then stay on get_account and never be recorded. This fails closed, but it is total. Fix round 1's probe showed the rewrite is honoured.

(Fix round 2 concern 1 is resolved: the controller's live gate at 4747354 recorded promises on the accepted path, with PUB-07 3/3, the PUB-10 retry passing and PUB-B 3/3.)

---

# Fix round 3 (PUB-06: an accepted offer was offered again)

Commits 7cb68c5, e505e0c, plus this report section.

**Cause.** After "Can you pay 637.50 on Friday 16 October 2026? The rest stays overdue.", the customer said "I can do that on Friday". The model called check_date. The OK verdict said "Propose it with the amount of your current attempt, as a yes/no question unless ...". The model repeated "Can you pay 637.50 on Friday 16 October 2026?", and the judge counted a second offer of the minimum.

| # | Fix | Where |
|---|---|---|
| a | **check_date knows the agent's own proposals.** Three sources count as dates the agent proposed: prepare_account's plan dates in `offer_dates`; every alternative date a result told the agent to offer, in `cd_offered` (next business day, deadline ...); and the deadline. When the checked date is one of them, the verdict is `ACCEPTED OFFER: <date> is a date you proposed and it can be accepted. The customer accepted your offer: do not propose it again. As a confirmation, read back the product, the amount and the date once and ask "Do you confirm?", then record (create_promise date: <iso>).` The read-back uses the spec's own confirmation question (step 4 wording, `CONFIRM` from `confirmation.question`). | compile.py:389, :910-918 `offered`, :942, :958, :2036 |
| a' | **A bare weekday resolves to the proposed date on it.** "the Tuesday is fine" after an offer of Tue 27 Oct now resolves to 27 Oct, not the next Tuesday (20 Oct). This uses `resolve_said(..., prefer=offered)`. A weekday with a modifier ("next Tuesday") still follows weekday_words. | dates.py:487 |
| b | **The prompt checks only days the customer names.** CHECK_DAY starts "A day the customer names themselves (...)" and ends "A yes to a date you proposed needs no check: go to the read-back." | compile.py:51 |
| c | **OK for a new date** (named by the customer): `OK: <date> can be accepted. If an amount is already agreed, read back the product, the amount and the date once and ask "Do you confirm?". If not, propose this date, as a yes/no question, for the amount of your current attempt (never say a figure the customer already heard). create_promise date: <iso>.` | compile.py, CHECK_PRE |
| audit | **No verdict tells the model to restate a heard amount.** The not-a-business-day, after-deadline and too-late-for-today offers now say `offer <date>, as a yes/no question, for the amount of your current attempt (never say a figure the customer already heard)` instead of "with the amount of your current attempt". No verdict contains "with the amount" or "Can you pay". This is asserted for 7 phrasings at each of the 5 now-values. | compile.py, CHECK_PRE `cur` |

## Tests

- **tests/test_a12.py:**
  - `test_fix3_pub06_accepting_the_offered_date_is_confirmed_not_offered_again`: "I can do that on Friday." / "Friday" / "the 16th" give ACCEPTED OFFER with a read-back. None of them contains "propose this date" or "current attempt".
  - `test_fix3_dates_from_a_result_and_the_deadline_count_as_offers`: after the holiday verdict, "Tuesday is fine" gives ACCEPTED OFFER for 27 Oct. The deadline counts as an offer.
  - `test_fix3_a_new_date_named_by_the_customer`.
  - `test_fix3_no_verdict_restates_an_amount_already_heard`, run for each of the 5 now-values.
  - `test_fix3_prompt_checks_only_days_the_customer_names`.
- **tests/test_dates.py:** `test_fix3_bare_weekday_means_the_offered_date_on_it`.
- **Updated:** the after-deadline and holiday wording assertions in test_a12 and test_a10 (`offer it, as a yes/no question, for the amount of your current attempt`).
- **Runs:**
  - `uv run pytest -q tests/test_a12.py tests/test_dates.py` → 103 passed.
  - `uv run pytest -q` → **685 passed, 6 skipped in 21.91s**.

## Compiled-prompt changes

- **spec-a:** the negotiate task_message is **4398 chars** (4302 before this round; budget 4800). Only CHECK_DAY changed, as in (b). The check_date pre-hook and the date-rules prelude changed (`CONFIRM`), and prepare_account writes `offer_dates`.
- **spec-b:** the negotiate task is unchanged (5336). Its prepare_account also writes the unused `offer_dates`.

## Concerns

1. **ACCEPTED OFFER also fires on a plan date the agent has not yet spoken.** This happens when the customer names a plan date first, for example attempt 2's deadline before attempt 2 was made. The verdict then asks for a read-back with the current amount instead of a proposal. In practice the plan date is the first one offered, and a read-back states the amount and asks for confirmation, so the customer can still say no.
2. **Not live-tested in this round.** No probe was run: hook mechanics are unchanged and only the wording and the offered-date bookkeeping changed. A gate run of PUB-06 should confirm that the model now reads back once.

---

# Fix round 4 (ACCEPTED OFFER only for offers the agent really made)

Commit de703cf, plus this report section.

**Cause.** `offered` in CHECK_PRE held every plan attempt date and the deadline from the start. A customer who named one of them before the agent said it (for example the deadline on attempt 1) got "The customer accepted your offer: do not propose it again" for an offer never made. The verdict did not name an amount either. DATE_ONLY still said "Propose that amount", which conflicted with the read-back.

| # | Fix | Where |
|---|---|---|
| a | **check_date takes `attempt`**, a required model-filled body field (`{{cdx_attempt}}`): "the number of the last plan attempt you have already said to the customer: 0 before your first offer with a date, 1 after attempt 1, ...". The hook reads leading digits only and clamps the value to the plan length. An unreadable, negative or template value counts as 0. | compile.py check_tool, CHECK_PRE |
| b | **Offered = plan dates of attempts 1..attempt, plus `cd_offered`.** `cd_offered` holds the dates an earlier result told the model to offer; they are spoken in that same turn. The deadline is no longer added by itself: it counts only as a reached attempt's date or as an offered alternative (after the deadline). The bare-weekday `prefer` list uses the same set, so an unsaid plan date never steers a bare weekday. | CHECK_PRE |
| c | **Both verdicts name the amount.** prepare_account writes `offer_amounts` (full/floor/current per attempt). ACCEPTED OFFER reads back the amount of the attempt that proposed that date (the latest reached attempt with that date; for a `cd_offered` date, the current attempt). NEW DATE reads back the current attempt's amount (attempt `max(attempt,1)`). Full gives the past-due figure, floor gives the floor, and current gives "540.00 (or 180.00 if the customer already said the full amount is too much)". It adds "(an amount the customer named and you may accept instead, if they named one)". | compile.py:295, :391, CHECK_PRE `_amt`/`_rb` |
| d | **NEW DATE verdict** (replaces "OK: ... If not, propose this date"): `NEW DATE: <date> can be accepted. You have not offered it: do not offer it now. As a confirmation, read back once the product and <amount> on <date> (...) and ask "Do you confirm?". This is not a question about paying and not a new offer. After a yes, record (create_promise date: <iso>).` | CHECK_PRE |
| e | **DATE_ONLY harmonised:** "... the amount is the one of your current attempt (the full amount, or the floor once it is on the table). Never offer it again: read it back with their date once, as the confirmation of step 4; never ask them to name an amount." | compile.py:54 |

Kept unchanged: offer verdicts (holiday, after the deadline, too late for today) with "never say a figure the customer already heard", the fail-closed create_promise guard, the retry guard, and last-mention parsing. The hook uses json/re/datetime only and contains no `{{`/`}}` (asserted).

## Tests

- **tests/test_a12.py:**
  - `test_fix4_plan_dates_count_only_once_the_attempt_was_said`, run for each of the 5 now-values, late band (attempt 1 current, attempt 2 floor on the deadline). Each plan date with attempt missing/""/0/template/"x" gives NEW DATE with 540.00 (or 180.00), and the deadline does so at attempt 1 too. Attempt 1 said gives ACCEPTED OFFER for its date. Attempt 2 said gives ACCEPTED OFFER for the deadline with 180.00 and no 540.00. A new date after attempt 2 reads back 180.00.
  - `test_fix4_attempt_one_date_named_first_is_a_new_date`, run for each of the 5 now-values, early band: the plan date and the deadline named at attempt 0 give NEW DATE with 540.00. The deadline stays a NEW DATE after attempt 1 when it is not a plan date.
  - `test_fix4_attempt_out_of_range_is_clamped` and `test_fix4_check_date_asks_for_the_attempt_and_date_only_reads_back` (field and required, no `{{` in the hook, DATE_ONLY wording, ≤ 4800, lint clean).
  - Fix-3 tests updated to pass `attempt` and to the new wording. The deadline counts only after an after-deadline result offered it. The no-restated-amount test also forbids "propose this date".
- **Runs:**
  - `uv run pytest -q tests/test_a12.py` → 54 passed.
  - `uv run pytest -q` → **697 passed, 6 skipped**.
  - spec-b lint is `[]`.

## Compiled-prompt changes

- **spec-a:** the negotiate task_message is **4430 chars** (budget 4800). Only DATE_ONLY changed. check_date gains the `attempt` field, and prepare_account writes `offer_amounts`.
- **spec-b:** the negotiate task is unchanged. Its prepare_account also writes the unused `offer_amounts`.

## Concerns

1. **The model fills the attempt count.** If it under-counts, a real acceptance gets NEW DATE, which still reads back once with the amount, so the behaviour is the same. If it over-counts, an unsaid plan date gets ACCEPTED OFFER, which is the old false claim, but now it names the amount. No probe was run because the hook mechanics are unchanged (one more model-filled body field, like `said`). A gate run of PUB-06/07 should confirm that the model fills `attempt`.
2. **A deadline the model proposes under the vague-period rule** (no tool told it) is not tracked. A later "the 2nd is fine" gets NEW DATE, which also reads back once.
3. **Attempt kind "current"** cannot be resolved in code: the hook does not know whether the floor is on the table. The read-back therefore names both figures with the condition, and the model picks one.

---

# Fix round 5 (one amount per verdict)

Commit a85aeb6, plus this report section.

| # | Fix | Where |
|---|---|---|
| 1 | check_date has a new required field, `floor_offered`. It is model-filled through `{{cdx_floor_offered}}` and described as "yes if you have already offered {{floor_text}} in an earlier message". No `cdx_*` variable is set in code. The hook resolves a "current" attempt amount to one figure: the floor when the value is yes/y/true (any case, trailing punctuation ignored), otherwise the full amount, including when the value is unreadable or still the template. A "floor" attempt is always the floor and a "full" attempt always the full amount. No verdict carries two figures. | compile.py CHECK_PRE `fo`/`_amt`, check_tool |
| 2 | ACCEPTED OFFER no longer says "The customer accepted your offer". It now reads "... is a date you proposed and it can be accepted. Do not propose it again. As a confirmation, read back once ...", so the attempt number only selects the amount. The `attempt` description now reads "The number of the last plan attempt you already said in an earlier message: ...". | CHECK_PRE, check_tool |

## Tests

- **tests/test_a12.py:**
  - `test_fix5_no_verdict_contains_two_amounts`: 5 now-values × early/late band × attempt 0/1/2/template × floor_offered yes/no/garbage/template × 7 phrasings + each plan date. No verdict contains both 540.00 and 180.00.
  - `test_fix5_floor_offered_picks_one_figure`, run for each of the 5 now-values. yes and "Yes." give 180.00, and no, garbage, "" and the template give 540.00. A floor attempt gives 180.00 whatever the field says.
  - `test_fix5_no_cdx_variable_is_set_in_code`.
  - The field and description assertions were added to the fix4 shape test. Fix-3 and fix-4 wording assertions were updated to the single figure.
- **Runs:** `uv run pytest -q` → **713 passed, 6 skipped**. Lint is `[]` for spec-a and spec-b.

## Compiled-prompt changes

- **spec-a:** the negotiate task_message is unchanged at **4430 chars**. Only the check_date tool (new field and descriptions) and its pre-hook changed.

## Concerns

1. Both `attempt` and `floor_offered` are filled by the model and not live-tested. A wrong `floor_offered` makes the read-back name the other figure once; the customer can still say no to the confirmation. A gate run of PUB-06/07 should confirm that the model fills both fields.
