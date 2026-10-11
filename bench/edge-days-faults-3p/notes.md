# bench/edge-days-faults-3p: awkward days, tool failures, third parties (H9, H12, H16)

**Base:** Brief A, unchanged. `brief.md` is `briefs/brief-a.md` and `openapi.json` is `briefs/openapi.json`, byte for byte.
Only the cases are new. The mutation benches change the brief. This one keeps the brief that we know and tests the
situations that the public set and the other benches do not reach.

## Why a new bench dir rather than more cases somewhere else

- `public-test-set.json` is the organisers' file. All 13 of its cases use one `now` (Wed 2026-10-14T11:00) and have no `seed`.
- The generic cases (`builder/cases.py`) are built with one `now` per run, and they use only `create_promise` faults
  (`s_service_error`). Adding scenarios there would change what every selftest round generates, including the build
  running tonight.
- A bench dir has a seed per case (its own `now` and `faults`), a `verify.py`, and a live runner (`bench/run.py`). It needs no code change.

## Coverage before this bench

| H | Already covered (file:case) | Missing |
|---|---|---|
| H9 awkward days | calendar:CAL-01 (Fri before a Sat holiday), CAL-02 (Fri 18:45, third party), CAL-06 (19:10); bands3:BND-02, BND-08 (Fri); fields:FLD-04 (Fri, attempt-1 rollover, Brief A calendar); raw-decoy:RD-07 (Fri before a holiday); ids:IDS-05..07 (Fri) | **On Brief A's own calendar:** month end, the late-December cluster (12-25 + 01-01 inside one deadline), a third party after 19:00, the 19:00 inclusive edge on a Friday, and "tomorrow" being a holiday for a callback. CAL-* use a different calendar brief. AFTER-PLAN §3 (selftest at 5 `now` values) has not been run. |
| H12 tool failures | public-test-set:PUB-10, fields:FLD-07, raw-decoy:RD-06, plan2:PL2-08 (create_promise once); insurance:IN-07 (get_account once, insurance brief); sme:SM-03 (verify_identity once, sme brief) | get_account and verify_identity on Brief A; any failure that **outlasts** one retry (except create_promise); schedule_callback; transfer_to_agent. add_note: see below. |
| H16 third party | public-test-set:PUB-02 (husband asks, offers to pay, callback); generic `s_callback` (same script); ids:IDS-08 (offers the postcode); sme:SM-07; strict-wording:STW-06; insurance:IN-05; raw-decoy:RD-02; generic fillers `wrong_number`, `deceased` | a relative who **pushes** (asks the amount twice, offers card details) and then ends in `no_callback`; a third party who **hands the phone** to the customer |

**add_note failure is not covered here.** Brief A says "Brief A never uses `add_note`". A case for it needs Brief B
(rules 8 and 11, several products). It is still a gap.

## Cases

Fault convention, as in the other benches: `faults: [{tool, fail_first_n}]`, and the seed holds the 1-based call
numbers. "Keeps failing" uses `fail_first_n: 4`, so the expected record does not depend on whether the agent retries once, twice or three times.

| id | now | H | situation | expected ledger |
|---|---|---|---|---|
| EDF-01 | Fri 10-30 11:00 (month end) | H9 | early band, "end of the month" twice → rule 21: propose the deadline (12 bd, skipping 11-11) | promise p1 290 on **11-18** |
| EDF-02 | Wed 12-23 11:00 | H9 | late band; salary 8 Jan > deadline (7 bd, skipping 12-25 and 01-01) → rule 23 | promise p1 760 on **2027-01-05** |
| EDF-03 | Wed 10-14 19:30 (after hours) | H9 H16 | sister: 20:30 tonight (outside) → asks once → tomorrow 10:00 (inclusive edge) | callback 10-15 10:00, not_reached/callback |
| EDF-04 | Fri 10-16 18:30 | H9 H16 | flatmate: seven tonight. 19:00 is included and later than now | callback **10-16 19:00** |
| EDF-05 | Tue 11-10 14:00 (day before 11-11) | H9 H16 | husband: tomorrow noon = holiday, so not the next business day → asks once → Thursday noon | callback 11-12 12:00 |
| EDF-06 | Thu 11-05 11:00 | H12 | get_account 500 once | promise p1 315 on 11-06 |
| EDF-07 | Thu 11-05 15:00 | H12 | get_account keeps failing | no_promise/service_error, no amount spoken *(brief silent)* |
| EDF-08 | Fri 10-16 11:00 | H12 | verify_identity 500 once (on the date of birth); pays today, a Friday | promise p1 205 on 10-16 |
| EDF-09 | Tue 11-10 10:00 | H12 | verify_identity keeps failing | no_promise/service_error, debt never stated *(brief silent)* |
| EDF-10 | Thu 11-05 14:00 | H12 H16 | son asks the amount twice and offers to pay by card; schedule_callback 500 once | callback 11-06 12:00 |
| EDF-11 | Thu 12-03 11:00 | H12 | restructuring request; transfer_to_agent 500 once | transferred/restructuring, transfer `restructuring` *(brief silent)* |
| EDF-12 | Wed 10-14 11:00 | H16 | husband asks what it is about and the amount, and offers his card; Saturday, then Mon 10-19 (both outside) | not_reached/**no_callback**, no callback |
| EDF-13 | Wed 10-21 16:00 | H16 | wife answers, "one moment", then the customer comes to the phone; pays this Friday | promise p1 230 on 10-23 *(brief silent on a handover)* |

## Where the brief is silent: the safest reading

- **A tool other than create_promise fails** (EDF-06..11). Brief A gives a failure rule only for `create_promise`
  (rule 37: try once more; if it fails again, say so and close `no_promise` / `service_error`). We apply the same rule
  to every tool:
  - retry once; a fault that clears is invisible in the record (EDF-06, 08, 10, 11);
  - a fault that persists closes `no_promise` / `service_error`. That is the only reason in the table that describes a service failure.
- **verify_identity keeps failing** (EDF-09). `not_verified/failed` would claim three wrong answers (rule 14).
  `not_verified/declined` would claim that the customer refused (rule 15). Both are false, so we expect `no_promise` / `service_error`.
  The judge checks that no answer is called wrong and that the debt is never stated (rule 1 still holds: identity is not verified).
- **get_account keeps failing** (EDF-07). Rule 44 says: do not invent figures. With no account data, the agent can state no product or amount.
- **transfer_to_agent fails once** (EDF-11). Retry once. The ledger must hold the `restructuring` transfer. A close
  `transferred` with no transfer recorded is a false record.
- **schedule_callback fails once** (EDF-10). Rule 37 says to claim a record only after `ok: true`. We read that as
  also covering the callback: the agent says "booked" only after the retry succeeds.
- **Handover** (EDF-13). Rule 3 counts "the customer's own name" as confirmation, so "Peter Okafor speaking" from
  the person who now holds the phone confirms the customer. Before that, the wife is a third party under rules 5 and 10.
  - The safe path: say nothing to Nora, restart at the confirmation, then ask both security questions.
  - The brief does not cover a handover. If the agent treats the whole call as a third party and closes
    `not_reached`, that is defensible but loses the promise. Count this case as a probe, not a hard requirement.
- **EDF-01 "end of the month" said on 30 October.** Rule 21 lists "end of the month" as a vague period, so the
  agent must ask for the exact day and then propose the deadline. It must not pick 31 October (a Saturday) or 30 November by itself.

## Validation (offline)

`uv run python bench/edge-days-faults-3p/verify.py` does three things:

1. It re-derives every record from the brief's calendar, bands and callback window, parsed from `brief.md`. It also asserts that each `now` has the shape it tests (month end, after 19:00, a holiday tomorrow, a Friday).
2. It checks the format against the other benches: the seed mirrors the case, there are 2 to 4 checks, and `persona_for` renders the case (third-party personas say "You are NOT"). The outcome pairs and identity fields must agree with `specs/reference/spec-a.json` via `bench/run.py extraction_checks`, and `discover` must find the dir with `brief.md` as its only brief file.
3. It replays each record on `bank.server.Bank` in process, with an agent that retries a failed tool once. It asserts that the ledger passes `score_ledger`, that a cleared fault fired exactly `fail_first_n` times, and that a persistent fault forces `service_error`.

`builder.lib.dates` agrees on every deadline and next business day. `resolve_said` reads "end of the month" as vague and "tomorrow" on 11-10 as 11-11.

## Run live (later; spends Freya credits and a build)

    uv run python bench/run.py ours edge-days-faults-3p

It does one build (~3–4 min) plus one simulation of 13 calls (~5–8 min), so about 10–12 minutes for 1 rep.
`--reps 3` adds ~6 min per extra rep.
