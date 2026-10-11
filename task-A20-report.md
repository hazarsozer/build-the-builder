# Task A20 report: export readability cleanup

Base 1b43106 (A19 tip). Branch `worktree-agent-a83d36b3a65b4e8fb`. Commits 9168759..843576d (7), plus this report.
Not merged. No Freya calls.

| Commit | Item |
|---|---|
| 9168759 | 1. trigger templates (new `builder/triggers.py`) |
| 4fb73c2 | 2. internal labels in shipped hook comments |
| dc6197d | 3. "brief" wording, garbled not-talk line and edge |
| c52a4e6 | 4 + 5. always-edge labels, code_exec / api step descriptions (new `builder/labels.py`) |
| 1cb7498 | 6. check_date / set_product descriptions |
| d58319c | 7. trace .md: relative paths, duplicate row, "tunnel" wording |
| 843576d | extra (from the controller): `layout._sample` whole-word matching |

Tests: `tests/test_a20.py` (new, 74 cases) and 20 new cases in `tests/test_layout.py`.
Full suite: **885 passed, 6 skipped** (base 791). Reference specs compile with lint clean (exit 0, no warnings).
Corpus-word test passes. spec-a negotiate task_message: 4430 -> 4415 chars (limit 4800).

## 1. Trigger templates

Cause: spec triggers come in several shapes ("The customer asks X.", "asks X", "After X, the customer says Y.",
"Asked whether ..."), and about 15 templates added their own subject and full stop around the raw text.
`builder/triggers.py` splits a trigger once into (subject, predicate, further sentences): it strips a leading
"The customer / The person / They", drops the trailing full stop, and lowercases the first word. Every place that
embeds a trigger now uses it: say rules, event guidance lines, extraction enum descriptions, NOT-an-event lists,
ask-once replies, the support-offer situation, seen-flags, multi-product readback, the set_product outcome text, and
the backup and counted-close tool descriptions. A trigger's second sentence ("Do not invent figures ...") now comes
after the rule instead of inside the `If` clause. A leading time clause moves to the end:
"After identity is verified, the customer says X" becomes "the customer says X (after identity is verified)".

The reference specs a and b use verb-led lowercase triggers, so this part does not change their output. The
extracted specs (dress-A/B, bench) do change. Doubled-subject or doubled-stop hits: dress-A 51 -> 0, dress-B 57 -> 0,
ids 54 -> 0, insurance 56 -> 0.

Before / after (dress-B fixture spec):

```
- If the person The customer asks a question the tools cannot answer. Do not invent figures, dates or reasons.: say "I do not have that information.", then continue.
+ If the customer asks a question the tools cannot answer: say "I do not have that information.", then continue. Do not invent figures, dates or reasons.

- If the person The person asks whether you are a robot or an AI.: say "I am Freya Bank's digital assistant.", then continue.
+ If the person asks whether you are a robot or an AI: say "I am Freya Bank's digital assistant.", then continue.

-   - paid, when the customer The customer says they already paid this past-due amount.; then continue with the other products.
+   - paid, when the customer says they already paid this past-due amount; then continue with the other products.

- If The customer asks to speak to a human.: the ask-once question is spoken automatically.
+ If the customer asks to speak to a human: the ask-once question is spoken automatically.

set_product outcome:
- paid: the customer The customer says they already paid this past-due amount.. dispute: the customer The customer says ...
+ paid: the customer says they already paid this past-due amount. dispute: the customer says ...

extraction (negotiate):
- yes if anywhere in the conversation the customer The customer mentions illness, a death in the family or job loss.. Otherwise no.
+ yes if anywhere in the conversation the customer mentions illness, a death in the family or job loss. Otherwise no.

ask-once reply:
- insists: the person still The customer asks to speak to a human. (says yes, repeats it, or refuses help).
+ insists: the person still asks to speak to a human (says yes, repeats it, or refuses help).
```

Tests: `test_trigger_clause` (8 shapes), `test_no_doubled_subject_or_full_stop` (6 specs, regex over every string),
`test_trigger_extra_sentence_is_kept`.

## 2. Internal labels in shipped code

13 comments and docstrings inside embedded hook and code-node source carried "(A11)", "(A11 probe)", "A12 fix 4",
"A14", "A15 (IDS-08)" or "probe 4c48412b". They are in builder/multi.py, builder/compile.py (embedded snippets) and
builder/lib/dates.py. The labels are gone and the wording is kept. Comments that never reach the export (builder-side
Python) are unchanged.

```
- # parallel set_product calls each read the same snapshot of vars and their writes merge per variable (A11 probe):
+ # parallel set_product calls each read the same snapshot of vars and their writes merge per variable:
- # Confirmed at the read-back but not recorded: the service-failure note, never the refused note (A11)
+ # Confirmed at the read-back but not recorded: the service-failure note, never the refused note
- # the clock as the pre-hook read it; the post-hook uses this copy (probe 4c48412b read the turn in a pre-hook only)
+ # the clock as the pre-hook read it; the post-hook uses this copy (the turn is readable in a pre-hook only)
```

Tests: `test_no_internal_labels_in_export` greps compiled spec-a/spec-b JSON, with and without a base URL, for
`\bA\d{1,2}\b|probe|TODO|FIXME|hack`. Result: 0 hits. The same check runs on all 6 fixture specs. The regex is
case-sensitive, as in the brief: a lowercase `todo` variable in the check_date hook stays.

## 3. "The brief", the not-talk line

- Headings: "Rules from the brief:" -> "Rules:" (system prompt, opening, third_party, identity); "RULES FROM THE BRIEF"
  -> "RULES" (negotiate, both paths); "The brief's rule for this situation:" -> "The rule for this situation:".
- Banned offers from extracted specs ("any term that is not in this brief") now read "... not in your instructions".
  The system prompt's "Never offer ..." line, which already ends "or any term not given in your instructions", drops
  that item, so the clause is not doubled (audit 2.7).
- The not-talk line and edge (dress-B):

```
- If the customer After identity is verified, the customer says for the second time that they do not want to talk.: call transition_to_wrap_not_talk.
+ If the customer says for the second time that they do not want to talk (after identity is verified): call transition_to_wrap_not_talk.
edge description:
- Call this when the customer After identity is verified, the customer says for the second time that they do not want to talk.. The goodbye is spoken automatically.
+ Call this when the customer says for the second time that they do not want to talk (after identity is verified). The goodbye is spoken automatically.
edge condition (matches_intent) description:
- The customer After identity is verified, the customer says for the second time that they do not want to talk.
+ The customer says for the second time that they do not want to talk (after identity is verified)
system prompt (dress-B):
- Never offer a discount, a waiver, an instalment plan, any term that is not in this brief, or any term not given in your instructions.
+ Never offer a discount, a waiver or an instalment plan, or any term not given in your instructions.
```

Tests: no `\bbrief\b` in any string of the 6 compiled specs; plain headings; banned clause said once; exact not-talk
edge and line; spec-a negotiate ≤ 4800.

## 4 + 5. Edge labels and step descriptions

`builder/labels.py` runs in `build()` after the graph is complete and before layout. It changes presentation only:

- An `always` transition labelled "always" becomes "Next: <target in words>". When the node also has conditional
  routes it becomes "Otherwise: <target>". An end_call target reads "end the call"; a close target reads
  "close the call (<event>)". Examples (spec-b): `load_account` "Next: prepare account"; `finish`
  "Otherwise: close the call (service error)" (audit 1.6); close nodes "Next: end the call".
  Only passthrough code/api nodes have always-edges, so no model-visible text changes.
- Each code node's `code_exec` pre-action (none had a description) gets one line from the builder's own node-name
  families. Examples: `setup` "Reads the call variables (customer, clock) and sets safe defaults for the call.";
  `prepare_account` "Works out the facts of every overdue product (amounts, dates, offer plan) from the account data.";
  `close_paid` "Sets the outcome, reason and farewell for: paid."; `human_ask_identity` "Marks that the agent asked once
  whether it can help first (human, identity step)."; `record_plan` "Lists the confirmed promises to record, one slot
  per product."
- Pre-action api tools whose description was their own name: get_account "Fetch the customer's account and overdue
  products.", create_promise "Record a payment promise for one product.", add_note "Add a note to the customer's
  account.", transfer_to_agent "Transfer the call to a team at the bank."

Tests: no "always" labels remain; every pre-action has a description that is not its name and not the generic fallback
(6 specs); `describe()` is idempotent; transitions keep only their own keys.

## 6. Read-only tools

```
check_date:
- ... say what what_to_do_next says. It records nothing.
+ ... say what what_to_do_next says. It records nothing: it only reads the account and changes no bank record.
set_product (api mode, the one shipped):
- ... (agreed, paid, dispute). Then follow what_to_do_next.
+ ... (agreed, paid, dispute). Then follow what_to_do_next. The answer is kept in the call; the call only reads the account and changes no bank record.
```

Code mode (not shipped) says "The answer is kept in the call and changes no bank record." The CALL rules are unchanged:
they steer the model.

## 7. Trace .md (builder/trace.py)

- All output passes through `relative_paths()`. Repo and working-directory paths become relative, and the home
  directory becomes `~`. A sibling path such as `<repo>-other/` is not cut.
- "Created on Freya" lists one row per saved agent. A self-test agent reused across rounds is one row with
  "self-test rounds 1, 2". The column "Base URL kind" (tunnel/official) is now "Bank service"
  ("local mock bank (self-test)" / "official mock bank"), with a new "Used for" column.
- Title: "Build trace: dress-B (run 20261011T013308Z)" (was "Build trace 20261011T013308Z-dress-B.jsonl").

```
- | dress-B-selftest | <wf> | <agent> | tunnel |
- | dress-B-selftest | <wf> | <agent> | tunnel |
- | dress-B | <wf> | <agent> | official |
+ | dress-B-selftest | <wf> | <agent> | local mock bank (self-test) | self-test rounds 1, 2 |
+ | dress-B | <wf> | <agent> | official mock bank | final agent |
- - /home/hazarsozer/Dev/freya-builder-kit/build-the-builder/submission/dress-B.workflow.json
+ - submission/dress-B.workflow.json
```

Tests: no repo/home path and no "/home/" in the rendered md; export path relative; one self-test row; no "tunnel".

## Extra: layout `_sample` whole words

`_sample` now splits a name into words (underscores, other non-word characters, camelCase). title/honorific/salutation
must be whole words. first/given and last/family count only next to the word `name`; firstname, lastname, surname and
forename count as single words. Results: `last_payment_date` -> "Last Payment Date" (was "Reyes"), `subtitle` ->
"Subtitle" (was "Ms"), `first_payment_date` -> "First Payment Date" (was "Dana"). `title`, `first_name`, `last_name`,
`company_name`, `customer_id` keep their samples. 20 parametrised cases in tests/test_layout.py.

## Behaviour unchanged: verification

I compiled 6 specs (reference spec-a and spec-b, plus fixtures a10-dress-A, a13-dress-B, bench-insurance and
bench-ids), each with and without a base URL, at base 1b43106 (from `git archive`) and at HEAD. Then I diffed every
leaf:

```
   116  code: comments/docstrings only         (AST equal after dropping docstrings; comments do not reach the AST)
   536  description: always-transition label
    84  description: extraction description    (trigger wording, "brief" -> "your instructions")
   112  description: pre-action api description
    16  description: tool/parameter description (check_date, set_product + its outcome text)
    16  description: transition description    (backup / counted-close tool calls)
    16  description: transition condition (matches_intent)  (the garbled not-talk text; same intent)
   424  new: pre_actions function description
    10  prompt: system_prompt
    54  prompt: task_message
UNEXPECTED: 0
```

Every node id, node type, transition target, condition operator, variable, operand and logic group, extraction
variable name and enum, tool name, tool parameter schema (types, enums, defaults, required), response mapping,
on_enter text (all spoken lines), position and test_variable is identical. The only added keys are `description` on
pre-action functions. For spec-a and spec-b the prompt changes are only "Rules from the brief" -> "Rules", "RULES FROM
THE BRIEF" -> "RULES" and "The brief's rule" -> "The rule"; the tool changes are the check_date and set_product
sentences.

## Concerns

- matches_intent condition descriptions changed in wording for counted closes and backup closes when the trigger had
  its own subject. Example: "The customer After identity is verified, the customer says ..." became "The customer says
  ... (after identity is verified)". The platform's intent model reads this text. Same meaning, but it is not byte-identical
  to base. The reference specs a and b are not affected (their triggers are verb-led), only extracted specs.
- Extraction descriptions in extracted specs changed from the garbled text to clean text. They are model-read. The
  meaning is unchanged, but no simulation was run (no Freya calls).
- When a time clause moves to the end in the identity "declined" description, the parenthetical "(before identity is
  verified)" now sits after the last disjunct. Every case in that stage is before verification, so this does not matter.
- Not done (outside the brief): node renames (audit 1.2-1.5, 1.9), agent_config Turkish defaults (2.13), the duplicate
  sympathy step in hardship_hold (2.9), the "the product" repeat (2.11), and the "Embedded as source text into Freya
  code nodes" docstring in builder/lib (4.3, accurate and harmless).
- Step descriptions key on the builder's own node-name families (labels.py). A future new node family without an entry
  would get "Code step: <name>.", and `test_every_step_has_a_description` would flag it.

## Fix round 1 (commit 9593394)

1. **Backup-close matches_intent** (compile.py, `event_values`). It now uses `sentence("the customer", trig)`, which
   is `subj()` with a capital first letter, and the counted-close condition uses the same helper. Before:
   `"The customer %s" % bare_trigger(trig)`. With "After identity is verified, the customer says the debt is not
   theirs." it now gives "The customer says the debt is not theirs (after identity is verified)". With "The person
   (not the customer) says ..." it keeps "The person (not the customer) says ...". Test:
   `test_backup_close_intent_keeps_own_subject`. No reference or fixture spec has a backup-close trigger in those
   shapes, so the compiled diff did not change here.
2. **Refused matches_intent** (single-product negotiate). My first A20 pass did **not** touch this field: it was still
   `"Customer: " + raw trigger`. Now it is `sentence("the customer", trigger) + "." + trigger_rest(trigger)`. This
   changes spec-a: `Customer: the persuasion attempts are used up after flat refusals or non-answers` becomes
   `The persuasion attempts are used up after flat refusals or non-answers.` The same field changes for dress-A (the
   "Customer: " prefix is dropped and the definition sentences are kept) and for ids. spec-b is multi-product and has
   no such edge. Test: `test_refused_intent_is_normalised`.
3. **Abbreviations.** The sentence split skips a full stop after Mr, Mrs, Ms, Dr, St, No, e.g., i.e., etc., vs,
   approx, Jr, Sr, Prof or a single-letter initial. Test: `test_sentence_split_skips_abbreviations`.
4. **verb_led.** A subject is added only when the first word is in a known verb list (asks, says, mentions, refuses,
   declines, gives, offers, still, cannot, ...). Otherwise the trigger is left as it is: "Fees are disputed" becomes
   "fees are disputed", with no subject. Test: `test_subject_only_before_a_known_verb`. No compiled output changed.
5. **Internal-word grep.** Work words now match case-insensitively on whole words (`probe`, `todo`, `fixme`, `hack`).
   Task labels are case-sensitive (`\bA\d{1,2}\b`, `\b[A-Z]{2,4}-\d{2}\b`), so the hook variable `a2` is not
   flagged. The stricter grep found the local variable `todo` in the check_date pre-hooks; it is renamed to
   `next_step`. That is code, but only a rename: the AST is identical once `todo` is mapped to `next_step`.
   Test: `test_internal_patterns_catch_labels`.

**New test:** `test_hostile_trigger_shapes_never_double`. It rewrites every event and say-rule trigger in spec-a,
dress-A, dress-B and insurance into each of six shapes: a leading after-clause, a leading before-clause with "the
person", "The person (not the customer) says", "They say", "Asked whether", and a two-sentence trigger. It then
checks every string in the compiled workflow for a doubled subject or a doubled full stop, and checks that each
matches_intent description starts with a capital letter. This found one more site: the hardship-hold extraction's
`human_request: they %s`. It produced "they after identity is verified, ..." and, on the real specs, "they asks for a
human or a person". It now reads "the customer asks for a human or a person".

**Re-verification against base**, over the same 6 specs x 2 base-URL variants:

```
   116  code: comments/docstrings only (plus the todo -> next_step rename)
   536  description: always-transition label
    88  description: extraction description
   112  description: pre-action api description
    16  description: tool/parameter description
    22  description: transition condition (matches_intent)
    16  description: transition description
   424  new: pre_actions function description
    10  prompt: system_prompt
    54  prompt: task_message
UNEXPECTED: 0
```

Nodes, transition targets, condition operators, variables and operands, extraction names and enums, and tool schemas
are still unchanged. Full suite: **922 passed, 6 skipped**. Reference specs lint clean.

**Concern added:** the refused matches_intent text is a routing field that the platform's intent model reads. It is
now worded differently for spec-a and the single-product fixtures. The meaning is the same, but it has not been
simulated.
