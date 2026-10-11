# Task A9 report: live failures from diag-t5r5

Base: candidate-1 @ 58c3477. The worktree branch was created from builder, so it was reset to candidate-1; it had no commits of its own.
Commits: 3bd8a4b..5fa1bbe (7 commits, plus this report).
Full suite: `uv run pytest -q` gives **557 passed, 6 skipped** (538 at base). `lint()` is clean for spec-a and spec-b, and both compile.

## Item 1: the model must see set_product's answer (multi-product)

### Probe (live, chat API only, slot `probe-hooks`)

- Script: `scratchpad/probe_a9.py`, run as a temporary copy in the worktree root and then deleted. Secrets were read from env.sh inside Python, never printed.
- Workflow: one conversation node. Its only tool is the real `Multi.set_tool("api")` for spec-b, with the real `SET_POST` (the script asserts this). The pre-hook is replaced by one that writes into `mp_msg` a random token that appears nowhere in the prompt (`... say exactly: The code word is Zanzibar4105.`).
- Run 1 (call 2b38902f) was inconclusive. The bank 404s an unseeded customer_id, so the platform logs a `function_call_error` and skips the post-hook. The pre-hook's vars write still persisted. The model said "technical error".
- Run 2 (call 26dc5ce1) pointed the tool url at httpbin.org/post (always 200), with everything else the same. Tool result event: `{"status":"success","data":{"ok":true,"what_to_do_next":"NOT accepted, nothing changed: say exactly ... Zanzibar4105.","phase":"probe_phase"}}`. The agent replied **"The code word is Zanzibar4105."** The response mapping `data.phase -> mp_phase` also worked.
- **Proven:** a post-hook's `result['data']` rewrite is the tool result the model reads. Earlier calls already showed that code_exec returns `data: null`. No simulation was started.

### Changes
| Change | Where | Tests |
|---|---|---|
| `SET_MODE = "api"`. The docstring records the probe. Body fields have no `{{sp_*}}` template defaults any more, and `API_ARGS` ignores any unfilled `{{...}}` value. | multi.py:29, 14-24, 345-350, set_tool api branch | test_multi.py:470 `test_a9_set_product_is_api_mode_and_model_reads_the_answer`, :483 `test_a9_unfilled_template_args_are_ignored`, :489 `test_code_mode_still_works`. Sim/test helpers now run pre-hook, post-hook and response mapping (test_multi `Sim.say`, test_generalise `_sp_call`/`_sp_props`). |
| A shared date is capped even when products are reported one at a time. An agree for a single product counts as shared when another product already agreed with the same requested date (stored as `asked`). The `reject` policy still rejects. | multi.py:213, :271 | test_multi.py:314 (PUB-13 r3 replay: p2 capped to 23 Oct, read-back says why), :328 |
| A turn cap or a goodbye never notes an agreed product as refused. negotiate's `max_turn_transition` and a new structural `neg_event` value `goodbye` both go to a new code node `negotiate_end`. If any product is agreed, it gets one engine read-back, recorded on a yes; otherwise the call goes to `wrap_done`. After that, a "change" at the read-back or its turn cap goes through `readback_reset` to `wrap_done`, never back to negotiate (`mp_ended`). `mp_readback` is kept current on every set_product call. In every wrap, an agreed product that was not recorded gets `AGREED_NOTE`: the wrap's own note (e.g. hardship), or **no note** when that note would be the refused note. | multi.py:334, 437-452, 478, 656, 749-753; compile.py:1742-1763 | test_multi.py:570, 586 (PUB-12 r3 replay: p1 recorded, p2/p3 refused), 598 (no confirmation: p1 gets no refused note, no second negotiation), 613, 622, 631, 638 |

## Item 2: attempts counted in code

- `set_product(refused)` is called after **every** refusal. `_refuse` increments `tried` in mp_state. While attempts remain, it returns "Refusal noted (attempt k of n ...). Make attempt k+1 now: <text>" and adds no "Next is" line for the same product. At the band's count it marks the product refused: "Make no more offers for it: no other amount, no other date". `_next` starts a pending product at attempt `tried+1`. `attempts_made` is removed from the schema and the code (multi.py:276-292, `_next`, main block `still`).
- Prompt: step 3 says to call refused at once, before speaking, and to make only offers that a result or the plan gives. Step 4 says "refused, after EVERY refusal" (compile.py:1716, :1720). Tool outcome description updated.
- Stopgap kept as well: each 1-attempt plan in products_text ends with "After one refusal make no other offer for it (no lower amount, no other date)." (multi.py:66).
- Tests: test_multi.py:527, 542, 549. Older tests were moved to the new `Sim.refuse(pid, n)` helper. `test_r3_missing_attempts_counts_as_zero` was replaced (its semantics no longer exist).

## Item 3: PUB-07 (day in pieces or in words)

- Each calendar row starts with its day number in digits and words: `- the 26th (twenty-sixth): Monday 26 October 2026 (2026-10-26): BANK HOLIDAY ...` (`ORD_WORDS`/`day_label`, compile.py:300-311, :334). The multi calendar uses the same function.
- Both templates gain `DAY_NUMBER` ("a day number is an exact day, not a vague period: follow the first calendar row with that number") and `CUT_OFF` ("a message cut off mid-phrase ... is not an answer or a refusal and uses no attempt or ask: say only "Go on." and read it with the next message") (compile.py:47-49, step 1 / step 2).
- Tests: test_compile.py:424 (holiday row label plus st/nd/rd/th/11-13 suffixes), :439.

## Item 4: PUB-04 (date accepted without an amount)

- `DATE_ONLY`: "A date with no amount: the amount is the one of your current attempt (the full amount, or the floor once it is on the table). Propose that amount with the date as a yes/no question; never ask them to name an amount." This is the first bullet of single step 1 and is also in multi step 2 (compile.py:50).
- The single step 1 gets the number-combining example: `"twelve"... "fifty" = 1250, "four hundred"... "and twenty" = 420; a short number right after a partial one is the rest of it` (compile.py:1434).
- Test: test_compile.py:446.

## Item 5: wording (audit-judge-wording)

- Attempt plans: "propose the **lower amount** X" replaces "propose the floor amount X". "the lower amount X is already on the table" replaces "the floor X ..." (compile.py:275-281).
- New hard rule in the system prompt: "Your instructions use internal words (floor, band, persuasion plan, attempt, product_id). Never say them to the customer..." (compile.py:1264).
- Wrong security answer: `what_to_do_next` replaces "does not match the details on file. Say so" with `Say "I am sorry, that is not correct." (never "records" or "on file")` (compile.py:742).
- The record rule bans "recorded, registered, saved, booked or done" until a tool result says so. Before that the agent says 'noted', replacing "say 'note down' or 'register'" (compile.py:1262-1263). Multi step 5 and the read-back task now say "recorded or registered".
- Test: test_compile.py:466.

## Item 6 (controller addition): PUB-06 re-gate (cut-off message, refer-back)

- A cut-off message is not a refusal and does not advance the ladder (`CUT_OFF`, item 3; it names "what the").
- `MENTIONED` (the Task 5 L1 refer-back) keeps the refer-back and drops the number restatement: `Asked about it again ("what is the minimum?", "how much?"): "That is the <minimum payment / amount> I mentioned. Can you pay it <date of that offer>?": no figure, no new date (keep that date unless they refused it), never a new offer, never "The rest stays overdue." again.` (compile.py:52-54).
- Tests: test_compile.py:457. The L1 test in test_multi was updated to the new wording.

## Changes to the compiled prompts (spec-a / spec-b)

Dumps: `scratchpad/prompts-base/*.txt` and `scratchpad/prompts-new/*.txt` (from `scratchpad/dump_prompts.py`).

- **system_prompt (both):** the record rule is widened ('register'/'registered' banned too; say 'noted'). New internal-words line. Size A 1312→1498, B 1573→1759.
- **negotiate task, spec-a (3999→4755 chars):** step 1 gets the number example, and the DATE_ONLY, DAY_NUMBER and CUT_OFF bullets. The MENTIONED line is the new refer-back. The `{{attempt_plan}}` runtime text says "lower amount".
- **negotiate task, spec-b (4408→5184 chars):** step 2 gets DAY_NUMBER (after the vague rule), CUT_OFF and DATE_ONLY (after the amount rule). Step 3 adds the per-refusal set_product call. The step 4 refused bullet is reworded. Step 5 says "recorded or registered". New MENTIONED line. Runtime `products_text`: 1-attempt plans get the no-further-offer line, and plans say "lower amount". The calendar rows (both specs, runtime `calendar_text`) carry day-number labels.
- **spec-b negotiate node:** set_product is an api_call to `/get_account` with pre-hook (state machine) and post-hook (`SET_POST`). The schema drops `attempts_made`. The refused description changed. neg_event enum + description gain `goodbye`. New transition to `negotiate_end`, and max_turn_transition is now `negotiate_end`.
- **spec-b new node** `negotiate_end`. `readback_reset` gains a `wrap_done` route when `mp_ended == yes`. The readback task says "recorded or registered".
- **identity (both):** the verify_identity post-hook's wrong-answer `what_to_do_next` (text above).
- Prompt budgets (our own, Task 7b M6) were raised. Single: 4000→4800. Multi: 4600→5300. The test comments name A9.

## Concerns
1. **Api mode on a failing bank:** a non-2xx get_account is a `function_call_error`. The post-hook is skipped, but the pre-hook's state change persists. If the model then retries a `refused`, it counts twice. An `agreed` is idempotent. This matters only during a bank outage.
2. **Ledger visibility:** every set_product call is now a read-only `get_account` request in the bank ledger. A literal judge reading the tool list may see many get_account calls. Not observed yet.
3. **Agreed but never confirmed:** an agreed product the customer never confirms (after the one end read-back) now gets **no note**. The close falls back to the wrap's fallback (no_promise/refused for wrap_done). If a brief requires a note for every product without a promise, this leaves one product un-noted. No reason in spec-b's list fits "agreed, unconfirmed".
4. **Goodbye false positives:** a `goodbye` false positive (min_confidence 0.6) jumps to the read-back or wrap early. The description limits it to "only goodbye, with no date, amount, refusal or question". max_node_turns is still 30 (not lowered to 16 as diag suggested), so long legitimate calls are not cut.
5. **Wording risk:** the "Go on." reply and the "<minimum payment / amount>" placeholder are prompt-level only. A literal judge may still read "Go on." as a prompt. Only live runs (controller's gate) can confirm items 3, 4 and 6.
6. **Probes:** two chat conversations were run on probe-hooks (the first was inconclusive because of the bank 404). The probe-hooks slot now holds the probe workflow (version 4).

---

# Fix round 1 (review: spec ❌)

Commits 3ef0bf3..2880730 (5 commits) plus this report section.

| # | Fix | Where | Tests |
|---|---|---|---|
| 1 | Reverted AGREED_NOTE. An agreed product that was not recorded gets the wrap's END_NOTE again, which is the refused note at wrap_done and wrap_not_talk (brief B rule 8: every product without a promise is noted). The negotiate_end read-back still gives agreed products their chance to be recorded first. Concern 3 of the first report is resolved by this. | multi.py:493 (constant removed from `wrap()`) | test_multi.py:650 `test_a9_timeout_unconfirmed_agreed_product_still_gets_a_note` (p1/p2/p3 all noted refused), :691 `test_a9_every_wrap_notes_every_product_without_a_promise` (wrap_hardship → hardship, wrap_not_talk / wrap_done → refused, all three products noted) |
| 2 | The set_product description is now "Report a product's answer. CALL it after EVERY refusal of an offer (refused, before you speak), and when a product is agreed or settled (agreed, dispute, paid). Then follow what_to_do_next." | multi.py:628 | test_multi.py:607 |
| 3 | A refusal counts only after an offer for that product. Code notes an offer (`P['offered']`) when a result names that product's attempt (`_next`), on a successful agree, or after a Not-yet answer. A `refused` with no offer noted returns `Not yet: make the offer first. The question about paying today uses no attempt. If you have not made attempt 1 for the X, make it now: <attempt 1>. If the customer just refused exactly that offer, call set_product with outcome refused for it again.` It uses no attempt. Prompt step 1 (multi) adds: `A "no" to the today question uses no attempt: never report it as refused.` | multi.py:293-301, 196, 276; compile.py:1709 | test_multi.py:554 (one-attempt car loan stays pending, 140.00 offer asked for, second refused counts), :567 (an offer named by a result counts at once); test_a9_attempts_counted updated (first call → Not yet). The `Sim.refuse` helper repeats the call after a Not-yet. |
| 4 | A band with 0 attempts never indexes `att`. `_next` says "Its plan has no offers: make no offer for it and call set_product with outcome refused for it now". `_refuse` marks it refused at once (refused note). products_text adds "Make no offer for it." | multi.py:193-195, 290-292, 67 | test_multi.py:574 (early band with `attempts: []`; review, refused, wrap_done notes all three) |
| 5 | neg_event `cannot_pay_or_too_much` description (single and multi) adds: `A message cut off mid-phrase ("you tell me what the", "Monday the.") is never cannot_pay_or_too_much: it is continue.` | compile.py:1620, 1743 | test_compile.py:446 |

Compiled-prompt deltas in this round:
- spec-b: tool description (item 2), multi step 1 today-question line (item 3), and the extraction description (item 5). Negotiate task is 5258 chars (budget 5300).
- spec-a: extraction description only (item 5). Task is 4755 chars.

Lint is clean for both specs.

Test runs (from the worktree root):
- `uv run pytest -q tests/test_multi.py tests/test_compile.py` → `109 passed, 2 skipped in 0.64s`
- `uv run pytest -q` → `562 passed, 6 skipped in 19.34s`

New concern: when the model really did make the first offer for a product but no result had named it (for example the first product right after the today question), the first refused call returns Not-yet and the model must call refused again. That costs one extra tool call, and there is a small risk the model repeats the offer instead of re-reporting. The Not-yet text says to call again if the customer just refused exactly that offer.

---

# Fix round 2 (re-review: items 1-5 ADDRESSED, one new Important)

Commit e8eeacd plus this report section. The ruling was text only, so there is no state change.

- **Change** (multi.py:296-299): the answer to a `refused` with no noted offer used to begin "Not yet: make the offer first ... make it now: <attempt 1 text with amount and date>". It is now:
  `Not counted yet. If you already made attempt 1 for the <product words> and the customer refused it, call set_product with outcome refused for it again now, before you speak. If you have not made it yet (a no to the today question is not a refusal), make attempt 1 of its plan.`
  The common case comes first, and the message carries no amount, date or offer wording. State handling is unchanged: the product is marked `offered`, `tried` is not incremented, and the product stays pending.
- **Tests:** test_multi.py `test_fix1_refusal_before_any_offer_is_not_counted` asserts the exact opening, "before you speak", and "make attempt 1 of its plan". It also asserts there is no number other than "attempt 1" (so no 140, no 2026), no weekday or month names, and no "propose", "lower amount" or "full amount". `test_a9_attempts_counted_in_code_per_refusal` and the `Sim.refuse` helper now key on "Not counted yet.".
- **Compiled prompts:** unchanged. This text is runtime state-machine output only.
- **Runs:**
  - `uv run pytest -q tests/test_multi.py` → `63 passed, 1 skipped in 0.50s`
  - `uv run pytest -q` → `562 passed, 6 skipped in 19.34s`
  - Lint is clean for spec-a and spec-b.
