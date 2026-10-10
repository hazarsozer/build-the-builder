close-codes-2026 (export of sheet "Outbound close codes", Northwind Mutual, last edited 2026-09-21)

Use: every call ends with one `close_call`. Send the `outcome` and the `reason` of the code. A dash means: send no reason.

| Code | outcome | reason | Used when |
|---|---|---|---|
| P01 | promise | — | at least one promise was recorded |
| P02 | paid | — | the customer already paid the past-due amount |
| T01 | transferred | restructure | transferred to the restructuring queue (new plan, lower instalments) |
| T02 | transferred | human_request | transferred to the general queue (customer insists on a person) |
| T03 | transferred | support_team | transferred to the support queue (hardship, customer accepted) |
| N01 | no_promise | declined_to_pay | attempts used up, or customer said twice they do not want to talk |
| N02 | no_promise | financial_hardship | hardship, customer declined the support team |
| N03 | no_promise | disputed | customer disputes the debt or the product |
| N04 | no_promise | system_error | create_promise failed twice |
| V01 | not_verified | id_failed | three wrong security answers |
| V02 | not_verified | id_declined | customer declined the security questions |
| R01 | not_reached | callback_booked | callback scheduled with someone else |
| R02 | not_reached | no_callback_time | no callback time inside the window |
| R03 | not_reached | wrong_number | the number is not the customer's |
| R04 | not_reached | deceased | the customer has died |
| R05 | not_reached | unclear_contact | still unclear who answered after the second ask |
| N09 | no_promise | broken_promise | RETIRED 2025-06. Do not use. |

Queue names for `transfer_to_agent` are unchanged: `restructuring`, `general`, `support`.
