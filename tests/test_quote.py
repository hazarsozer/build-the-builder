from builder.extract import fact_quote_misses, quote_in_brief

BRIEF = "Rule 18. The promise deadline is 10 business days after today. Holidays: 2026-10-26.\nCall `transfer_to_agent` with queue `general`."


def test_long_exact_quote_found():
    assert quote_in_brief("the promise deadline is 10 business days", BRIEF)
    assert quote_in_brief("call `transfer_to_agent` with queue `general`", BRIEF)   # settle's style


def test_short_quote_rejected():
    assert not quote_in_brief("10 business days", BRIEF)           # 3 words
    assert not quote_in_brief("is 10 a b", "is 10 a b c")          # 4 words, under 20 chars
    assert not quote_in_brief("", BRIEF)


def test_word_boundaries():
    # substring inside words: "romise deadline is 10 business" must not match "promise deadline is 10 business"
    assert not quote_in_brief("romise deadline is 10 business day", BRIEF)
    assert not quote_in_brief("deadline is 10 business days after toda", BRIEF)


def test_fact_misses_keep_short_facts():
    spec = {"facts": [{"id": "f1", "quote": "2026-10-26"}, {"id": "f2", "quote": "not in brief at all"}]}
    assert fact_quote_misses(spec, BRIEF) == ["f2"]
