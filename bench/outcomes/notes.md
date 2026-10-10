# bench/outcomes: notes

Test input only. Never show this brief, its cases or these notes to the builder as examples.

## Hypothesis

H4 (renamed reasons, new transfer queue, enums in the openapi) and H7 (new situations with their own outcome). Bank: Bramblewood Bank (fictional).

## What was mutated (against Brief A)

| Where | Brief A | outcomes |
|---|---|---|
| Reason names | `refused`, `dispute`, `hardship` | `declined_to_pay`, `disputed`, `vulnerable` (rules 32, 46, 41, 33) |
| Rule 47 (new) | none | bankruptcy / insolvency → fixed sentence, `transfer_to_agent` queue `legal`, close `transferred` / `legal`; before rules 31, 33 and 42; only after identity is verified |
| Rule 48 (new) | none | wants it in writing → "We will send you a letter with the details.", no promise, close `no_promise` / `letter_requested` |
| Outcome table (rule 49) | | `legal` under transferred; `letter_requested` under no_promise; renamed reasons; a line that the old names do not exist |
| openapi `transfer_to_agent.queue` | restructuring, general, support | + `legal` |
| openapi `add_note.reason` | refused, hardship, dispute, paid, service_error | declined_to_pay, vulnerable, disputed, paid, service_error, letter_requested (unused: this brief has no add_note) |
| openapi `close_call.reason` | free string | **new enum** of every reason in the table |

Calendar, bands, identity, ladder: Brief A's. The seed `reasons` table is the brief's table, so our bank rejects `refused`, `dispute`, `hardship` at close_call.

## Cases

| Case | `now` | What it tests | Expected |
|---|---|---|---|
| OUT-01 | Wed 14 Oct 11:00 | flat refusal through both attempts | no_promise / declined_to_pay |
| OUT-02 | Tue 20 Oct 10:30 | never had the product | no_promise / disputed |
| OUT-03 | Mon 2 Nov 15:00 | illness, declines support | no_promise / vulnerable |
| OUT-04 | Mon 2 Nov 11:00 | death in the family, accepts support | transferred / support, transfer `support` |
| OUT-05 | Tue 17 Nov 11:00 | filed for bankruptcy | transferred / legal, transfer `legal` |
| OUT-06 | Tue 17 Nov 14:30 | wants it in writing | no_promise / letter_requested |
| OUT-07 | Thu 3 Dec 10:00 | job loss and bankruptcy together: rule 47 before rule 33 | transferred / legal, transfer `legal` only |
| OUT-08 | Thu 3 Dec 16:00 | "do not want to talk" twice after verification (rule 46) | no_promise / declined_to_pay |

## How the dates were computed

Only OUT-01 involves dates (in `checks`, not in the record): Wed 14 Oct, late band (16 days). Attempt 1: full 510 on Fri 16 Oct. Attempt 2: floor 153 on the 7th business day: 15, 16, 19, 20, 21, 22, 23 → 23 Oct. `verify.py` re-derives both from `brief.md`.

## Concerns

- OUT-07's persona may decline the support offer before mentioning bankruptcy if the agent offers support first; expected stays `transferred` / `legal` because rule 47 comes first, but a builder that applies rule 33 first could close `no_promise` / `vulnerable`. That is the intended failure.
- The close_call `reason` enum mixes reasons of every outcome; the pairing still comes only from the brief's table.
