# bench/multi-raw — Brief B's multi-product rules as raw material (H1, H8, H4)

**Base:** Brief A plus Brief B, as "Corvane Bank". The rules are the same as A+B, but they come as three documents and no clean brief:

1. `brief-01-policy-extract.md`: the policy RC-4 v4.2, rules 1–43. It merges Brief A and Brief B, numbered, with the original note codes `refused` / `hardship` / `dispute` / `paid` / `service_error`.
2. `brief-02-memo.md`: an operations memo that **changes the note reasons**:
   - `refused` → `declined`
   - `dispute` → `disputed`
   - `paid` → `payment_claimed`
   - `service_error` → `system_error`
   - `hardship` is unchanged

   The memo keeps the `close_call` reasons as they were and rewrites rule 43 with an explicit mapping from note reason to close reason. So the first non-`payment_claimed` note `declined` closes as `no_promise` / `refused`.
3. `brief-03-code-list.md`: the full outcome table, with the note codes and how they map, the queues and the product words.

`openapi.json`: the `add_note.reason` enum is the new code set. Our bank rejects the old codes ("unknown reason").

## Hypotheses

- **H1**: raw material, a memo that overrides the policy, a code list.
- **H8**: every customer has 2–3 products, with mixed bands and deadlines.
- **H4**: changed `add_note` enums. Note reasons and close reasons diverge.

## Cases

Every case has 2 or 3 products, with both bands present. `verify.py` asserts this.

| id | now | covers | expected |
|---|---|---|---|
| MRW-01 | Thu 2026-11-05 | **shared date after one deadline**: all on Fri 11-20; the loan (late, deadline 11-17) is capped | promise: p1 220 on 11-20, p2 840 on 11-17, p3 150 on 11-20 |
| MRW-02 | Fri 2026-10-16 | **one paid, one disputed**, the third promised "next Tuesday" | promise p3 690 on 10-20; notes p1 payment_claimed, p2 disputed |
| MRW-03 | Fri 2026-11-20 | **restructuring after a promise was recorded**: card recorded ("next Friday" = 11-27), loan refused twice, then lower instalments asked | transferred / restructuring; promise p1 400 on 11-27 stays; **no notes** |
| MRW-04 | Thu 2026-12-17 | refuses all; card late (attempt 1 is 780 on 12-18, attempt 2 is 234 on 12-29), car early (one attempt, 12-18) | no_promise / **refused** (note `declined` maps to it); notes declined × 2 |
| MRW-05 | Thu 2026-11-05 | both already paid | paid; notes payment_claimed × 2 |
| MRW-06 | Fri 2026-10-16 | paid + disputed ("closed years ago") + refused | no_promise / **dispute** (the first non-payment_claimed note is p2 disputed); 3 notes |
| MRW-07 | Fri 2026-11-20 | floor on the loan, one date for all (Mon 11-30, within every deadline) | promise: p1 450, p2 240, p3 330, all on 11-30 |
| MRW-08 | Thu 2026-12-17 | restructuring **before** recording: the agreed card promise is not recorded | transferred / restructuring; nothing recorded; no notes |

## How the expected values were computed

`verify.py`:
- uses Brief A's calendar, with a per-product band and deadline (B rule 3);
- implements `shared()`, the shared date capped to each product's own deadline (B rule 6);
- implements `close()`, which is the memo's version of rule 43:
  - a transfer wins and clears the notes;
  - otherwise any promise wins;
  - if every note is `payment_claimed`, the outcome is `paid`;
  - otherwise the outcome is `no_promise`, with the close reason mapped from the first non-`payment_claimed` note in `get_account` order.

It asserts the ladder dates that the `checks` lines quote (MRW-03, 04 and 06), the 11-17 cap in MRW-01, and that every note reason is in the new openapi enum.

## Findings about the spec schema

- `multi_product.notes.reasons` is a free list, so it can hold the new codes.
- `multi_product.no_promise_reason = first_non_paid_note` assumes two things:
  - a note reason named `paid`. Here it is `payment_claimed`.
  - the note reason is the close reason. Here `declined` must close as `refused` and `disputed` as `dispute`.
- No field maps note reasons to close reasons, so a faithful spec needs `extra_rules` or `custom_code`.
- `all_paid_outcome` works if the extractor understands that "paid" now means `payment_claimed`.
- `compile.py` currently has no `multi_product` or `add_note` handling (`rg add_note builder/compile.py` returns nothing). The whole brief depends on the multi-product compile work landing.
