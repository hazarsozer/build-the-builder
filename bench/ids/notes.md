# bench/ids: notes

Test input only. Never show this brief, its cases or these notes to the builder as examples.

## Hypothesis

H2 (identity questions change, per-question wrong-answer limit). Bank: Harbourline Bank (fictional).

## What was mutated (against Brief A)

| Where | Brief A | ids |
|---|---|---|
| Rule 11 | date of birth, then phone last 4 | postcode, then year of birth |
| Rule 12 (new) | none | the postcode is sent whole, once, even when said in pieces |
| Rule 13 | 3 wrong answers in total | 2 wrong answers per question; questions counted separately |
| Rule 14 | hint `day_month_swapped` | hint `partial_postcode`: ask again, not counted |
| Rule 10 | relative may not answer | also may not offer the customer's details |
| openapi `verify_identity.field` | `date_of_birth`, `phone_last4` | `postcode`, `birth_year` |
| openapi `answer` description | DOB as YYYY-MM-DD, four digits | postcode letters and numbers, birth year four digits |
| openapi 200 description, `hint` | enum `day_month_swapped` | enum `partial_postcode`, described as "ask again, not counted" |
| openapi summary, `wrong_answers_left` | 3 in total | two per field |

All other rules match Brief A and are renumbered by +1 after rule 11. Calendar, bands, ladder and outcome table are Brief A's.

## Cases

| Case | `now` | What it tests | Expected |
|---|---|---|---|
| IDS-01 | Wed 14 Oct 11:00 | happy path with the new questions | promise 260 on 2026-10-14 (today) |
| IDS-02 | Tue 20 Oct 15:30 | postcode spoken in pieces (`style: pieces`) | promise 450 on 2026-10-23 ("this Friday") |
| IDS-03 | Thu 29 Oct 10:15 | two wrong postcodes end the check (Brief A would allow a third) | not_verified / failed |
| IDS-04 | Thu 29 Oct 14:00 | one wrong answer per question is fine (2 in total), then a full refusal | no_promise / refused |
| IDS-05 | Fri 6 Nov 16:00 | two wrong birth years end the check though only 2 wrong in total | not_verified / failed |
| IDS-06 | Fri 6 Nov 09:30 | declines the postcode twice | not_verified / declined |
| IDS-07 | Fri 6 Nov 11:00 | year said in words; date after the deadline | promise 1340 on 2026-11-18 (deadline) |
| IDS-08 | Tue 10 Nov 16:30 | brother offers the answers; "tomorrow" is a holiday | callback 2026-11-12 11:00 |

## How the dates were computed

Business days Mon–Fri except 2026-10-26, 2026-11-11, 2026-11-26, 2026-12-25, 2027-01-01. Early band (1–7 days): 12th business day; late band (8–45): 7th business day.

- IDS-02: Tue 20 Oct, "this Friday" = 23 Oct. Late band deadline: 21, 22, 23, (26 holiday), 27, 28, 29, 30 → 30 Oct. 23 Oct ≤ deadline.
- IDS-04: Thu 29 Oct, late band. Attempt 1: last business day of this week = Fri 30 Oct. Attempt 2: floor 300 on the deadline: 30 Oct, 2, 3, 4, 5, 6, 9 Nov → 9 Nov.
- IDS-07: Fri 6 Nov, late band deadline: 9, 10, (11 holiday), 12, 13, 16, 17, 18 → 18 Nov. 30 Nov is after it, so the deadline is offered.
- IDS-08: Tue 10 Nov. The next business day is Thu 12 Nov (Wed 11 Nov is a holiday), so "tomorrow at eleven" is outside the window and Thursday 11:00 is booked.

`verify.py` parses the calendar, the band table, the ladder and the callback window from `brief.md` and re-derives all of these.

## Limits of our bank for this brief

- `bank/server.py` counts wrong answers globally (3 in total) and never returns `partial_postcode`. A half postcode sent alone is counted as a wrong answer (it does not fail the call by itself, but costs one). The per-question limit is checked only through the outcome: in IDS-03 and IDS-05 a third ask would get a third, unscripted answer and could change the outcome.
- `field_kinds` reads the openapi `answer` description: postcode compares as text (spaces and case ignored), birth_year as digits.
