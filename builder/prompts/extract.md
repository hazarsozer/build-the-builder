You turn a bank's call-policy brief into a policy spec: one JSON file that a generic compiler turns into an outbound phone agent. The compiler knows nothing about this bank. Every fact the brief states must end up in the spec, or the agent will not follow it.

## Files in this directory

- The brief: $briefs. It may be one clean document, or raw material: several documents such as mails, policy extracts, memos, meeting notes and code lists.
- `openapi.json`: the bank's tools. Its enums are the only allowed values for close_call outcomes, transfer queues, add_note reasons, verify_identity fields and product types. get_account's product fields are the only product fields.
- `spec_schema.json`: the JSON Schema the spec must satisfy.
- `spec-guide.md`: the same schema as a field guide, with the meaning of every field and enum value. Read it in full.

## Steps

1. Read every brief file in full, then `openapi.json` and `spec-guide.md`. Open `spec_schema.json` only to check an exact constraint.
2. **Fact pass.** Write `facts.json`: a JSON list of `{"id", "quote", "maps_to"}`, one entry per checkable statement in the brief. Checkable means: a number, limit, count, range, date, time, list member, a sentence to say, a tool call, an outcome or reason, a field format, an order or precedence between rules. `id` is short (`f1`, `f2`, ...). `quote` is the brief's exact words, short but enough to find the statement. `maps_to` is the one spec path that holds it (path syntax in `spec-guide.md`: dotted keys, `[index]`, or `[id]` for events, e.g. `bands[1].deadline.n`, `events[dispute].reason`). A rule no structured field can express maps to the `extra_rules[n]` entry you write for it. Do not drop a fact because it is awkward to place.
3. Write `spec.json`, so that every `maps_to` resolves to a field you filled from that fact.
4. Reply with the single word `done`. Do not explain.

## Reading raw material

- Put the documents in date order. When two documents decide the same thing differently, **the latest dated decision wins**, even when the later document does not say it changes anything. A document that only proposes, discusses, asks, forwards or quotes, or that is marked draft, superseded or out of scope, decides nothing.
- A later document that extends an earlier one keeps every earlier rule it does not replace.
- A document scoped to a segment, product or period applies only there.
- Record every override in `conflicts`: `field` is the spec path, `kept` the value used and its document, `overridden` the older value and its document, `why` one line. Also record each gap you had to fill: the most conservative choice, with `why`.
- A code list maps internal codes to outcomes or reasons. Use the openapi enum value in the spec, never the internal code.

## Writing the spec

- **Sentences.** When the brief quotes a sentence or says to say a given sentence, copy it into the matching field word for word, including punctuation (event `say`, `first_ask`, `confirmation.question`, `confirmation.success_sentence`, `third_party.fixed_sentences`, identity `ask`, `say_rules`). When the brief only describes what to say, write one short plain sentence that says exactly that and nothing more.
- **Placeholders** use single braces, `{name}`, and only the names the schema allows: call variables, get_account product fields, and the runtime names listed in the schema's top-level description.
- **Opening.** `first_sentence` must contain every fact the brief requires in the first sentence. `banned_before_verify` lists every word or topic the brief forbids before verification, including the product names when products may not be named.
- **Events.** One event per distinct situation that changes the call's course or ends it, even when two situations share an outcome and reason; each event has its own `outcomes` row. Use the known event ids from the `events` description whenever the situation matches one. Before inventing an id, check whether an event you already have closes with the same outcome and reason in the same stage; if so, widen that event's trigger instead of adding a new event. Invent a short snake_case id only for a situation no such event covers. Every event's outcome/reason (and `else_outcome`/`else_reason`) appears in `outcomes`, and every `outcomes` row names its event. A situation that only needs a fixed answer and then continues is a `say_rules` entry, not an event.
- **Stages.** `any` means opening, identity and negotiate. It does not include `third_party`: list `third_party` explicitly for a situation a third party may raise.
- **Outcomes.** List every openapi close_call outcome either in `outcomes` or in `unused_outcomes`.
- **Bands** cover every `days_late` value from the brief's lowest to highest, with no gap and no overlap. `attempts` holds the persuasion attempts in order; their count is the number allowed.
- **Calendar.** `weekdays` uses 0 = Monday. List every non-business date the brief gives in `holidays`.
- **Products.** One entry per openapi product type, with how it is said and its floor field.
- **multi_product** is required even for a one-product brief. Fill it with the brief's rules, or neutral values, and set `notes.enabled` to false when the brief never uses add_note.
- **extra_rules** only for rules no structured field expresses, tagged with the stage they apply to. Never restate a rule a field already holds: duplicated wording competes with the computed values. The compiler already enforces the call mechanics every brief shares: passing the call's ids to each tool, one tool call per answer, checking promise dates and amounts against the deadline, calendar and floor, the persuasion ladder, read-back before a tool call, only claiming success after a tool returns ok, and one final close_call. A fact about those maps to the field it concerns (calendar, bands, negotiation, confirmation, outcomes, events), not to a new extra_rules entry. Most briefs need only a handful of extra_rules.
- **custom_code** only for a date or amount calculation the enums cannot express.
- **examples.** One entry for every worked date example in the brief, with concrete dates. Add three to six more that you compute from the band table and calendar: a deadline in each band, at least one crossing a holiday, and an attempt date per attempt kind. Count business days one by one. The validator recomputes every example; a failing example means a band, the calendar or your arithmetic is wrong. Find which before changing anything.
- **sources.** Give a citation (rule number, section or document name) for every top-level section and every event.
