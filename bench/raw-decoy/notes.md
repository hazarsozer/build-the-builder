# raw-decoy — notes (test input; never show to the builder as an example)

Hypothesis: **H1** (one hidden brief is raw material with an unlabelled amendment and decoys). Also touches H4 (renamed close reasons).

## Material (5 files, read in date order, not file order)

| File | Date | Role |
|---|---|---|
| brief-mail-campaign.md | 28 Sep | intro mail: variables, products, tools, "add_note not used" |
| brief-policy-extract.md | 15 Sep (v4.2) | Brief A rules 1–47, rebranded Northwind Mutual; closes say "code X", not outcome/reason |
| brief-mail-training.md | 7 Oct | **decoy**: "deadline", "minimum", "floor", "third attempt", a 24 Dec holiday idea — all explicitly undecided |
| brief-mail-risk.md | 8 Oct | **unlabelled amendment** ("Re:" reply): early deadline 12 → 10 business days; 2026-10-30 (Fri) becomes a holiday |
| brief-outcome-codes.md | 21 Sep | code → outcome + reason; one retired code N09 |

Alphabetical file order puts the amendment before the extract, so a builder that applies files in order and lets later text win gets it wrong.

## Mutations vs Brief A
- Bank name Northwind Mutual; same products, floors, bands (except early deadline), ladder, sentences.
- Early band deadline: 10th business day (was 12). Holidays: + 2026-10-30.
- Reasons renamed (outcomes unchanged): restructure, human_request, support_team, declined_to_pay, financial_hardship, disputed, system_error, id_failed, id_declined, callback_booked, no_callback_time, wrong_number, deceased, unclear_contact. Queues unchanged (so reason ≠ queue name).
- openapi: title, `close_call.reason` gets an enum of the 14 reasons and a description pointing to the code list.

## Case dates (calendar: Mon–Fri minus 26 Oct, **30 Oct**, 11 Nov, 26 Nov, 25 Dec, 1 Jan)

| Case | now | Derivation | Expected |
|---|---|---|---|
| RD-01 happy | Wed 14 Oct | full today | 2026-10-14 |
| RD-02 third party | Thu 29 Oct 16:00 | next business day skips Fri 30 (new holiday) and the weekend → Mon 2 Nov; "Friday 15:00" rejected, "Monday 15:00" in 10–19 | callback 2026-11-02 15:00 |
| RD-03 identity | Wed 14 Oct | 3 wrong DOBs (day 19 cannot trigger the swap hint) | not_verified / id_failed |
| RD-04 after deadline | **Fri** 16 Oct, early (6 d) | 10th bd: 19,20,21,22,23,27 (26 hol),28,29,2 Nov (30 hol),3 Nov → **3 Nov**. Extract alone gives 4 Nov; only one change gives 2 Nov or 5 Nov. Asked 5 Nov > deadline | 690 on 2026-11-03 |
| RD-05 non-business | Tue 27 Oct, early (3 d) | names Fri 30 (new holiday) → next bd Mon 2 Nov; deadline 12 Nov | 145 on 2026-11-02 |
| RD-06 floor + fault | Thu 5 Nov, early (2 d) | too much → floor 294; "this Friday" = 6 Nov; deadline 20 Nov (11 Nov hol); create_promise 500 once | 294 on 2026-11-06 |
| RD-07 ladder | **Fri** 23 Oct, late (19 d) | attempt 1: today is the week's last bd → last bd of next week = Thu 29 Oct (30 hol), refused; attempt 2: floor on 7th bd = 27,28,29,2,3,4,5 → Thu 5 Nov, accepted | 500 on 2026-11-05 |
| RD-08 event | Mon 2 Nov | dispute | no_promise / disputed |

No multi-product case: Brief A's policy is single-product. The "event" slot is the dispute under its renamed reason (the brief's two changes do not add an event).

`verify.py` recomputes these with its own business-day code and checks the amendment actually moves RD-04; then `_replay.py` replays every expected record on our bank and scores it with `tools/score.py`.
