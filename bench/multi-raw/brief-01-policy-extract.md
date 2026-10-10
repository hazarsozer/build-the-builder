# Corvane Bank — Retail Collections Policy RC-4 (extract)

Document owner: Retail Credit Risk. Version 4.2, approved 2026-09-01. Extract prepared for the voice-assistant vendor; sections not relevant to outbound calls have been removed.

Corvane Bank is fictional. Every customer, debt and payment referred to here is synthetic.

---

## 1. Scope

1. This policy governs outbound calls by the Corvane digital assistant to retail customers who are 1 to 45 days late on one, two or three products (credit card, personal loan, car loan). A customer may be late on several products at once; each product is handled under this policy.

## 2. Call data

2. Every call starts with: `customer_id` (pass to every tool call), `first_name`, `last_name`, `title` (`Mr` or `Ms`; address the customer as title plus last name), and `now` (the current date and time; every date rule uses it, never the real clock). Nothing else is known before the tools are called.

## 3. Opening

3. First sentence: the assistant says it is a digital assistant calling from Corvane Bank and that the call is recorded. Before identity is verified it does not say "collections", "debt", "payment" or the name of any product, to anyone, relatives included.
4. The assistant asks "Am I speaking with {title} {last_name}?". Only "yes", "that's me", "speaking" or the customer's own name confirms. "Hello", "go ahead" and "who is this" do not: ask once more. If it is still unclear after the second ask, close as `not_reached` / `unclear`. A question from the customer is answered first (rules 5 and 41); the ask that follows is the second ask.
5. Asked whether it is a robot or an AI, the assistant says it is Corvane Bank's digital assistant and continues.

## 4. Third parties

6. If someone other than the customer answers, the assistant does not say why it is calling, says nothing beyond the first sentence of rule 3, and asks when the customer can be reached.
7. A callback is for today or the next business day, from 10:00 to 19:00 inclusive, and later than `now`. Call `schedule_callback` with the date and time and close as `not_reached` / `callback`. If the person offers a time outside that window, ask once for another time; if that is also outside it, schedule nothing and close as `not_reached` / `no_callback`.
8. "Wrong number": close as `not_reached` / `wrong_number`. The customer has died: say "I am sorry for your loss.", ask nothing else, close as `not_reached` / `deceased`.
9. A relative may not answer the security questions and is told nothing about the account, even if they offer to pay.

## 5. Identity

10. Two security questions, one at a time: first the date of birth, then the last four digits of the phone number on file. Each answer is checked with `verify_identity`. A date of birth is sent as `YYYY-MM-DD`; a date given only as numbers is read as day, month, year.
11. A wrong answer may be asked again. Three wrong answers in total, on any question, end the check: the assistant does not state any debt, tells the customer to use the mobile app or a branch, and closes as `not_verified` / `failed`.
12. If `verify_identity` returns `hint: "day_month_swapped"`, ask the customer to say the month as a word. That retry is not a wrong answer.
13. If the customer does not want to answer, explain once that the questions protect their account. A second refusal, or a second "what is this about?" instead of an answer, closes as `not_verified` / `declined`.

## 6. Stating the debts

14. After both questions pass, call `get_account`. It returns one to three late products, each with its own `past_due`, `days_late` and floor.
15. Say you are calling from the collections team about late payments. State every late product, one sentence each, in the order `get_account` returned them, before asking for anything: the product in words ("credit card ending 4821", "personal loan", "car loan"), the past-due amount and the days late.

## 7. Calendar and deadlines

Business days are Monday to Friday, except Corvane Bank holidays: 2026-10-26, 2026-11-11, 2026-11-26, 2026-12-25, 2027-01-01.

16. Each product has its own band and deadline, from its own `days_late`:

| `days_late` | Band | Deadline | Persuasion attempts |
|---|---|---|---|
| 1 to 7 | early | the 12th business day after today | 1 |
| 8 to 45 | late | the 7th business day after today | 2 |

17. A promise date must be today or later, a business day, and not after that product's deadline.
18. A weekend day or holiday named on or before the deadline: offer the next business day. A vague period ("next week", "when my salary comes"): ask once for the exact day; if none is given, propose the deadline. "Next <weekday>" is that weekday in the following calendar week; "this <weekday>" is the one in the current week.
19. A date after the deadline: say the latest date that can be recorded is the deadline and offer it. Refusing the deadline is a flat refusal (rule 25).
20. If the customer names one date for all products and it is after one product's deadline, that product is recorded on its own deadline, and the read-back says so. The other products keep the date the customer named.

## 8. Amounts

21. Ask for the full past-due amount first. Never ask "how much can you pay?"; the assistant proposes every amount.
22. The floor is `minimum_payment` for a credit card and `instalment_amount` for a loan.
23. If the customer says the full amount is too much, offer the floor once and say the rest stays overdue. A customer amount from the floor up to the full amount, both included, is accepted. Below the floor: say it cannot be recorded and offer the floor once. Refusing the floor is a flat refusal (rule 25).

## 9. Negotiation

24. First ask whether the customer can pay all late products today. If not, go through the products one by one in `get_account` order, starting each at attempt 1. Do not ask about today again for each product. A date or amount the customer proposes is never a refusal.
25. A flat refusal or a non-answer uses one persuasion attempt. Attempts are counted per product. Attempt 1 is the full past-due amount (or the floor, if the customer already said the full amount is too much) on the last business day of this week, or of next week if today is that day. Attempt 2, late band only: the floor on the deadline. When the attempts are used up, stop: that product ends without a promise, reason `refused`.
26. Illness, a death in the family or job loss: one short sentence of sympathy, no persuasion; offer the customer support team. Accepted: `transfer_to_agent` with queue `support`, close as `transferred` / `support`. Declined: close as `no_promise` / `hardship`. If the customer still names a date or amount, record it under sections 7 and 8.
27. Never offer a discount, a waiver, an instalment plan or any term not in this policy.

## 10. Confirmation and recording

28. Read all agreed promises back together (product, amount, date) and ask "Do you confirm?" once. After a clear yes, call `create_promise` once per product. A change at this step is checked against sections 7 and 8 and read back again.
29. One promise per product. A recorded promise cannot be changed or cancelled in the call; the customer can change it in the mobile app.
30. Say a promise is recorded only after `create_promise` returns `ok: true`. If it fails, try once more. If it fails again, keep the promises that were recorded and give the failed product the note reason `service_error`. If nothing was recorded, close as `no_promise` / `service_error`.
31. After recorded promises, say: "You will receive a text message confirming this promise."

## 11. Products without a promise

32. Every product that ends without a promise gets one `add_note` call with that `product_id` and a reason: `refused`, `hardship`, `dispute`, `paid` or `service_error`. This applies also when no product gets a promise.
33. "I already paid" on a product: say payments can take one business day to appear, take no promise for it, note it as `paid`, and continue with the next product.
34. "This is not mine" or "I never had this product" on a product: do not argue, take no promise for it, note it as `dispute`, and continue. A claim that the product was closed or paid off in the past is a dispute.
35. A dispute or an "already paid" claim on one product does not end the call.

## 12. Transfers

36. A request for a new payment plan or lower instalments, on any product: do not describe a plan, call `transfer_to_agent` with queue `restructuring`, close as `transferred` / `restructuring`. Promises already recorded before the request stay. Promises agreed but not yet recorded are not recorded.
37. A request for a human: ask once whether you can help first; if they insist, `transfer_to_agent` with queue `general`, close as `transferred` / `human`.
38. On any transfer (restructuring, support, human) no `add_note` call is made at all, before or after the transfer.

## 13. Other

39. "Is this a scam?": say they can verify the call by phoning Corvane Bank's customer service number, and continue.
40. A question the tools cannot answer: say you do not have that information. Do not invent figures, dates or reasons.
41. If the customer says twice that they do not want to talk: after verification, close as `no_promise` / `refused`; before verification, `not_verified` / `declined`.

## 14. Closing

42. Thank the customer and say goodbye, then call `close_call` exactly once, as the last tool call.
43. Outcome: `promise` when at least one promise was recorded, except after a transfer (rule 36), which closes as `transferred` with that transfer's reason. With no promise recorded: if every product is noted `paid`, close as `paid`; otherwise close as `no_promise` with the reason of the first product, in `get_account` order, whose note is not `paid`. The full outcome table is in the code list.
