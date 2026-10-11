"""Event triggers as clauses: one normalisation wherever a spec trigger is embedded in a sentence.

Spec triggers come in many shapes ("The customer asks X.", "asks X", "After X, the customer says Y.",
"Asked whether ..."). Templates add their own subject and full stop, so a trigger is split once here.
When unsure (a first word that is not a known verb), a trigger is left as it is rather than given a subject.
"""
import re

_SUBJECT = re.compile(r"^(the customer|the person|they)\s+", re.I)
# a full stop that may end a sentence: followed by space and a capital letter
_STOP = re.compile(r"\.\s+(?=[A-Z])")
# words a full stop does not end a sentence after ("Dr. Smith", "e.g. Friday")
ABBREV = {"mr", "mrs", "ms", "dr", "st", "no", "e.g", "i.e", "etc", "eg", "ie", "vs", "approx", "jr", "sr", "prof"}
# first words that make a subject-less trigger a predicate ("asks X" -> "the person asks X")
VERBS = {"asks", "says", "mentions", "refuses", "declines", "gives", "offers", "wants", "claims", "requests", "agrees",
         "answers", "confirms", "denies", "disputes", "insists", "names", "repeats", "explains", "tells", "reports",
         "accepts", "rejects", "threatens", "hangs", "becomes", "proposes", "promises", "objects", "complains",
         "still", "cannot", "can", "will", "does", "did", "keeps", "stays", "remains", "sounds", "seems"}


def _sentences(t):
    """(first sentence, rest): split at the first full stop that is not after an abbreviation."""
    for m in _STOP.finditer(t):
        before = re.search(r"([A-Za-z.]+)$", t[:m.start()])
        word = (before.group(1) if before else "").lower().strip(".")
        if word in ABBREV or (len(word) == 1 and word.isalpha()):   # "Dr.", "e.g.", an initial "J."
            continue
        if m.start() and t[m.start() - 1] not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789)\"'":
            continue
        return t[:m.start()], t[m.end():]
    return t, ""


def split_trigger(t):
    """An event trigger -> (subject, predicate, rest), normalised once for embedding in a sentence.

    "The customer asks X. Do not Y." -> ("the customer", "asks X", "Do not Y.").
    No subject: ("", "asks X", "") or ("", "after identity is verified, the customer says X", "").
    The predicate never ends with a full stop; rest is the trigger's further sentences, if any.
    """
    first, rest = _sentences((t or "").strip())
    first = first.strip().rstrip(". ")
    rest = rest.strip()
    m = _SUBJECT.match(first)
    if m:
        return m.group(1).lower(), first[m.end():], rest
    w = (first.split() or [""])[0]
    if "_" not in w and not (len(w) > 1 and w.isupper()):
        first = first[:1].lower() + first[1:]
    return "", first, rest


def verb_led(pred):
    """True only for a known verb as first word ("asks", "still"); "Fees are disputed" is not verb-led."""
    return (pred.split() or [""])[0].lower() in VERBS


def bare_trigger(t):
    """A trigger without its subject and full stop ("The customer asks X." -> "asks X")."""
    return split_trigger(t)[1]


_LEAD = re.compile(r"^((?:after|before|once|when) [^,]+), ((?:the customer|the person|they) .+)$")


def subj(who, trigger):
    """A trigger as a clause for "If ..."/"when ...": its own subject, else `who` when it starts with a known verb,
    else the trigger alone ("asks X" -> "the person asks X"). A leading time clause moves to the end:
    "After X, the customer says Y." -> "the customer says Y (after X)"."""
    s, p, _ = split_trigger(trigger)
    if s:
        return s + " " + p
    m = _LEAD.match(p)
    if m:
        return "%s (%s)" % (m.group(2), m.group(1))
    return (who + " " + p) if verb_led(p) else p


def sentence(who, trigger):
    """subj() as a sentence start, for fields that stand alone (matches_intent descriptions): capitalised."""
    c = subj(who, trigger)
    return c[:1].upper() + c[1:]


def trigger_rest(t):
    """The trigger's sentences after the first, with a leading space ("" when there are none)."""
    r = split_trigger(t)[2]
    return (" " + r) if r else ""
