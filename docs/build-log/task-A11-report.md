# Task A11 report: set_product survives parallel tool calls

Base: a178772 (A10 tip; the branch was reset to it and had no work of its own).
Commits: eecb6cd (code + updated tests), 9cfba01 (new A11 tests), plus this report.
Full suite: `uv run pytest -q` gives **594 passed, 6 skipped** (588 at base). `lint()` returns `[]` for spec-a and spec-b, and both compile.
No simulations. Two chat-API conversations ran on slot `probe-hooks`: one to learn the platform's behaviour and one to prove the fix.

## Evidence (call 922f2517, PUB-12, gate a9c, read with freya.get_call)
- The model sent three set_product calls in parallel. Their start times were 21.76 (p2 refused), 21.82 (p1 agreed 2026-10-15 full) and 21.99 (p3 refused). They completed at 22.06, 22.26 and 22.27.
- Each pre-hook read the same mp_state, changed its own product, and wrote back the **whole** JSON. p3 completed last, so its copy won. That copy lost two things: p1's agreement and p2's `offered` flag. As a result p2's next refusal again answered "Not counted yet".
- Later, `review` answered "Not every product is settled yet. Next is the credit card ...". The model still said "Your payment is noted. Goodbye.". At goodbye, negotiate_end found nothing agreed and went to wrap_done. The wrap noted p1, p2 and p3 as refused.
- Offline reproduction against base (`scratchpad/a11/oldbatch.py`): I ran the base set_product on the same three bodies with the merge rule from the probe below. It prints `p1 status after the batch = pending`.

## Probe 1: how parallel hooks and var writes behave (call f58d6ed0)
- **Setup:** one tool `mark` (api_call to httpbin), called three times in parallel (a, b, c). Each pre-hook does four things:
  - records what it sees,
  - busy-waits 0.6 s,
  - writes its own key `k_<item>`,
  - read-modify-writes a shared `shared` string.
  The post-hook reports the vars it sees.
- **Results:**
  - **All three pre-hooks read the same snapshot.** Each saw `shared=""` and every `k_*` as `none`.
  - **Writes merge per variable.** `k_a`, `k_b` and `k_c` all persisted. For the shared key the last writer wins: `shared` ended as `"|a"`, so b's and c's appends were lost.
  - **Post-hooks read the merged live table, not their own call's copy.** All three posts saw all three `k_*` values and `shared="|a"`. Even b's post saw that, although b's own pre had written `"|b"`.
- **Consequence:** one variable per product, written only by that product's call, never loses an update. A shared JSON always can. A per-call message in a shared variable (mp_msg) is also unsafe, because a sibling's write can replace it before the post-hook reads it.

## Design chosen and why
I chose per-product variables. The alternative was one set_product call carrying several updates. A prompt rule cannot stop the model from making parallel calls; the live failure happened despite one. Per-product variables are safe whether the model makes one call or several. They also avoid array-typed body leaves, which would need an untested `{{sp_*}}` default.

1. **State split** (multi.py:103-137):
   - `mp_state` is written only by prepare_account (order, amounts, plans, calendar). set_product never rewrites it.
   - Each product's changing fields (`DYN`: status, amount, date, capped, extra, asked, offered, tried, ack, seq, own) live in `mp_p<num>`. Only that product's call writes it.
   - The "offer made" flag that a result sets for the next product goes into `mp_o<num>`. It is only ever set to `yes`, so a write can never be lost.
   - `LOAD`/`_load(vars)` lays these over mp_state. Every reader uses it: the pre-hook, the post-hook, record_plan, negotiate_end and every wrap. `load_state(v)` exposes it to tests.
2. **Pre-hook** (`SET_PRE`, multi.py:362-410):
   - It runs the existing state machine on its own snapshot.
   - It writes `mp_p<num>` only for the products it targets, with an ack prefixed by the product ("The credit card ending 9015: Noted for now ...").
   - It writes the batch counter `mp_seq`. Every call in a batch reads the same N and writes the same N+1.
   - Review, unknown-outcome and technical-error answers go to `mp_ax`.
   - It no longer writes mp_phase, mp_msg, mp_readback or rb_answer.
3. **Post-hook** (`SET_FIN`, multi.py:412-465; api mode `post = head + LOAD + SET_LIB + SET_FIN + SET_POST`, multi.py:753):
   - It builds the answer from the merged state. It collects every ack with the current `mp_seq`, so a result shows every answer of its batch that has landed.
   - It computes phase and next step with `_next`, and writes mp_phase, mp_readback, rb_answer and mp_msg.
   - In a batch (more than one ack) it adds "Still open: the ...".
   - If any ack of the batch already says what to do next, there is no "Next is" line. This covers refused with attempts left, Not counted yet, and NOT accepted.
   - The last result of a batch is the most complete. The response mapping writes the matching phase.
   - Code mode is still supported: pre + FIN run in one code_exec.
4. **negotiate_end** (multi.py:562-575) rebuilds mp_readback from the merged state, so a read-back built before the batch landed is never spoken.
5. **Item 2, review while products are open** (multi.py:446-450). The result now starts:
   `NOT every product is settled: the personal loan and the car loan are still open. Nothing is noted or recorded for them and the call is not over: never say noted, recorded or goodbye now.` followed by the next step.
   - Prompt (compile.py:1907, multi step 4): `Say a product is noted only after a result that says "Noted" for it; ... A result that says a product is still open ("NOT every product is settled", "Still open"): nothing is noted for it and the call is not over; never say noted, recorded or goodbye, continue with that product.`
6. **Item 3, confirmed products** (multi.py:511, :596-611):
   - record_plan stores the confirmed product ids in `mp_confirmed`.
   - In every wrap, an agreed product that was confirmed but not recorded gets FAIL_NOTE (the service-failure note) plus the failure sentence, never END_NOTE (refused).
   - With state no longer lost (item 1), the PUB-12 path itself works: an agreed product reaches the engine read-back at negotiate_end and is recorded on a yes.

## Probe 2: proof (call 352b3ee6, slot probe-hooks version 8)
- **Setup:**
  - Seeded a PUB-12 practice session, customer c_858b83ae-ee2, and verified both fields (all_verified true).
  - Ran setup and prepare_account offline on the bank's products and passed the resulting vars as customerVariables.
  - The workflow is one node with the **compiled spec-b negotiate task_message, system prompt and set_product tool, unmodified**.
- **Turn 1:** the customer said "I will pay the credit card in full tomorrow, and the car loan in full on Friday the sixteenth. I am not paying the personal loan."
  - The model made three parallel calls: p3 agreed 2026-10-16, p1 agreed 2026-10-15, p2 refused.
  - The third result read: "The credit card ending 9015: Noted for now ... The personal loan: Not counted yet. ... The car loan: Noted for now ... Still open: the personal loan."
  - The agent said: "The credit card and car loan are noted. For your personal loan, can you pay the full amount of 1320.00 on Friday 16 October 2026?"
- **Turn 2:** the customer said "No. I am not paying the personal loan." The result was "Refusal noted (attempt 1 of 2 ...). Make attempt 2 now". The agent offered 440.00 on 23 Oct.
- **Final variable table:**
  - mp_p1: agreed 510.0 on 2026-10-15
  - mp_p3: agreed 140.0 on 2026-10-16
  - mp_p2: pending, tried 1
  - mp_readback: "To confirm, you will pay 510.00 for your credit card ending 9015 on Thursday 15 October 2026; and 140.00 for your car loan on Friday 16 October 2026. Do you confirm?"
- **Conclusion:** no update was lost. The bank ledger has get_account reads only.

## Tests
- Updated:
  - Tests that read product status from `mp_state` now use `load_state` (test_multi, test_generalise).
  - `_sp_call` (test_generalise) also runs the post-hook.
  - Message assertions now expect the product prefix ("The personal loan: NOT accepted ...").
  - `Sim.refuse` matches "Not counted yet." anywhere in the result.
- New `Sim.batch` (test_multi.py:175) replays parallel calls with the probe-1 rules: same snapshot, per-variable merge, then the posts on the merged vars.
- New tests:
  - test_multi.py:853 `test_a11_pre_hook_writes_only_its_own_product_variable`
  - :864 `test_a11_pub12_replay_parallel_calls_keep_the_agreed_product`: live batch replay, then the promise is recorded and p2/p3 get refused notes.
  - :889 `test_a11_parallel_agreements_all_kept_and_read_back`: PUB-11, three parallel agreements.
  - :900 `test_a11_goodbye_after_a_parallel_batch_reads_the_agreed_product_back`
  - :910 `test_a11_review_with_open_products_forbids_noted_and_goodbye`: result wording and prompt line.
  - :925 `test_a11_confirmed_product_is_never_noted_refused_by_any_wrap`: every wrap node.
- Runs:
  - `uv run pytest -q tests/test_multi.py` → 71 passed, 1 skipped
  - `uv run pytest -q` → **594 passed, 6 skipped in 20.29s**

## Compiled-prompt and workflow changes (diffed against a178772 with `scratchpad/a11/cmp.py`)
- **spec-a:** no change (0 paths differ).
- **spec-b:** 9 paths differ.
  - negotiate task_message: step 4 wording above, 5129 → 5336 chars (budget 5500).
  - set_product pre_process_code and post_process_code.
  - Code of negotiate_end, record_plan, wrap_done, wrap_hardship, wrap_not_talk and wrap_recorded (`_load`, mp_confirmed, read-back rebuild).
  - System prompt, tool schema and descriptions are unchanged.

## Concerns
1. **Early results in a batch can be partial.** A post-hook that runs before a sibling's pre-hook has landed shows only the answers so far. In probe 2, the first two results lacked the personal loan's answer and still said "Next is the personal loan ... attempt 1". The last result is complete, the state is always complete, and mp_phase comes from the last mapping. If the model acts on an early, partial result, it repeats a step, but it loses nothing.
2. **The shared-date cap inside a batch.** If two products agree on the same date in parallel calls (not one ALL call), neither sees the other. A date after one product's deadline is then answered "NOT accepted ... offer its deadline" instead of being capped. Nothing is lost; the customer is asked again. A single ALL call still caps.
3. **Several parallel calls for the same product** (for example agreed and refused at once) still overwrite each other: last writer wins on its own variable. I have not seen this happen.
4. **Ack seq relies on the same-snapshot rule from probe 1.** If the platform ever serialised hooks, each call would get its own seq, and each result would show only its own ack plus the next step. That is the pre-A11 behaviour, and it is still correct.
5. **mp_confirmed is only defensive.** The read-back "yes" always goes to record_plan and then wrap_recorded, so no current path reaches another wrap after confirmation. A confirmation given to the model's own read-back in negotiate, as in the live call, is not tracked. In that case the engine read-back (on review, or at negotiate_end) asks again.
6. **Probe slot and ledger.** probe-hooks now holds probe 2's workflow (version 8). Practice session c_858b83ae-ee2 has get_account reads only. Probe 1 used httpbin, not the bank.
7. **Item 2 is prompt-level plus result wording.** Only a live run can show that the model stops saying "noted"/goodbye after an open review.
