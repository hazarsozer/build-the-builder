From: Ingrid Halvorsen <i.halvorsen@northwind-mutual.example>
To: Collections Operations; Digital Channels
Date: Mon, 28 Sep 2026 09:12
Subject: Autumn early-stage campaign: the phone assistant goes live

Hi all,

From Monday 5 October our digital assistant starts calling customers who are 1 to 45 days late on one product: a credit card, a personal loan or a car loan. Northwind Mutual is a fictional bank and every customer in the pilot data is synthetic, so nothing here is real money.

What the assistant gets at the start of every call: `customer_id`, `first_name`, `last_name`, `title` (Mr or Ms) and `now`, the date and time of the call. It knows nothing else until it uses the tools. It passes `customer_id` to every tool call, addresses the customer as title plus last name, and uses `now` for every date rule, never the real clock.

The rules the assistant follows are in the attached extract of the Early Collections Policy (EC-POL, version 4.2). The close codes it uses are in the attached outcome code list. Please read both before the go-live call on Thursday.

The tools are the usual seven: verify_identity, get_account, create_promise, add_note, schedule_callback, transfer_to_agent, close_call. The early-stage campaign does not use add_note.

Thanks,
Ingrid
Head of Collections Operations, Northwind Mutual

Attachments: EC-POL-4.2-extract.pdf, close-codes-2026.xlsx
