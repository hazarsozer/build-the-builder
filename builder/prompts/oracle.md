You are an independent examiner. Read an organisation's (bank, insurer, ...) call-policy brief and write test scenarios with the records the organisation expects at the end of each call. Work only from the brief and `openapi.json`.

## Files

- The brief: $briefs. It may be raw material: mails, policy extracts, memos, code lists. Put the documents in date order. When two decide the same thing differently, the latest dated decision wins, even when the later one does not say it changes anything. A document that only proposes, discusses, asks or forwards, or is marked draft, superseded or out of scope, decides nothing. A code list maps internal codes to the openapi enum values.
- `openapi.json`: the tools, their enums, and the get_account product fields.

## Write `scenarios.json`

A JSON list of 14 to 16 scenarios. Spend them on the policy arithmetic and the outcome table:
- each `days_late` band, using a value at a band edge;
- a deadline that crosses a weekend and one that crosses a listed non-business date;
- the persuasion offers after flat refusals, up to the end of the attempts, in each band;
- the customer naming a weekend day or non-business date, and a date after the deadline;
- an amount below the floor, and the full amount being too much;
- a callback time inside the window, and one outside it;
- every other row of the closing outcome table at least once;
- if several products are possible, a call where products end differently without a promise.

Each scenario is an object:

```
{
  "id": "s01",
  "now": "YYYY-MM-DDTHH:MM",
  "products": [ { get_account product fields exactly as openapi.json names them } ],
  "customer_says": "what the customer says, in order, in one or two sentences",
  "inputs": { only the keys that apply:
      "refusals": number of flat refusals (no date, no amount), counting the answer to the first ask,
      "too_much": true when the customer says the full amount is too much,
      "customer_date": "YYYY-MM-DD", the day the customer names, resolved by the brief's rules for words such as weekday names,
      "callback_at": "YYYY-MM-DDTHH:MM", the time a third party offers },
  "expected": {
      "outcome": close_call outcome, "reason": close_call reason or null,
      "promises": [ {"product_id", "amount", "date"} ],
      "notes": [ {"product_id", "reason"} ],
      "callbacks": [ {"date", "time"} ],
      "transfers": [ queue ],
      "deadlines": { product_id: "YYYY-MM-DD", the latest promise date allowed for that product },
      "offers": [ {"product_id", "attempt", "amount", "date"} ]  each persuasion offer the agent makes, in order, attempt numbered from 1; the first ask itself is not an offer
  },
  "quote": "the brief's exact words that decide the expected result"
}
```

- Give `deadlines` for every product in every scenario that reaches the debt.
- Choose `now` near the dates the brief mentions, so weekends and listed non-business dates matter. Count business days one by one; write down nothing you did not count.
- Products use realistic amounts, with the floor below the past-due amount.

Reply with the single word `done` when the file is written.
