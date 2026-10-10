# bench/strict-wording — fixed sentences, banned words, abusive customers (H14, H17, H11)

**Base:** Brief A, as "Halvard Mutual Bank". The calendar, bands, ladder and amounts are unchanged.

## Mutation

The brief changes these rules:

- **Rule 1.** A second opening sentence is required, word for word: "Your personal data is processed under our privacy notice, which you can read on our website or at any branch." It is said to everyone, relatives included. Rule 5 now allows the two opening sentences.
- **Rule 16.** Right after both answers pass, and before anything about the account, the agent says word for word: "This is an attempt to collect a debt and any information obtained will be used for that purpose."
- **Rule 48.** These words are banned, in any form and even when the customer used them first: "guarantee", "lawsuit", "credit score", "final notice".
- **Rule 49.** The agent never states consequences of non-payment. Asked about them, it says word for word: "I am not able to discuss that, but I can help you arrange a payment." The question is not a refusal and uses no persuasion attempt.
- **Rule 50.** Abuse (insults, swearing, threats) gets one warning, word for word: "I want to help you, but I will end this call if the abusive language continues." The second time, at any stage, the call closes as `no_promise` / `abusive`. This is a **new reason**, added to the outcome table. If a promise was already recorded, it closes as `promise`. Anger without insults is not abuse.

Ruling for STW-07: rule 50 applies "at any stage of the call", so a second abuse during the security questions closes as `no_promise` / `abusive`. It wins over `not_verified` / `declined` (rule 15), because abuse is not a refusal to answer.

## Hypotheses

- **H14**: required fixed sentences in the opening and after verification.
- **H17**: judge literalism about banned words and consequences.
- **H11**: the angry or abusive persona.
- **H7**: a new outcome reason.

## Cases

| id | now | covers | expected |
|---|---|---|---|
| STW-01 | Thu 2026-10-22 | both fixed sentences, happy path | promise p1 210 on 10-22 |
| STW-02 | Mon 2026-11-09 | **"what happens if I don't pay?"** and "credit score?"; then 2 refusals → attempt 1 is 960 on Fri 11-13, attempt 2 is the floor on the deadline | promise p1 320 on 11-19 |
| STW-03 | Thu 2026-11-19 | **abusive** after the debt: warned, abuses again | no_promise / abusive |
| STW-04 | Mon 2026-12-21 | angry but **not** abusive, "this Thursday" | promise p1 515 on 12-24 |
| STW-05 | Thu 2026-10-22 | the customer says "guarantee"; "next Wednesday" | promise p1 375 on 10-28 |
| STW-06 | Mon 2026-11-09 15:00 | relative: data-protection sentence only; 09:00 refused, 18:30 next business day | not_reached / callback 11-10 18:30 |
| STW-07 | Mon 2026-12-21 | abusive during identity: no disclosure, no get_account | no_promise / abusive |
| STW-08 | Thu 2026-11-19 | "final notice? going to sue me?", then already paid | paid |

## How the expected values were computed

`verify.py` uses Brief A's calendar rules, the same as `bench/fields`.

For STW-02, it asserts that attempt 1 is 11-13 and the deadline is 11-19. The script's two refusals land on attempt 2, which is the floor of 320 on the deadline.

For STW-06, the callback is the next business day after today, and 18:30 falls inside the 10:00–19:00 window.

It also asserts that every word-for-word sentence quoted in a `checks` line appears verbatim in `brief.md`. The seed's reasons table includes `abusive`.

## Findings about the spec schema (partly expressible, with workarounds)

- **Data-protection sentence.** It fits in `opening.first_sentence`, which may hold two sentences.
- **Disclosure after verification.** No dedicated field.
  - The negotiate node's `on_enter` is fixed TTS: "Thank you, you are verified. {{debt_statement_text}} …".
  - The disclosure only lands before the debt if the spec author prefixes it to `negotiation.debt_statement`.
  - If it goes into `extra_rules` or `say_rules` instead, it is spoken after the debt and fails the "before the debt" check.
- **Banned words for the whole call.** `opening.banned_before_verify` covers only the time before verification, so this goes to `extra_rules` (stage `any`).
- **Abusive.** This is an event with `times: 2` and a `say_rule` for the warning. The warning-then-close sequence is not a first-class shape, so it is a judgment call for the extractor.
