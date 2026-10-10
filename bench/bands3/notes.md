# bench/bands3: notes

Test input only. Never show this brief, its cases or these notes to the builder as examples.

## Hypothesis

H6 (more bands, each with its own deadline and number of attempts, a three-rung ladder). Bank: Northmere Bank (fictional).

## What was mutated (against Brief A)

| Where | Brief A | bands3 |
|---|---|---|
| Job (rule 1 section) | 1–45 days late | 1–60 days late |
| Band table (rule 19) | early 1–7 (12th bd, 1 attempt), late 8–45 (7th bd, 2) | band 1: 1–10, 5th business day, 1 attempt; band 2: 11–30, 8th, 2; band 3: 31–60, 12th, 3 |
| Ladder (rule 31) | 1: full on last bd of week; 2 (late only): floor on deadline | 1 (every band): full on last bd of week; 2 (bands 2, 3): floor on deadline; 3 (band 3): floor on the next business day after today |
| Rule 27 | "Attempt 2 may name the floor again" | "Attempts 2 and 3 may name the floor again" |
| openapi | | title only |

Calendar (Mon–Fri, Brief A's five holidays), identity, amounts and outcome table are Brief A's.

## Cases

| Case | `now` | Band (days) | Customer | Expected |
|---|---|---|---|---|
| BND-01 | Wed 14 Oct 11:00 | 1 (7) | accepts attempt 1 | promise 300 on 2026-10-16 |
| BND-02 | Fri 16 Oct 10:00 | 1 (3) | accepts attempt 1 on the last bd of the week → next week | promise 410 on 2026-10-23 |
| BND-03 | Mon 9 Nov 11:00 | 1 (10, boundary) | refuses the only attempt | no_promise / refused |
| BND-04 | Thu 22 Oct 15:00 | 2 (18) | accepts attempt 2 | promise 210 on 2026-11-04 |
| BND-05 | Mon 9 Nov 14:00 | 2 (11, boundary) | refuses both attempts | no_promise / refused |
| BND-06 | Tue 10 Nov 11:00 | 3 (45) | accepts attempt 3 | promise 500 on 2026-11-12 |
| BND-07 | Mon 21 Dec 10:00 | 3 (52) | **refuses all 3 attempts** | no_promise / refused |
| BND-08 | Fri 27 Nov 09:30 | 3 (31, boundary) | accepts attempt 2 | promise 240 on 2026-12-15 |

## How the dates were computed

Business days Mon–Fri except 2026-10-26, 2026-11-11, 2026-11-26, 2026-12-25, 2027-01-01.

- BND-01: attempt 1 = Fri 16 Oct. (Deadline 5th bd: 15, 16, 19, 20, 21 → 21 Oct.)
- BND-02: today Fri 16 Oct is the last bd of the week → Fri 23 Oct. Deadline 5th bd: 19–23 → 23 Oct, so the promise is on the deadline.
- BND-03: attempt 1 = Fri 13 Nov; band 1 has no attempt 2.
- BND-04: attempt 1 = Fri 23 Oct. Deadline 8th bd after Thu 22 Oct: 23, (26 hol), 27, 28, 29, 30, 2 Nov, 3, 4 → **4 Nov**; floor 210.
- BND-05: attempt 1 = Fri 13 Nov. Deadline 8th bd after Mon 9 Nov: 10, (11 hol), 12, 13, 16, 17, 18, 19, 20 → 20 Nov.
- BND-06: attempt 1 = Fri 13 Nov. Deadline 12th bd after Tue 10 Nov: 12, 13, 16, 17, 18, 19, 20, 23, 24, 25, (26 hol), 27, 30 → 30 Nov. Attempt 3: next bd after today = Thu 12 Nov (11 Nov holiday); floor 500.
- BND-07: attempt 1: Fri 25 Dec is a holiday → Thu 24 Dec. Deadline 12th bd after Mon 21 Dec: 22, 23, 24, (25 hol), 28, 29, 30, 31, (1 Jan hol), 4, 5, 6, 7, 8 → **8 Jan 2027**. Attempt 3: Tue 22 Dec.
- BND-08: today Fri 27 Nov is the last bd of the week → attempt 1 = Fri 4 Dec. Deadline 12th bd: 30 Nov, 1–4 Dec, 7–11, 14, 15 → **15 Dec** (band 2 would give 9 Dec).

`verify.py` parses the band table and the ladder text from `brief.md` and re-derives all of these; for refusals it checks that each attempt's date appears in the case's `checks`.

## Concerns

- Ledger records do not show refused offers; BND-03, 05, 07 rely on the judge reading the attempt dates from the transcript (named in `checks`).
- Rule 27 (floor offered after "full is too much") interacts with the ladder; no case exercises it, on purpose, to keep each case to one question.
