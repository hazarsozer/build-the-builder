"""D2: the compiled workflow speaks the brief's language (builder.localise). No model calls: fake translators."""
import copy
import json
import re
from pathlib import Path

import pytest

from builder import compile as comp
from builder import localise as L
from builder.lint import lint
from builder.validate import validate

ROOT = Path(__file__).resolve().parent.parent
OA = json.load(open(ROOT / "tools/openapi.json"))
A = json.load(open(ROOT / "specs/reference/spec-a.json"))
B = json.load(open(ROOT / "specs/reference/spec-b.json"))
TR = {"code": "tr", "name": "Turkish"}


@pytest.fixture(autouse=True)
def _clear_cache():
    L._CACHE.clear()
    yield
    L._CACHE.clear()


def fake_tr(texts, lang):
    """Round-trips: a marker prefix, placeholders and numbers untouched."""
    return ["[TR] " + t for t in texts]


def compiled(spec, lang=None):
    s = copy.deepcopy(spec)
    if lang:
        s["language"] = lang
    return comp.compile(s, OA, "http://x/v1"), s


def test_detect_language():
    assert L.detect_language("Müşteriye ödeme tarihini sorun ve bir ödeme sözü alın. Bu çağrı kaydedilir.")["code"] == "tr"
    for b in ("brief-a.md", "brief-b.md"):
        assert L.detect_language((ROOT / "briefs" / b).read_text())["code"] == "en"
    assert L.detect_language("")["code"] == "en"


def test_language_of_prefers_spec():
    assert L.language_of({"language": {"code": "tr"}}, "plain English text")["name"] == "Turkish"
    assert L.language_of({}, "the customer and the payment for you")["code"] == "en"


def test_spec_with_language_validates_and_compiles():
    wf, s = compiled(B, TR)
    assert not [e for e in validate(s, OA) if "language" in str(e)]
    bad = dict(s, language={"code": "tr", "x": 1})
    assert [e for e in validate(bad, OA) if "language" in str(e)]
    assert wf["nodes"]


@pytest.mark.parametrize("spec", [A, B], ids=["spec-a", "spec-b"])
def test_spoken_strings_cover_fixed_sentences(spec):
    wf, _ = compiled(spec)
    texts = [t for _, t in L.spoken_strings(wf)]
    joined = "\n".join(texts)
    assert any(t.startswith("Hello, this is a digital assistant") for t in texts)          # opening
    assert "For your security I need to ask you two questions" in joined                  # identity question
    assert "Goodbye." in joined                                                             # closes
    assert "technical problem" in joined                                                    # service-error lines
    assert "Can I help you with that first?" in texts                                       # holds
    locs = {loc[0] for loc, _ in L.spoken_strings(wf)}
    assert locs == {"tts", "code"}


def test_fake_translator_round_trips_everywhere():
    wf, s = compiled(B, TR)
    rep = L.localise(wf, TR, fake_tr)
    assert rep["localised"] > 10
    for _, t in L.spoken_strings(wf):
        assert t.startswith("[TR] "), t
    assert wf["nodes"]["opening"]["on_enter"]["text"].startswith("[TR] Hello")
    code = wf["nodes"]["close_account_error"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
    assert "T = '[TR] I am sorry" in code
    compile(code, "close", "exec")                                                          # still valid Python
    assert "Speak only Turkish with the customer." in wf["system_prompt"]
    assert "Speak English." not in wf["system_prompt"]
    for n in wf["nodes"].values():
        if n.get("task_message"):
            assert n["task_message"].endswith("Speak only Turkish with the customer.")
    # step prompts quote the translated sentence, not the English one
    assert '"[TR] Can I help you with that first?"' in wf["nodes"]["human_hold_opening"]["task_message"]
    # lint finds no new errors
    en, _ = compiled(B)
    assert set(p for p in lint(wf, s) if not p.startswith("warning:")) <= set(p for p in lint(en, s) if not p.startswith("warning:"))


def test_unicode_translation_keeps_code_valid():
    wf, _ = compiled(A, TR)
    L.localise(wf, TR, lambda texts, lang: [t.replace("Goodbye.", "Hoşça kalın, ğüşiöç 'x'.") for t in texts])
    for n in wf["nodes"].values():
        for a in n.get("pre_actions") or []:
            c = a["function"]["parameters"]["properties"].get("code", {}).get("default")
            if c:
                compile(c, "n", "exec")
    assert "Hoşça kalın" in json.dumps(wf, ensure_ascii=False)


def test_placeholders_and_numbers_checked():
    assert L.check(["Hi {{title}} {x}, 3 days"], ["Merhaba {{title}} {x}, 3 gün"]) == []
    assert L.check(["Hi {{title}}"], ["Merhaba"])
    assert L.check(["Pay within 3 days"], ["Üç gün içinde ödeyin"])
    assert L.check(["a", "b"], ["a"])
    assert L.check(["a"], [""])


@pytest.mark.parametrize("bad", [
    lambda texts, lang: [re.sub(r"\{\{\w+\}\}", "", t) for t in texts],      # drops placeholders
    lambda texts, lang: texts[:-1],                                             # wrong count
    lambda texts, lang: (_ for _ in ()).throw(RuntimeError("model down")),     # raises
])
def test_failure_falls_back_to_english(bad):
    wf, _ = compiled(B, TR)
    en, _ = compiled(B, TR)
    events = []

    class T:
        def event(self, kind, **f):
            events.append(kind)
    rep = L.localise(wf, TR, bad, trace=T())
    assert rep["localised"] == 0 and rep["errors"]
    assert "localise_failed" in events
    assert [t for _, t in L.spoken_strings(wf)] == [t for _, t in L.spoken_strings(en)]


def test_cache_one_call_per_text():
    calls = []

    def tr(texts, lang):
        calls.append(list(texts))
        return fake_tr(texts, lang)
    L.localise(compiled(B, TR)[0], TR, tr)
    L.localise(compiled(B, TR)[0], TR, tr)
    assert len(calls) == 1
    assert len(calls[0]) == len(set(calls[0]))                                   # deduplicated batch


@pytest.mark.parametrize("spec", [A, B], ids=["spec-a", "spec-b"])
def test_english_is_byte_identical(spec):
    wf, _ = compiled(spec)
    before = json.dumps(wf, sort_keys=True)

    def boom(texts, lang):
        raise AssertionError("no translation for English")
    rep = L.localise(wf, L.ENGLISH, boom)
    assert rep["localised"] == 0
    assert json.dumps(wf, sort_keys=True) == before


def test_main_localises_turkish_spec(monkeypatch):
    from builder import main as M
    traced = []

    class Tr:
        def event(self, kind, **f):
            traced.append((kind, f))
    b = M.Build.__new__(M.Build) if hasattr(M, "Build") else None
    if b is None:
        pytest.skip("no Build class")
    b.openapi, b.trace, b.briefs, b._lang = OA, Tr(), [], None
    b.ev = lambda kind, **f: traced.append((kind, f))
    monkeypatch.setattr(M, "translate_factory", lambda trace: fake_tr)
    s = copy.deepcopy(B)
    s["language"] = TR
    wf = b._compile_lint(s, "http://x/v1", "t")
    assert wf["nodes"]["opening"]["on_enter"]["text"].startswith("[TR] ")
    assert ("language", {"code": "tr", "name": "Turkish", "source": "spec"}) in traced
    s2 = copy.deepcopy(B)
    b._lang = None
    wf2 = b._compile_lint(s2, "http://x/v1", "t")
    assert json.dumps(wf2, sort_keys=True) == json.dumps(comp.compile(B, OA, "http://x/v1"), sort_keys=True)
