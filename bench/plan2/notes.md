# bench/plan2 — two-part split plan (H13)

> **Extraction-only.** Use these cases to check the spec and the compile, not to run calls on a bank. A bank that behaves like the official one returns "this product already has a promise" for a second promise on the same product. So `bench/run.py` must not run the plan2 cases on our bank, because 6 of the 8 would fail for the bank's reason, not the agent's.

**Base:** Brief A, as "Larkspur Bank". The calendar, bands, ladder, floor and outcome table are unchanged.

## Mutation

Brief A rule 36, "One promise per product", is replaced by a split plan. Rules 33, 34, 37 and 41 are adjusted to match.

- A customer who cannot pay the full amount at once may **ask** to split it. The agent never offers the split first.
- The split covers the full past-due amount, as two `create_promise` calls for the same product.
- **Part 1:** at least 40% of the past-due amount, rounded **up** to the cent, on a date that meets rule 19 (on or before the deadline).
- **Part 2:** the rest, on a business day after part 1 and no later than 30 calendar days after today. If day 30 is not a business day, it snaps back to the business day before.
- With no amounts named, the agent proposes 40% on the deadline, and the rest on the latest part-2 date.
- A part-1 amount below 40% gets one counter-offer of the minimum.
- The split replaces the floor offer.
- A request for more than two parts, or for lower instalments, still transfers to `restructuring`.
- One read-back, one "Do you confirm?", then part 1 and then part 2.

Two choices were mine, because the task left them open:
- "within 30 calendar days" is counted from **today**, not from the part-1 date;
- part-1 rounding is **up** to the cent.

## Hypothesis

- **H13**: a brief that allows something Brief A forbids, here more than one promise per product.

## Cases

| id | now | covers | expected promises |
|---|---|---|---|
| PL2-01 | Wed 2026-10-14 | split, customer names both parts; "next Friday" = deadline | p1 500 on 10-23; p1 500 on 11-06 |
| PL2-02 | Fri 2026-11-06 | **first part too small**: 300 → minimum 493.83 (0.4 × 1234.56 = 493.824, rounded up); "early December" → asked → 4 Dec (day 30 = Sun 6 Dec → Fri 4 Dec) | p1 493.83 on 11-06; p1 740.73 on 12-04 |
| PL2-03 | Fri 2026-11-20 | **normal single promise**, "next Tuesday" | p1 450 on 11-24 |
| PL2-04 | Fri 2026-12-18 | part 2 named 29 Jan > latest (day 30 = Sun 17 Jan → Fri 15 Jan) | p1 400 on 12-24; p1 400 on 2027-01-15 |
| PL2-05 | Wed 2026-10-14 | part 1 after the deadline → deadline 10-23; part 2 exactly day 30 (Fri 13 Nov) | p1 300 on 10-23; p1 300 on 11-13 |
| PL2-06 | Fri 2026-11-06 | three monthly payments → restructuring | none, transferred / restructuring |
| PL2-07 | Fri 2026-11-20 | split with no amounts: the agent proposes 40% on the deadline (12-09) and the rest on the latest date (day 30 = Sun 20 Dec → Fri 18 Dec) | p1 380 on 12-09; p1 570 on 12-18 |
| PL2-08 | Fri 2026-12-18 | split; the first create_promise returns 500 once | p1 350 on 12-23; p1 350 on 2027-01-08 |

## How the expected values were computed

`verify.py`:
- uses Brief A's calendar, deadline and weekday words;
- uses `latest_part2(today)`, which is today + 30 calendar days, snapped back to a business day;
- uses `part1_min`, which is the ceiling to the cent of 0.4 × past_due, computed with `Decimal`;
- implements `split()`, which applies the rules above to each customer-named split.

It asserts:
- each split sums to the past-due amount;
- part 2 is after part 1;
- every date is a business day;
- the boundary values: 493.83, 12-04, 2027-01-15, 11-13 and 12-18.

## Findings

- **Our spec schema cannot express this.**
  - Nothing in `negotiation`, `confirmation` or `multi_product` allows more than one promise per product. Each of them assumes one `promise_amount` / `promise_date`.
  - `compile.py` moves to `close_promise_recorded` as soon as `promise_status == "recorded"` (~l.1068). That ends the call after part 1, so part 2 is never recorded.
  - `extra_rules` could describe the plan in the prompt, but the graph would still close after the first success.
  - The rule needs these things that the schema lacks:
    - a "max promises per product" field, or a plan structure: parts, minimum share, rounding, latest-date rule;
    - a promise loop in the negotiate node;
    - a second read-back field.
  - The date and amount arithmetic could go into `custom_code`, but the flow cannot.
- **Neither our mock nor (probably) the official one accepts it.** `bank/server.py` `t_create_promise` returns `"this product already has a promise"` for a second promise on the same product. That error text was copied from probes of the official mock. A replay of the expected ledgers on our bank fails all 6 split cases, and passes PL2-03 and PL2-06.
  - To run this brief, the bank must allow N promises per product. Either drop the duplicate check, or limit it by a max read from the brief, such as a `"max_promises_per_product": 2` seed key.
  - If the organisers ship such a brief, they must change their mock too.
