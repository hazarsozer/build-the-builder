# Task A8 — outage resilience (AFTER-PLAN §8): report

Worktree: `/home/hazarsozer/Dev/freya-builder-kit/build-the-builder/.claude/worktrees/agent-a001fcb1893ea692d`
Branch: `worktree-agent-a001fcb1893ea692d`. Base `960bfc7`. Commits `37de052` (compiler fixes) and `a3dd696` (tests), plus the commit that adds this report.

## Failure model tested

For every code node of the compiled spec-a and spec-b workflows (except `setup_retry`, which only runs after `setup` failed, so failing it as well is a double failure):

- **skip**: the node writes none of the keys it computes. The sandbox did not run it.
- **raise@k**: the node's own code runs and an exception that `except Exception` does not catch escapes at the k-th variable write statement. Writes made before it stay. This covers every k up to a complete run.

The node is entered with realistic variables: call variables, then setup, then a loaded two-product account, then (for read-back nodes) a written read-back. The engine is then simulated. Passthrough nodes route on their conditions. Downstream code nodes run for real, and the failed node keeps failing if it is entered again. The walk stops at a conversation node or the end node. The test asserts:

- **End node**: `outcome` is in the openapi `close_call` enum, and (outcome, reason) is a pair in the spec's outcome table (or the outcome is reasonless). The farewell is a real sentence with no placeholder.
- **Conversation node**: every computed variable named in `on_enter`, `task_message` or `terminal` is set and not empty or None.
- **Onward**: every conversation node still reachable names only variables that are set on the way. Code nodes on the way are assumed to work. The end node is reached with `outcome` and `farewell` set.
- **Graph**:
  - every node reaches the end node;
  - every conversation node has a `max_turn_transition`;
  - every passthrough node ends with `always`;
  - no cycle is made of passthrough nodes only;
  - `close_call` is a lifecycle tool that runs at `end_of_call`.

## Tests that failed before any fix (base 960bfc7): 40 failed, 23 passed

| Failing cases | Why |
|---|---|
| All 18 `close_*` nodes in each spec (36) | If a close node does not run, `outcome` is unset. close_call then drops the field (an unresolved `{{outcome}}` is dropped), so it sends no valid outcome. `goodbye` speaks the literal `{{farewell}}`. |
| `a/prepare_account`, `b/prepare_account` | The transitions were `account_state == error` → close, else `always` → negotiate. A snippet that did not run went to negotiate, which then spoke empty or placeholder `debt_statement_text`, `product_words` and amounts/dates (`mp_msg`/`first_ask_text` in spec-b). |
| `a/setup`, `b/setup` | `setup` → opening `always`. `third_party` was reached with `callback_rules` unset. The prompt carries the literal placeholder, and `today_text`/`now_time` are missing in negotiate. |
| `b/human_back_readback` | Harness artefact, not a compiler bug. The entry state had `mp_readback = ''` from prepare_account. The test now enters read-back nodes with a written read-back, as the real call does. |

## Fixes (generic; no brief values)

1. **`builder/nodes.py` `close_node`**
   - Outcome, reason and farewell are written in one statement: `vars.update(dict(outcome=OUTCOME, reason=REASON, farewell=F))`. A partial run can therefore never pair a new outcome with an old reason.
   - A reasonless close writes `reason = ''`.
   - New `close_defaults_code()`.
2. **`builder/compile.py`**
   - `setup` first writes the service-failure close defaults: the `confirmation.failure_event` outcome and reason, and the farewell "I am sorry, there was a technical problem. Thank you for your time. Goodbye." A close node that fails therefore still ends with a valid `close_call` (no_promise/service_error in A and B) and a spoken goodbye.
   - `setup` → `opening` only when `setup_state` is `ok` or `error`. Otherwise (the snippet did not run) it goes to the new node `setup_retry`, which holds the same code and then goes `always` → opening.
   - `prepare_account` → `negotiate` only when `account_state == ok`, which is its last write. Its default is now `close_account_error`. In multi-product mode the single-product part writes `facts` and the multi part writes `ok` at its end. The multi part raises if the facts are not there.
   - The `close_call` pre-hook (`CLOSE_PRE`) also drops an empty `reason`.
   - New helper `fail_close()`, also used by `close_account_error` (same values as before).
3. **`builder/multi.py`**
   - `MP_ACCOUNT_CODE` guard: it proceeds only when `account_state == 'facts'`.
   - `finish` defaults to the service-failure close when the wraps can reach it. The spec-b output is unchanged, because it already defaulted to `close_service_error`.
4. **`builder/lint.py`** `_assigned` also reads `OUTCOME = '...'`/`REASON = '...'`. An empty reason counts as none.

## Compiled-output changes (old = base compiler, new = this branch)

The same in spec-a and spec-b. **No `task_message`, `on_enter` text, extraction schema or system prompt changed.**

- **Node added**: `setup_retry`, a code node with the same code as `setup`, followed by `always` → `opening`.
- **`setup`**
  - code: a first line `vars.update(dict(outcome='no_promise', reason='service_error', farewell='I am sorry, there was a technical problem. Thank you for your time. Goodbye.'))`.
  - transitions: was `[always → opening]`, now `[setup_state ok|error → opening, always → setup_retry]`.
  - `passthrough_default_transition`: `opening` → `setup_retry`.
- **`prepare_account`**
  - code: adds `ACCOUNT_OK = 'ok'` (A) or `'facts'` (B), and the account-state write uses it. In B the multi part adds the guard.
  - transitions: was `[account_state error → close_account_error, always → negotiate]`, now `[account_state ok → negotiate, always → close_account_error]`.
  - `passthrough_default_transition`: `negotiate` → `close_account_error`.
- **Every `close_*` node** (18 in each spec): only the code changed, to `OUTCOME`/`REASON` constants plus the single `vars.update`. The outcome, reason and farewell values are the same, except that reasonless closes (`close_promise_recorded`, `close_paid`) now write `reason = ''`. Transitions are unchanged.
- **Workflow `tools`** (close_call): the pre-hook condition is now `outcome in REASONLESS or not reason`.
- Node count: A 39 → 40, B 57 → 58. Lint is clean for both.

## Existing tests updated

`tests/test_multi.py`:

- `test_spec_a_node_set_unchanged` now includes `setup_retry`.
- `Sim.chain()` returns `reason or None`. Reasonless closes now write `''`, which close_call drops. This fixed 2 tests (`test_pub12...`, `test_all_paid...`) that compared against `None`.

## Tests added

`tests/test_outage.py`, 67 tests:

- `test_failed_code_node_reaches_a_valid_close` (61 parametrised cases: one per code node per spec, each over every failure mode and entry state);
- `test_graph_has_no_dead_end_or_uncapped_loop` (A, B);
- `test_close_call_prehook_drops_an_empty_reason` (A, B);
- `test_setup_writes_valid_close_defaults` (A, B);
- `test_harness_catches_a_close_without_defaults` (a self-check: the walk fails a close with no defaults).

## Full suite

`uv run pytest -q` → `518 passed, 6 skipped in 18.65s` (451 passed before).

## Concerns

- **Early hang-up semantics change.** A call that ends without reaching any close (the customer hangs up mid-call) now sends close_call with setup's default `no_promise/service_error`. Before, the request carried no outcome, which the bank would reject. That is arguably a false "service_error" record. The alternative is to detect a failed close through `<node>_failure_count` in the close_call pre-hook, but that is unverified for code nodes.
- **Single-failure model.** If the sandbox is down for the whole call (setup, setup_retry, the closes and the hooks all fail), placeholders still leak and close_call has no outcome. The only code-free lever is `test_variables` defaults, and whether they merge into simulations is unverified.
- **Engine semantics are assumed, not verified live.** The tests assume a failed `code_exec` writes nothing (or stops at a statement boundary) and the passthrough `always` then fires.
- **Not covered.** The multi `set_product` code_exec *tool* and the api pre/post hooks are tools, not code nodes.
- **Not run live.** The setup, prepare_account and close-node code changed, so the merge-bar re-gate (PUB-A/PUB-B at 3 reps) should run before relying on this.
- **Size.** `setup_retry` adds about 9 KB, a duplicate of the setup code, to each workflow.

## Fix round 1 (reviewer finding: default close on early hang-up)

Ruling applied: `setup` no longer pre-writes `outcome`/`reason`. A call that ends before any close node runs sends
close_call with no outcome and no reason again (the `{{outcome}}`/`{{reason}}` defaults are unresolved and dropped), as
before A8. Kept: setup_retry, the prepare_account inversion, the single-statement outcome/reason/farewell write in close
nodes, the empty-reason drop in the close_call pre-hook, the multi `finish` default.

- `builder/nodes.py`: `close_defaults_code` replaced by `farewell_default_code(farewell)` (writes only
  `vars['farewell']`). Kept deliberately: it touches no close_call field, and without it a failed close node makes
  goodbye speak the literal `{{farewell}}`. Easy to drop if the controller prefers.
- `builder/compile.py`: setup's first line is now `vars['farewell'] = 'I am sorry, there was a technical problem.
  Thank you for your time. Goodbye.'` (was the `vars.update(dict(outcome=..., reason=..., farewell=...))` line).
  No other compiled-output change versus round 0.
- `tests/test_outage.py`:
  - new `close_body(w, v)` builds the close_call body as the engine sends it (single unresolved placeholder → field
    dropped, then the pre-hook);
  - a failed close node may now end the call with no outcome (asserted: body has no outcome and no reason, goodbye
    still speaks a farewell) but never with a wrong one; every other failed node must still reach a valid close;
  - `test_setup_writes_valid_close_defaults` → `test_setup_writes_a_farewell_but_no_outcome`;
  - new `test_early_hangup_sends_no_outcome` (A, B): after call start, after setup, after a loaded account, the body
    has no outcome/reason; after `close_refused` / `close_promise_recorded` ran, it carries their outcome (promise
    without reason).

Behaviour change vs round 0: a close node whose snippet fails now ends the call with no outcome (same as a hang-up)
instead of no_promise/service_error.

Commands and results:
- `uv run pytest -q tests/test_outage.py tests/test_multi.py tests/test_compile.py` → `153 passed, 2 skipped in 1.03s`
- `uv run pytest -q` → `520 passed, 6 skipped in 18.71s`
