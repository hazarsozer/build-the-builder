# bench/fields — tool fields change (H4, H15)

**Base:** Brief A, as "Northwind Savings Bank". The calendar, bands, ladder and outcome table are unchanged.

## Mutation

1. **Request field.** `create_promise` gains a required enum field `channel`, with values `["bank_transfer", "card", "app"]`. Brief rules 34, 35 and 37 now say:
   - ask how the customer will pay, before the read-back;
   - if the customer names another way, list the three ways and ask once more;
   - if they still name none of the three, record `bank_transfer` and say so in the read-back;
   - read the method back;
   - a change at the read-back gets read back again;
   - a retry sends the same channel.
2. **Response fields.** In the `verify_identity` 200 schema, `verified` is renamed `match` and `wrong_answers_left` is renamed `attempts_left`. `all_verified`, `hint`, `ok` and `error` stay. The brief text does not mention the response fields: the builder must read them from the openapi.

## Hypotheses

- **H4** (tool fields and enums change; an extra required field). The builder must send `channel` on every `create_promise`, including the retry.
- **H15** (bank response fields renamed). The builder's identity logic must not depend on the literal `verified` / `wrong_answers_left`.

## Cases

| id | now | covers | expected |
|---|---|---|---|
| FLD-01 | Thu 2026-11-05 | happy path, card | promise p1 260 on 11-05, card |
| FLD-02 | Mon 2026-10-19 | 1 wrong DOB (`attempts_left` read), Sat 24 Oct named → Mon 26 is a holiday → Tue 27 | promise p1 480 on 10-27, app |
| FLD-03 | Thu 2026-12-17 | 3 wrong answers (`attempts_left` = 0 ends the check) | not_verified / failed |
| FLD-04 | Fri 2026-10-16 | flat refusal → attempt 1 on a Friday → next week's Friday; "cash" twice → bank_transfer | promise p1 1180 on 10-23, bank_transfer |
| FLD-05 | Thu 2026-11-05 | "next Monday"; changes card → bank transfer at the read-back | promise p1 340 on 11-09, bank_transfer |
| FLD-06 | Thu 2026-12-17 | full too much → floor, tomorrow | promise p1 438 on 12-18, app |
| FLD-07 | Mon 2026-10-19 | create_promise 500 once; the retry keeps the channel | promise p1 620 on 10-20, card |
| FLD-08 | Thu 2026-11-05 | `day_month_swapped` hint still works; already paid | paid |

The expected promises carry `channel`. `tools/score.py` compares only `(product_id, amount, date)`, so every promise case also has a `checks` line, "create_promise is called with channel '…'", for the judge. `verify.py` asserts that this line is present.

## How the expected values were computed

`verify.py` re-derives every expected record from Brief A's rules, using the brief's calendar:
- business days are Mon–Fri;
- holidays are 10-26, 11-11, 11-26, 12-25 and 01-01;
- the deadline is the 12th business day (early band) or the 7th (late band) after today;
- attempt 1 is the last business day of this week, or of next week if that day is today;
- a named non-business day becomes the next business day;
- "next X" means X in the following calendar week.

It also asserts the openapi mutation: `channel` is in the enum and in `required`, and the renamed response keys are present.

## Findings: our builder cannot express this brief

- **`channel` cannot be sent.**
  - `nodes.api_tool` emits only the body fields that the compiler passes in `body_props`. `compile.py` passes only `product_id`, `amount` and `date`: the negotiate node at ~l.1086, and the `retry_promise` node and `RETRY_PRE` at ~l.1105.
  - So an extra required openapi field is silently dropped. The spec schema has no slot for extra tool arguments and no enum-to-customer-words mapping, so the spec author can only put it in `extra_rules`, which no tool reads.
  - On a mock that enforces `required`, every `create_promise` fails. With the retry and the failure path, every promise case closes `no_promise / service_error`.
- **Renamed response fields break identity.**
  - `VERIFY_POST` (compile.py ~l.424) reads `verified` and `wrong_answers_left`, and the identity node maps `data.wrong_answers_left` (~l.1024).
  - With `match` / `attempts_left`, a *correct* first answer falls through to "This answer does not match our records … Wrong answers left: None."
  - After 3 wrong answers, the "check over" response is `ok: false`, which hits the "could not be read, NOT a wrong answer" branch. The `id_left == 0` transition never fires.
  - FLD-02 and FLD-03 should fail.
  - The schema has no response-field mapping.
- **Our mock bank does not serve the mutation.** See `bank_patch.md`. On our bank as it is, all 8 expected ledgers replay and pass `score_ledger`. But the bank still returns `verified` / `wrong_answers_left` and ignores `channel`, so a bench run on it would not exercise H15 or the `channel` requirement.
