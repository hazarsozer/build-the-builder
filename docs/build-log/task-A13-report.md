# Task A13 report

Base d77a197. Three commits on top (lint, main, tests), plus this report.

## Changes
- builder/lint.py:49 `required_pre_verify_sentences(spec)`: opening.first_sentence (after the safe_opening rewrite),
  opening.confirm_question, identity question asks, identity.non_counting_hints, identity.decline_explanation,
  third_party.fixed_sentences.
- builder/lint.py:72 `reconcile_banned(spec)` -> (spec, removals). Case-insensitive, word boundary, leading "the" stripped
  from the term. Pure (copies); returns the same object when nothing changes.
- builder/main.py:403 `Build._reconcile`: emits trace event `banned_reconciled {stage, removed[{term, found_in, sentence}]}`
  plus a log line. Called at main.py:437 (after extract validation, only when it has no errors) and main.py:579 (after
  fix-round validation). cases.banned() and the compiler read the cleaned spec, so cases.py/compile.py are unchanged.
- tests/test_a13.py (8 tests), tests/fixtures/a13-dress-B.spec.json (full dress-B spec).

## Compile identity (spec-a / spec-b)
reconcile_banned returns the same object with no removals; json of comp.compile is identical
(test_reference_specs_unchanged_and_compile_identical).
dress-B: "the account" removed (found in identity.decline_explanation); compiled workflow lints clean; the other 7 terms
stay banned; a term in no required sentence stays banned (tests).

## Suite
`uv run pytest -q`: 602 passed, 6 skipped (base 594, +8).

## Fix round 1
1. main.py: `_reconcile` now runs unconditionally at the extract and fix1 stages (still wrapped in try/except; lint.py guards
   non-dict opening/identity/questions). Tests: test_main_reconciles_even_when_validation_errors, test_reconcile_is_defensive,
   test_fix1_stage_reconciled.
2. lint.py `required_pre_verify_sentences`: evidence is now only opening (first sentence after rewrite, confirm question),
   identity question asks and decline_explanation. third_party.fixed_sentences and non_counting_hints removed
   (test_hints_and_third_party_sentences_are_not_evidence).
3. test_reference_specs_unchanged_and_compile_identical compares against a deepcopy compiled without reconcile.
Suite: 606 passed, 6 skipped (tests/test_a13.py: 12 passed).
