"""Localisation pass: the compiled workflow speaks the brief's language.

The compiler writes its fixed spoken sentences (opening template, confirm question, holds, closes, technical-problem
lines) in English. When the brief is in another language, `localise(wf, lang, translate)` collects every fixed string
spoken to the customer, translates them in ONE batched call, validates the result (same count, non-empty, every
placeholder and number kept) and writes them back. Any failure keeps the English workflow and is reported, never raised.
It also tells the model, in the system prompt and every step prompt, to speak only that language.

Language: spec["language"] (filled by extraction), else `detect_language(brief_text)`, else English.
"""
import ast
import asyncio
import hashlib
import json
import re

ENGLISH = {"code": "en", "name": "English"}

# Letters that mark a language, and a few frequent stopwords; generic and cheap. Unknown -> English.
_MARKERS = {
    "tr": ("Turkish", "çğışİöüÇĞŞÖÜ", {"ve", "bir", "bu", "için", "ile", "da", "de", "olarak", "müşteri", "ödeme"}),
    "de": ("German", "äöüßÄÖÜ", {"und", "der", "die", "das", "nicht", "mit", "für", "ist", "zahlung", "kunde"}),
    "es": ("Spanish", "ñáéíóúÑ¿¡", {"el", "la", "los", "las", "que", "para", "con", "una", "pago", "cliente"}),
    "fr": ("French", "àâçéèêëîïôûùœ", {"le", "la", "les", "des", "et", "pour", "avec", "une", "paiement", "client"}),
}
_EN_STOP = {"the", "and", "of", "to", "is", "for", "with", "you", "a", "customer", "payment"}

_PH = re.compile(r"\{\{\s*[A-Za-z_][A-Za-z0-9_.]*\s*\}\}|\{[A-Za-z_][A-Za-z0-9_.]*\}")
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_CODE_LIT = re.compile(r"^(T = |vars\['farewell'\] = )('(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\")$", re.M)

_CACHE = {}


def detect_language(text):
    """{code, name} from the brief text: marker letters and stopwords; English when nothing else wins."""
    text = text or ""
    words = re.findall(r"[^\W\d_]+", text.lower())
    if not words:
        return dict(ENGLISH)
    n = len(words)
    best, score = None, 0.0
    for code, (name, letters, stop) in _MARKERS.items():
        letter_share = sum(text.count(c) for c in letters) / max(1, len(text))
        stop_share = sum(1 for w in words if w in stop) / n
        s = letter_share * 10 + stop_share
        if s > score:
            best, score = code, s
    en = sum(1 for w in words if w in _EN_STOP) / n
    if best and score > en and score > 0.04:
        return {"code": best, "name": _MARKERS[best][0]}
    return dict(ENGLISH)


def language_of(spec, brief_text=""):
    """The language the call is spoken in: the spec's language, else detected from the brief."""
    lang = (spec or {}).get("language")
    if isinstance(lang, dict) and lang.get("code"):
        code = str(lang["code"]).lower()[:2]
        name = lang.get("name") or (_MARKERS.get(code, (None,))[0]) or ("English" if code == "en" else code)
        return {"code": code, "name": name}
    return detect_language(brief_text)


def is_english(lang):
    return not lang or str(lang.get("code", "en")).lower().startswith("en")


# ---------------------------------------------------------------- collect / apply

def _is_spoken(text):
    """Worth translating: has words outside placeholders."""
    return bool(re.search(r"[^\W\d_]{2,}", _PH.sub(" ", text or "")))


def _code_of(node):
    out = []
    for i, a in enumerate(node.get("pre_actions") or []):
        props = ((a.get("function") or {}).get("parameters") or {}).get("properties") or {}
        code = (props.get("code") or {}).get("default")
        if isinstance(code, str):
            out.append((i, code))
    return out


def spoken_strings(wf):
    """[(locator, text)] of every fixed string spoken to the customer: tts on_enter texts, end_call messages,
    close farewells (T = ...) and the default farewell (vars['farewell'] = ...) in code nodes."""
    out = []
    for name, n in (wf.get("nodes") or {}).items():
        oe = n.get("on_enter") or {}
        if oe.get("type") == "tts" and _is_spoken(oe.get("text")):
            out.append((("tts", name), oe["text"]))
        msg = ((((n.get("terminal") or {}).get("function") or {}).get("parameters") or {}).get("properties") or {}) \
            .get("spoken_message", {}).get("default")
        if isinstance(msg, str) and _is_spoken(msg):
            out.append((("end", name), msg))
        for i, code in _code_of(n):
            for m in _CODE_LIT.finditer(code):
                try:
                    lit = ast.literal_eval(m.group(2))
                except (ValueError, SyntaxError):
                    continue
                if isinstance(lit, str) and _is_spoken(lit):
                    out.append((("code", name, i, m.group(1)), lit))
    return out


def _set_code(node, i, prefix, old, new):
    props = node["pre_actions"][i]["function"]["parameters"]["properties"]
    code = props["code"]["default"]
    lines = code.split("\n")
    for k, line in enumerate(lines):
        if line.startswith(prefix):
            try:
                if ast.literal_eval(line[len(prefix):]) == old:
                    lines[k] = prefix + repr(new)
            except (ValueError, SyntaxError):
                pass
    props["code"]["default"] = "\n".join(lines)


def _apply(wf, items, translated):
    mapping = {}
    for (loc, old), new in zip(items, translated):
        n = wf["nodes"][loc[1]]
        if loc[0] == "tts":
            n["on_enter"]["text"] = new
        elif loc[0] == "end":
            n["terminal"]["function"]["parameters"]["properties"]["spoken_message"]["default"] = new
        else:
            _set_code(n, loc[2], loc[3], old, new)
        if old != new:
            mapping[old] = new
    # step prompts quote what was said ("You asked \"...\""): keep the quotes in step with the spoken text
    olds = sorted(mapping, key=len, reverse=True)
    for n in wf["nodes"].values():
        tm = n.get("task_message")
        if isinstance(tm, str):
            for o in olds:
                tm = tm.replace(o, mapping[o])
            n["task_message"] = tm


def add_language_rule(wf, lang):
    line = "Speak only %s with the customer." % lang["name"]
    sp = wf.get("system_prompt")
    if isinstance(sp, str):
        sp = sp.replace("Speak English.", line) if "Speak English." in sp else sp.rstrip() + "\n" + line
        wf["system_prompt"] = sp
    for n in (wf.get("nodes") or {}).values():
        if isinstance(n.get("task_message"), str) and line not in n["task_message"]:
            n["task_message"] = n["task_message"].rstrip() + "\n" + line


# ---------------------------------------------------------------- validate

def check(sources, translated):
    """[] when the translation is usable, else the problems."""
    if not isinstance(translated, list) or len(translated) != len(sources):
        return ["expected %d strings, got %s" % (len(sources), len(translated) if isinstance(translated, list) else type(translated).__name__)]
    errs = []
    for i, (s, t) in enumerate(zip(sources, translated)):
        if not isinstance(t, str) or not t.strip():
            errs.append("string %d is empty" % i)
            continue
        if sorted(_PH.findall(s)) != sorted(_PH.findall(t)):
            errs.append("string %d: placeholders changed %s -> %s" % (i, sorted(set(_PH.findall(s))), sorted(set(_PH.findall(t)))))
        if sorted(_NUM.findall(_PH.sub(" ", s))) != sorted(_NUM.findall(_PH.sub(" ", t))):
            errs.append("string %d: numbers changed" % i)
    return errs


def _key(lang, text):
    return hashlib.sha256(("%s\0%s" % (lang["code"], text)).encode()).hexdigest()


# ---------------------------------------------------------------- the pass

def localise(wf, lang, translate, trace=None):
    """Localise `wf` in place for `lang`. translate(list_of_str, lang) -> list_of_str (one batched model call).
    Returns a report dict; on any failure the spoken strings stay English (the language rule is still added)."""
    ev = (trace.event if trace is not None else (lambda *a, **k: None))
    if is_english(lang):
        return {"localised": 0, "skipped": "english"}
    items = spoken_strings(wf)
    texts = list(dict.fromkeys(t for _, t in items))
    todo = [t for t in texts if _key(lang, t) not in _CACHE]
    report = {"language": lang["code"], "strings": len(texts), "from_cache": len(texts) - len(todo)}
    ok = True
    if todo:
        try:
            got = translate(todo, lang)
            errs = check(todo, got)
        except Exception as e:  # noqa: BLE001 - never crash the build
            got, errs = None, ["%s: %s" % (type(e).__name__, str(e)[:300])]
        if errs:
            ok = False
            report["errors"] = errs[:5]
            ev("localise_failed", language=lang["code"], strings=len(todo), errors=errs[:5])
        else:
            for s, t in zip(todo, got):
                _CACHE[_key(lang, s)] = t
    if ok:
        _apply(wf, items, [_CACHE[_key(lang, t)] for _, t in items])
        report["localised"] = len(texts)
    else:
        report["localised"] = 0
    add_language_rule(wf, lang)
    ev("localised", **report)
    return report


# ---------------------------------------------------------------- the model call

PROMPT = """Translate the customer-facing phone sentences in strings.json into {name} ({code}).
strings.json is a JSON list of strings. Write out.json: a JSON list with exactly the same number of strings, in the
same order, each the translation of the string at the same index.
Rules:
- Keep every placeholder exactly as written: {{{{name}}}} and {{name}} (do not translate or change what is inside braces).
- Keep every number, date template and identifier exactly.
- A string already in {name}: copy it unchanged. A quoted sentence inside a string that is already in {name}: keep it.
- Natural, polite spoken {name}, as a bank's phone agent says it. Keep the meaning; no additions.
Write only out.json."""


def model_translate(trace=None, client_factory=None, timeout_s=180):
    """A translate(texts, lang) that makes one Agent SDK session call (builder.sdk, the extraction client)."""
    from builder import sdk

    def translate(texts, lang):
        work = sdk.make_workdir([], {}, "l10n", with_schema=False)
        (work / "strings.json").write_text(json.dumps(texts, ensure_ascii=False, indent=1))

        async def go():
            async with sdk.Session(work, "localise", trace or sdk.NullTrace(), client_factory=client_factory) as sess:
                await sess.ask(PROMPT.format(name=lang["name"], code=lang["code"]), timeout_s=timeout_s)
        asyncio.run(go())
        data, err = sdk.read_json(work / "out.json")
        if err:
            raise RuntimeError(err)
        return data

    return translate
