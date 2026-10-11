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


# ---------------------------------------------------------------- runtime-built text: dates and the read-back

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
# The multi-product read-back's fixed fragments (builder/multi.py _readback), as templates over its runtime parts.
READBACK = {"item": "{amount} for your {product} on {date}",
            "capped": "{item}, because that is the latest date I can accept for it",
            "join": "{rest}; and {last}",
            "head": "To confirm, you will pay {items}."}


def _block_end(code, start):
    """Index just after the top-level definition starting at `start` (the next non-indented, non-blank line)."""
    k = code.index("\n", start) + 1
    while k < len(code):
        e = code.find("\n", k)
        line = code[k:] if e < 0 else code[k:e]
        if line.strip() and not line[0].isspace():
            return k
        if e < 0:
            return len(code)
        k = e + 1
    return len(code)


def _inject_after(code, header, block):
    """Insert `block` right after the top-level `header` definition (a later def of the same name wins)."""
    at = code.find(header)
    if at < 0 or (at and code[at - 1] != "\n"):
        return code
    k = _block_end(code, at)
    if k >= len(code) and not code.endswith("\n"):
        code += "\n"
        k = len(code)
    return code[:k] + block + "\n\n" + code[k:]


def _py(obj):
    from builder.nodes import py_json
    return py_json(obj)


def date_override(weekdays, months):
    """Code that redefines say_date and speak with the brief language's names: '<weekday>, <day> <month> <year>'
    (no English ordinal suffixes; speak keeps the ISO date in brackets, as before)."""
    return ("SAY_WD = %s\nSAY_MON = %s\n\n\n"
            "def say_date(d):\n"
            "    return SAY_WD[d.weekday()] + ', ' + str(d.day) + ' ' + SAY_MON[d.month - 1] + ' ' + str(d.year)\n\n\n"
            "def speak(d):\n"
            "    return say_date(d) + ' (' + d.isoformat() + ')'\n" % (_py(list(weekdays)), _py(list(months))))


def readback_override(t):
    """Code that redefines multi's _readback over translated templates (placeholders filled with str.replace)."""
    return ("RB_T = %s\n\n\n"
            "def _readback(st):\n"
            "    parts = []\n"
            "    for q in st['order']:\n"
            "        P = st['p'][q]\n"
            "        if P['status'] != 'agreed':\n"
            "            continue\n"
            "        s = RB_T['item'].replace('{amount}', fmt(P['amount'])).replace('{date}', _say(P['date']))"
            ".replace('{product}', str(P['words']))\n"
            "        for xf, xv in sorted((P.get('extra') or dict()).items()):\n"
            "            if xv not in (None, ''):\n"
            "                s += ', ' + xf.replace('_', ' ') + ' ' + str(xv).replace('_', ' ')\n"
            "        if P.get('capped'):\n"
            "            s = RB_T['capped'].replace('{item}', s)\n"
            "        parts.append(s)\n"
            "    if len(parts) > 1:\n"
            "        body = RB_T['join'].replace('{rest}', '; '.join(parts[:-1])).replace('{last}', parts[-1])\n"
            "    else:\n"
            "        body = parts[0]\n"
            "    return RB_T['head'].replace('{items}', body) + ' ' + CFG['confirm']\n" % _py(t))


def _map_code(wf, fn):
    for n in (wf.get("nodes") or {}).values():
        for a in n.get("pre_actions") or []:
            props = ((a.get("function") or {}).get("parameters") or {}).get("properties") or {}
            c = (props.get("code") or {}).get("default")
            if isinstance(c, str):
                props["code"]["default"] = fn(c)


def _wants(wf, header):
    found = []

    def see(c):
        if ("\n" + header) in ("\n" + c):
            found.append(1)
        return c
    _map_code(wf, see)
    return bool(found)


def apply_dates(wf, weekdays, months):
    block = date_override(weekdays, months)
    _map_code(wf, lambda c: _inject_after(c, "def speak(d):", block))


def apply_readback(wf, t):
    block = readback_override(t)
    _map_code(wf, lambda c: _inject_after(c, "def _readback(st):", block))


def check_names(sources, translated):
    """Weekday and month names: non-empty, short, no digits, braces, quotes or newlines."""
    errs = check(sources, translated)
    if errs:
        return errs
    return ["name %d is not a plain name: %r" % (i, t) for i, t in enumerate(translated)
            if len(t) > 30 or re.search(r"[\d{}'\"\\\n]", t)]


# ---------------------------------------------------------------- the pass

def localise(wf, lang, translate, trace=None):
    """Localise `wf` in place for `lang`. translate(list_of_str, lang) -> list_of_str (ONE batched model call for
    every part not in the cache). Three parts, each validated and applied on its own: the fixed spoken strings,
    the weekday and month names spoken in dates, the read-back fragments. A part that fails stays English (traced);
    the language rule is always added. Returns a report dict."""
    ev = (trace.event if trace is not None else (lambda *a, **k: None))
    if is_english(lang):
        return {"localised": 0, "skipped": "english"}
    items = spoken_strings(wf)
    groups = {"spoken": (list(dict.fromkeys(t for _, t in items)), check)}
    if _wants(wf, "def speak(d):"):
        groups["dates"] = (WEEKDAYS + MONTHS, check_names)
    if _wants(wf, "def _readback(st):"):
        groups["readback"] = (list(READBACK.values()), check)
    allt = list(dict.fromkeys(t for g in groups.values() for t in g[0]))
    todo = [t for t in allt if _key(lang, t) not in _CACHE]
    report = {"language": lang["code"], "strings": len(allt), "from_cache": len(allt) - len(todo), "parts": {}}
    got, call_err = {}, None
    if todo:
        try:
            out = translate(todo, lang)
            if not isinstance(out, list) or len(out) != len(todo):
                raise ValueError("expected %d strings, got %s" % (
                    len(todo), len(out) if isinstance(out, list) else type(out).__name__))
            got = dict(zip(todo, out))
        except Exception as e:  # noqa: BLE001 - never crash the build
            call_err = "%s: %s" % (type(e).__name__, str(e)[:300])
    errors = []
    for name, (src, checker) in groups.items():
        if call_err:
            errs = [call_err]
        else:
            tr = [_CACHE.get(_key(lang, t), got.get(t)) for t in src]
            errs = checker(src, tr)
        if errs:
            report["parts"][name] = "english"
            errors += ["%s: %s" % (name, e) for e in errs[:5]]
            continue
        for s0, t in zip(src, tr):
            _CACHE[_key(lang, s0)] = t
        report["parts"][name] = "localised"
        if name == "spoken":
            _apply(wf, items, [_CACHE[_key(lang, t)] for _, t in items])
        elif name == "dates":
            apply_dates(wf, tr[:7], tr[7:])
        else:
            apply_readback(wf, dict(zip(READBACK, tr)))
    report["localised"] = len(groups["spoken"][0]) if report["parts"].get("spoken") == "localised" else 0
    if errors:
        report["errors"] = errors[:8]
        ev("localise_failed", language=lang["code"], strings=len(todo), errors=errors[:8])
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
- A single weekday or month name: only its {name} name.
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
