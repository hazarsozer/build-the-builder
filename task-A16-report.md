# Task A16 report

Added optional boolean `opening.verbatim`. When true: `safe_opening(sentence, verbatim)` returns the sentence untouched (compile.py passes the flag); lint turns banned-word hits in the first sentence into warnings, strips that sentence from the opening-line banned-word check and from the 1b "record* before success" check (the rest of the line, e.g. confirm_question, is still checked). Absent/false: unchanged behaviour. Schema, docs/spec-guide.md (regenerated) and builder/prompts/extract.md carry neutral wording.

Checked other word-for-word fields: safe_opening is the only rewriting function and is used only for opening.first_sentence; confirm_question, events say, first_ask(_all), post_verify_sentence, post-promise sentence, disclosure lines are never rewritten by compile or lint (lint only flags "record*" in opening/identity/negotiate text as errors, no rewrite). A13 reconcile: no first_sentence use found in extract.py/specdiff.py (only cases.py reads it), so nothing to change.

Tests: tests/test_a16.py (4): insurance verbatim byte-exact + lint no errors; no flag/false still rewritten; verbatim exempts only the first sentence; reference specs lint clean, no flag. Full suite: 753 passed, 6 skipped.
Concern: the bench-insurance fixture does not set verbatim (extractor decides it).

## Merge of 635644b
Merged cleanly (no conflicts); opening.verbatim wording passes test_no_corpus_words_in_generic_text. Reconcile (lint.required_pre_verify_sentences) is on branch builder (071db36), not in this lineage; skipped per controller. At final merge it must use opening.first_sentence unchanged when opening.verbatim is true.
