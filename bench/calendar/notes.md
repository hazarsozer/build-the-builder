# bench/calendar: notes

Test input only. Never show this brief, its cases or these notes to the builder as examples.

## Hypothesis

H5 (different calendar: other business weekdays, many holidays, deadline in calendar days snapped back), and part of H9 (`now` on a Saturday, a Friday before a holiday, after hours). Bank: Kestrel Savings Bank (fictional).

## What was mutated (against Brief A)

| Where | Brief A | calendar |
|---|---|---|
| Business days | Mon–Fri | **Mon–Sat**; Sunday never |
| Holidays | 5 | 8: 2026-10-17 (Sat), 10-26, 10-30, 11-02, 11-11, 11-21 (Sat), 12-08, 12-25 |
| Deadline (rule 19) | 12th / 7th business day by band | 10 calendar days after today; if not a business day, the business day before. Same for both bands |
| Band table | sets deadline and attempts | sets attempts only (early 1, late 2) |
| Callbacks (rule 6) | 10:00–19:00, Mon–Fri | 09:00–18:00, Mon–Sat; after 18:00 the earliest is the next business day |
| Rule 21 | "weekend day or holiday" | "Sunday or holiday" |
| Rule 23 | "next <weekday>" | same meaning; adds the Saturday example |
| Ladder attempt 1 | last business day of this week | same, usually Saturday; snaps back over a Saturday holiday |
| openapi | | only the title and the `schedule_callback.time` description ("HH:MM, 24-hour") |

Identity, amounts, ladder, outcome table: Brief A's (rules renumbered as in bench/ids, identity split into rules 11–16).

## Cases

| Case | `now` | What it tests | Expected |
|---|---|---|---|
| CAL-01 | **Fri** 16 Oct 11:00 (Sat 17 is a holiday) | today is the last business day of the week → attempt 1 is next week's Saturday | promise 620 on 2026-10-24 |
| CAL-02 | **Fri** 16 Oct **18:45** | after hours, Saturday holiday refused, Monday booked | callback 2026-10-19 10:00 |
| CAL-03 | **Sat** 24 Oct 10:00 | "next Saturday" on a Saturday; Saturday is a business day | promise 340 on 2026-10-31 |
| CAL-04 | **Sat** 24 Oct 12:30 | names a Monday holiday → next business day, which is the deadline | promise 1100 on 2026-11-03 |
| CAL-05 | Tue 20 Oct 14:00 | +10 lands on a holiday → deadline snaps back | promise 495 on 2026-10-29 |
| CAL-06 | Wed 21 Oct **19:10** | late band, accepts attempt 2 (floor on a Saturday deadline) | promise 294 on 2026-10-31 |
| CAL-07 | Thu 19 Nov 11:00 | "this Saturday" is a holiday → Monday, not Sunday | promise 150 on 2026-11-23 |
| CAL-08 | Thu 17 Dec 16:00 | +10 lands on a Sunday → deadline Saturday 26 Dec | promise 720 on 2026-12-26 |

## How the dates were computed

Deadline = today + 10 days, then back one day at a time until Mon–Sat and not a holiday.

- CAL-01: 16 Oct + 10 = Mon 26 Oct (holiday) → Sun 25 → **Sat 24 Oct**. Attempt 1: this week's Saturday 17 Oct is a holiday, so the last business day of this week is Fri 16 Oct = today → last business day of next week = Sat 24 Oct (≤ deadline).
- CAL-02: after 18:00, so not today. Sat 17 Oct is a holiday, Sun 18 is not a business day → next business day Mon 19 Oct; 10:00 is inside 09:00–18:00.
- CAL-03 / CAL-04: 24 Oct + 10 = **Tue 3 Nov** (business day). "Next Saturday" = 31 Oct. 2 Nov is a holiday → next business day 3 Nov.
- CAL-05: 20 Oct + 10 = Fri 30 Oct (holiday) → **Thu 29 Oct**. The named 30 Oct is after the deadline → offer 29 Oct.
- CAL-06: 21 Oct + 10 = **Sat 31 Oct**. Attempt 1: full on Sat 24 Oct; attempt 2: floor 294 on 31 Oct.
- CAL-07: 19 Nov + 10 = Sun 29 Nov → **Sat 28 Nov**. "This Saturday" = 21 Nov (holiday) → Sun 22 skipped → Mon 23 Nov.
- CAL-08: 17 Dec + 10 = Sun 27 Dec → **Sat 26 Dec** (not a holiday here). The 28th is after it → offer 26 Dec.

`verify.py` parses all of this from `brief.md` (weekday range from "Monday to Saturday", holidays from the same line, "10 calendar days after today, snapped back" from the band table) and re-derives it.

## Concerns

- A Saturday that is a business day is unusual; a builder with Mon–Fri hard-coded anywhere (date helpers, "weekend" wording) fails CAL-01, 03, 06 and 08.
- No `now` falls on a Sunday or a holiday; the brief does not say what happens then.
