# sme — notes (test input; never show to the builder as an example)

Hypotheses: **H2** (company_number + founded_year, per-question limit of 2) and **H3** (business customer, product and floor field change). Also H5 (Mon–Sat, calendar-day deadline snapped back), H7 (company in administration), H12 (verify_identity fails).

## Mutations vs Brief A
- Customer is a company; call is for the named director. New call variable `company_name`, used in the confirmation question.
- Identity: company_number (8 digits, leading zero) then founded_year; **2 wrong answers on the same question** end the check; wrong answers on different questions do not add up.
- One product `business_overdraft`: account_last4, past_due, days_late, minimum_due (floor).
- Business days Mon–Sat; holidays 24 Oct (Sat), 31 Oct (Sat), 11 Nov, 25/26 Dec, 1 Jan.
- Deadline = today + 10 calendar days, snapped back to a business day. Bands only change attempts (1–20: 1, 21–60: 2). Attempt 1 = last bd of the Mon–Sat week (Saturday unless a holiday).
- Third party "director not available": colleague who handles banking is still a third party; callback window Mon–Fri 09–18, Sat 09–13; new not_reached reason `director_left`.
- Event: company in administration → add_note(reason `administration`) → no_promise / in_administration.
- Queues: restructuring, relationship_manager, business_support. Time-dependent greeting (Good morning / Good afternoon from 12:00).
- openapi: verify_identity field enum, answer description and summary (no fixed 3); get_account item schema; add_note enum `[administration]`; queue enum.

## Case dates

| Case | now | Derivation | Expected |
|---|---|---|---|
| SM-01 happy | **Sat** 17 Oct 10:00 | +10 = Tue 27 (bd); pays Tue 20 | 3450 on 2026-10-20 |
| SM-02 floor | Wed 14 Oct | +10 = Sat 24 holiday → Fri 23; floor 1040 "this Saturday" = 17 Oct, a business day | 1040 on 2026-10-17 |
| SM-03 non-business + fault | **Fri** 16 Oct | +10 = Mon 26; names Sun 25 → next bd Mon 26 (= deadline); verify_identity 500 once | 2280 on 2026-10-26 |
| SM-04 after deadline | Wed 21 Oct | +10 = Sat 31 holiday → Fri 30; asks 2 Nov | 1875 on 2026-10-30 |
| SM-05 ladder | Thu 22 Oct, escalated (35 d) | a1: Sat 24 holiday → Fri 23 (full 8900); a2: +10 = Sun 1 Nov → Sat 31 hol → Fri 30 (minimum 1780); both refused | no_promise / refused |
| SM-06 identity | Mon 19 Oct | company number right, founded year wrong twice | not_verified / failed |
| SM-07 third party | **Fri** 23 Oct 15:00 | next bd: Sat 24 holiday, Sun → Mon 26; "Saturday 10:00" rejected, "Monday 09:30" inside 09–18 | callback 2026-10-26 09:30 |
| SM-08 event | Tue 10 Nov | administration | note p1/administration; no_promise / in_administration |

No multi-product case: the brief has one product.

## Known bank mismatch
Our bank hard-codes 3 wrong answers in total. In SM-06 it still reports `wrong_answers_left: 1` after the second wrong year; the brief (rule 12) tells the agent to end the check anyway. The ledger score only checks the close, so the case is fair, but an agent that trusts `wrong_answers_left` over the brief fails — which is the point.

`verify.py` recomputes all dates, then `_replay.py` replays and scores every expected record on our bank.
