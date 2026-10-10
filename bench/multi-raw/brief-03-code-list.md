# Corvane Bank — Collections code list (attachment to memo of 2 October 2026)

## A. `close_call` outcomes and reasons

| Outcome | Reason | When (RC-4 rule) |
|---|---|---|
| `promise` | none | at least one promise recorded (43) |
| `paid` | none | no promise recorded and every product noted `payment_claimed` (33, 43) |
| `transferred` | `restructuring` | plan or lower instalments requested (36) |
| `transferred` | `human` | customer insists on a person (37) |
| `transferred` | `support` | hardship, support team accepted (26) |
| `no_promise` | `refused` | first non-`payment_claimed` note is `declined`; or told twice they do not want to talk after verification (25, 41, 43) |
| `no_promise` | `hardship` | hardship, support team declined; or first non-`payment_claimed` note is `hardship` (26, 43) |
| `no_promise` | `dispute` | first non-`payment_claimed` note is `disputed` (34, 43) |
| `no_promise` | `service_error` | nothing recorded because `create_promise` failed twice; or first non-`payment_claimed` note is `system_error` (30, 43) |
| `not_verified` | `failed` | three wrong security answers (11) |
| `not_verified` | `declined` | refused the security questions twice; or did not want to talk before verification (13, 41) |
| `not_reached` | `callback` | third party, callback booked (7) |
| `not_reached` | `no_callback` | third party, no time inside the window (7) |
| `not_reached` | `wrong_number` | number not the customer's (8) |
| `not_reached` | `deceased` | customer has died (8) |
| `not_reached` | `unclear` | person on the line not confirmed after two asks (4) |

## B. `add_note` reasons (one per product without a promise; never on a transferred call)

| Code | Meaning | Maps to close reason |
|---|---|---|
| `declined` | persuasion attempts used up | `refused` |
| `hardship` | hardship on this product | `hardship` |
| `disputed` | customer says the product is not theirs | `dispute` |
| `payment_claimed` | customer says this past-due amount is already paid | — (all products `payment_claimed` → outcome `paid`) |
| `system_error` | `create_promise` failed twice for this product | `service_error` |

## C. `transfer_to_agent` queues

| Queue | Used for |
|---|---|
| `restructuring` | payment plans, lower instalments |
| `general` | customer insists on a person |
| `support` | hardship |

## D. Product words

| `type` | Say |
|---|---|
| `credit_card` | "credit card ending {card_last4}" |
| `personal_loan` | "personal loan" |
| `car_loan` | "car loan" |
