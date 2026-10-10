Two independent readings of the same bank brief produced `spec-a.json` and `spec-b.json`. An independent examiner also wrote test scenarios (`scenarios.json`) from the brief alone; their expected results were recomputed from `spec-a.json`.

`questions.json` lists:
- `disagreements` ($n_diff): spec paths where the two readings differ, with both values (`a_absent` / `b_absent` mean the path is missing on that side);
- `oracle_mismatches` ($n_mismatch): scenario results that differ from what `spec-a.json` computes, with the spec paths involved.

## Files

The brief: $briefs. `openapi.json`, `spec_schema.json` and `spec-guide.md` describe the tools and the spec. When documents conflict, the latest dated decision wins, even unlabelled; a document that only proposes, discusses or forwards decides nothing.

## Your job

Decide each question from the brief, not from either reading.
- For a disagreement: choose the value the brief supports (it may be a's, b's, or a third value).
- For an oracle mismatch: if the brief supports the scenario, fix the spec; if it supports the spec, reject the scenario. Examiners make arithmetic mistakes: recount business days one by one before you decide.

Write `settle.json`:

```
{
  "decisions": [ {"path": "...", "value": <the full JSON value at that path>, "quote": "exact brief words", "why": "one line"} ],
  "rejected": [ {"scenario": "s03", "quote": "exact brief words", "why": "one line"} ]
}
```

- Decisions are applied to `spec-a.json`. Only list paths whose value should differ from `spec-a.json`; a question settled in a's favour needs no decision.
- `value` has the shape of the spec at that path: an `events[<id>]` path takes the whole event object, `outcomes` the whole table. To delete a path, give `"remove": true` instead of `value`.
- Keep the spec consistent: a changed event needs its matching `outcomes` rows (and the reverse).
- Every decision and every rejection needs a `quote`. A change without a quote is discarded.

Reply with the single word `done`.
