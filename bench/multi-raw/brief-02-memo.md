**From:** Ines Varga, Head of Collections Operations, Corvane Bank
**To:** Voice assistant vendor; Collections team leads
**Date:** 2 October 2026
**Subject:** RC-4 change — product note codes (effective immediately)

Hello all,

Our case-management system was migrated last week. The new system uses different codes for the note left on a product that ends a call without a promise, and it rejects the old codes. From today, RC-4 rules 25, 30, 32, 33 and 34 use the new codes below. Nothing else in RC-4 changes.

| Old note code (RC-4) | New note code | Used when |
|---|---|---|
| `refused` | `declined` | attempts used up, no promise (rule 25) |
| `hardship` | `hardship` | unchanged |
| `dispute` | `disputed` | "not mine" / "never had it" (rule 34) |
| `paid` | `payment_claimed` | "I already paid" (rule 33) |
| `service_error` | `system_error` | `create_promise` failed twice (rule 30) |

Please note: **the call outcome codes for `close_call` do not change.** The reporting team still reads `refused`, `dispute`, `hardship` and `service_error` as `no_promise` reasons. So RC-4 rule 43 now reads:

> With no promise recorded: if every product is noted `payment_claimed`, close as `paid`. Otherwise close as `no_promise` with the close reason that matches the note of the first product, in `get_account` order, whose note is not `payment_claimed`: `declined` → `refused`, `disputed` → `dispute`, `hardship` → `hardship`, `system_error` → `service_error`.

The rest of rule 43 (promise wins when anything was recorded; a transfer closes as `transferred` with its reason; no notes on transferred calls) is unchanged.

The updated code list is attached. Thanks for turning this round quickly.

Ines
