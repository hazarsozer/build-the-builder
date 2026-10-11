# Final fix 2 report (final-review-2.md)

Base: 89bb9e3 (builder, tag gate-green-4). Branch: `worktree-agent-ac148ee19af545c79`. No Freya calls. Not merged.

Tests: 1019 passed, 6 skipped (base: 1008). The new tests are in `tests/test_final_fix2.py` (20) and one addition to
`tests/test_main_dry.py`. I5 shrank the parametrised sample test in `tests/test_layout.py` from 20 cases to 11.
`tests/test_a15.py`, the corpus-word test, passes. All 10 fixture specs compile lint-clean. The negotiate prompts are
unchanged: spec-a 4620 (cap 4800), spec-b 5694 (cap 5800).

## Commits (89bb9e3..df840cf)

| Commit | Finding | What changed |
|---|---|---|
| 1bd459d | I1 | `opening_node` renders the first sentence with `tpl()`. `safe_before_success` keeps the rendered verbatim sentence. In lint, the verbatim exemption matches the rendered sentence: each `{x}` matches `{{var}}` or a few words of prose, without importing compile. New lint error: a single-brace placeholder left in any tts text. |
| 86c0c58 | I2 | `say_for(ev, stage)`: the `stage_say` for that stage, else `say`. An event heard in only one stage uses that stage's sentence. `close_for(stage, id)` builds `close_<id>_<stage>`, but only when that stage's farewell differs from the default close. It is used by `end_target`, `fallback_close`, the transfer closes, callback, declined, the negotiate fail/promise/refused routes and `retry_promise`. Also covered: the ask-once hold line, the support-offer sentence, the multi wrap `LEAD`/`FAIL_SAY`, the per-product "Say:" lines, `cases` checks (stage of the case) and `fixed_sentences`. |
| b387b61 | I3 | `ACCOUNT_CODE` is split into `ACCOUNT_DEFS` and `ACCOUNT_MAIN`. When `same_day_cutoff` is set, `CUTOFF_DEFS` sits between them: past the cut-off, `attempt_date` moves "today" to the next business day; `product_facts` drops today from the allowed dates; the calendar says "too late for a payment today". `CUTOFF_MAIN` runs after the account code: it sets `{{pay_day_text}}` (the single-path first ask's "today" is that variable), rewrites the multi `first_ask_text` and stores `closed` in `mp_state`. The multi `_agree` rejects today with a "too late for today" verdict. The check is inserted into `SET_LIB` at an anchor, with an assert that the anchor occurs once. `cases.Ctx.attempt` and `promise_day` read the cut-off against `now`. |
| 67936a0 | I4 | `dates._offset`: the chosen mention resolves to `(None, 'none')` in two cases. (1) It follows "after" or "before". (2) It is the anchor of a span ("N days/weeks from ...", "a week today"). A vague mention stays vague. "The day after tomorrow" and "the Friday after next" still resolve. `check_date` then uses the model's ISO date (tested end to end). |
| e165b1a | I5 (+M6) | `test_variables`: `customer_id` is `c_test`; `now` is undeclared, so setup reads the real clock; every other call variable is `""`. The review called the title/first/last samples harmful, so they are `""` too. `_sample_now`, `ORG_WORDS` and the Dana/Reyes samples are removed. The tests in test_layout and test_generalise now expect the reverted behaviour. |
| ecd7b73 | M1 (+M9) | The trace's tunnel event is `url_kind="tunnel"`. The RUNBOOK scan uses two `rg` commands (below); both must print nothing. The dry-run test asserts that the trace has no match. RUNBOOK step 1 now says the `final-candidate` tag is created at freeze. |
| 9601590 | M3, M8 | `close_call` `REASONLESS` lists only outcomes whose rows all lack a reason. The multi `FAIL_SAY_DEFAULT` is now "I am sorry, I could not save your promise." |
| df840cf | M10 | `validate(new)` after the fix round is wrapped like the validate at extraction time. |

RUNBOOK secret scan (expected output: none; rg exits 1):
```
rg -n 'fak_[A-Za-z0-9]{8}|sk-ant-[A-Za-z0-9]|FREYA_[A-Z_]+=[A-Za-z0-9_-]{8}|https://[a-z0-9-]+\.trycloudflare\.com' traces/ submission/
rg -nF -e "${FREYA_API_KEY:?}" -e "${FREYA_BANK_TEAM_KEY:?}" traces/ submission/
```
Checked against the existing `traces/`: no matches. A synthetic tunnel event and a `[REDACTED:FREYA_API_KEY]` marker
do not match. A real `fak_` key, an `sk-ant-` key, a `FREYA_API_KEY=` assignment and a tunnel URL do.

## Compiled spec-a / spec-b change

I compared compile output for spec-a, spec-b and the 8 fixtures, with and without a base URL, against 89bb9e3. For
spec-a and spec-b, exactly two things differ:
- **I4:** the embedded `builder/lib/dates.py` source, which adds `_offset` and `_SPAN_W`. It sits in the code of the
  setup/setup_retry/prepare_account nodes, the check_date/create_promise hooks (spec-a) and the wrap nodes (spec-b).
  Everything else in those strings is byte-identical.
- **I5:** `test_variables`: first_name, last_name and title are now `""`, and `now` is removed.

The I1, I2 and I3 changes emit nothing for the reference specs: they have no placeholder in the first sentence, no
`stage_say` and no `same_day_cutoff`. The M3 change is behaviour-neutral for every fixture. The M8 change only affects
the bench multi specs, whose failure event has no sentence.

## Concerns

- **I3 is offline-tested only.** It redefines `attempt_date`, `product_facts` and `calendar_text` inside the code node
  (plain Python, no braces). No live gate has run a brief with a cut-off.
- **I3 rewrites the brief's "today" by regex.** The first ask's "today" becomes "on <next business day>" past the
  cut-off. A first ask such as "today or tomorrow" would become "on Thursday 15 October or tomorrow".
- **I3 uses an anchored insertion into `SET_LIB` for the multi path.** I chose it so that spec-b stays byte-identical.
  The assert fails at compile time if the anchor line ever changes.
- **I2 adds close nodes.** `close_<id>_<stage>` exists only for a spec whose `stage_say` differs from the default close.
  On a multi spec these stage closes are not wrap targets, so they keep their own farewell. That is intended: only
  negotiate closes speak the wrap summary.
- **I4 lowers recall on purpose.** "Not before Friday" and "after Friday" now fall back to the model's date instead of
  Friday.
- **I5 trade-off.** The dashboard warns again that `now` will not resolve, and Call Agent from the dashboard has no
  name or title.
- **Not done:** M2 (triggers.py, a file outside this change), M4, M5 and M7.
