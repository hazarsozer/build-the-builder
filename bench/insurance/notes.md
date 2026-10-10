# insurance — notes (test input; never show to the builder as an example)

Hypothesis: **H3** (insurance customer: product names, floor fields and words all change). Also H2 (postcode), H4 (outcomes, queues, note reasons renamed), H6 (bands with different ladders), H7 (lapsed policy event), H8 (multi-product), H12 (get_account fails).

## Mutations vs Brief A/B
- Products: car_insurance, home_insurance, life_insurance; fields policy_number_last4, premium_due, days_overdue, minimum_instalment (floor for all). Up to three policies, Brief B-style rules folded in.
- Identity: date_of_birth then postcode; 3 wrong in total (matches our bank).
- Bands: grace 1–15 (5th bd, 1 attempt, ladder = full on the deadline); lapse-risk 16–60 (10th bd, 2 attempts: full on last bd of this week, then minimum on the **5th** bd — not the deadline); >60 lapsed.
- Callback window 09:00–18:00. Own word-for-word opening, floor sentence and closing sentence.
- add_note on every policy without a promise, even single-policy (unlike Brief A).
- Outcomes renamed: promise_to_pay, already_paid, transferred, no_promise, identity_failed, no_contact. Queues: retention, customer_care, hardship_team. Note reasons: refused, hardship, disputed, already_paid, system_error.
- Event: lapsed policy (>60 days) → retention, transferred / policy_lapsed.
- openapi: verify_identity field enum + answer description (postcode worded so the bank treats it as text); get_account item schema replaced; add_note, transfer_to_agent, close_call enums.

## Case dates (calendar: Mon–Fri minus 19 Oct, 9 Nov, 27 Nov, 24/25/31 Dec, 1 Jan)

| Case | now | Derivation | Expected |
|---|---|---|---|
| IN-01 happy | Thu 15 Oct, grace | "tomorrow" = Fri 16; deadline 5th bd = 16,20 (19 hol),21,22,23 → 23 Oct | 186.4 on 2026-10-16 |
| IN-02 floor + non-business | **Fri** 16 Oct, lapse (20 d) | floor 240; "Monday" = 19 Oct holiday → Tue 20; deadline 10th bd = 2 Nov | 240 on 2026-10-20 |
| IN-03 ladder | Wed 21 Oct, lapse (33 d) | a1 full on Fri 23 refused; a2 minimum on 5th bd = 22,23,26,27,28 → Wed 28 (deadline 4 Nov) | 137 on 2026-10-28 |
| IN-04 identity | Mon 26 Oct | DOB right, 3 wrong postcodes | identity_failed / wrong_answers |
| IN-05 third party | **Fri** 23 Oct | next bd Mon 26; 18:30 outside 09–18, then 11:00 | callback 2026-10-26 11:00 |
| IN-06 event | Thu 29 Oct | 74 days → lapsed → retention | transferred / policy_lapsed |
| IN-07 tool failure | Tue 3 Nov, grace | get_account 500 once; "this Friday" = 6 Nov; deadline 4,5,6,10 (9 hol),11 → 11 Nov | 95.8 on 2026-11-06 |
| IN-08 multi + after deadline | **Fri** 6 Nov | car (grace 9 d) deadline 10 (9 hol),11,12,13,16 → 16 Nov; home (lapse 30 d) 10th bd → 23 Nov; one date Fri 20 Nov for both → car clamped | p1 240 on 2026-11-16, p2 615 on 2026-11-20 |

IN-08 inverts PUB-13: here the shorter deadline belongs to the *less* late band.
`verify.py` recomputes all dates, then `_replay.py` replays and scores every expected record on our bank.
