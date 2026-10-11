"""Event triggers as clauses: one normalisation wherever a spec trigger is embedded in a sentence.

Spec triggers come in many shapes ("The customer asks X.", "asks X", "After X, the customer says Y.",
"Asked whether ..."). Templates add their own subject and full stop, so a trigger is split once here.
"""
import re

_SUBJECT = re.compile(r"^(the customer|the person|they)\s+", re.I)
# a full stop between two sentences ("... cannot answer. Do not invent ...")
_SENTENCE = re.compile(r"(?<=[A-Za-z0-9)\"'])\.\s+(?=[A-Z])")


def split_trigger(t):
    """An event trigger -> (subject, predicate, rest), normalised once for embedding in a sentence.

    "The customer asks X. Do not Y." -> ("the customer", "asks X", "Do not Y.").
    No subject: ("", "asks X", "") or ("", "after identity is verified, the customer says X", "").
    The predicate never ends with a full stop; rest is the trigger's further sentences, if any.
    """
    parts = _SENTENCE.split((t or "").strip(), maxsplit=1)
    first = parts[0].strip().rstrip(". ")
    rest = parts[1].strip() if len(parts) > 1 else ""
    m = _SUBJECT.match(first)
    if m:
        return m.group(1).lower(), first[m.end():], rest
    w = (first.split() or [""])[0]
    if "_" not in w and not (len(w) > 1 and w.isupper()):
        first = first[:1].lower() + first[1:]
    return "", first, rest


def verb_led(pred):
    w = (pred.split() or [""])[0].lower()
    return (w.endswith("s") or w == "still") and w not in ("this", "is")


def bare_trigger(t):
    """A trigger without its subject and full stop ("The customer asks X." -> "asks X")."""
    return split_trigger(t)[1]


_LEAD = re.compile(r"^((?:after|before|once|when) [^,]+), ((?:the customer|the person|they) .+)$")


def subj(who, trigger):
    """A trigger as a clause for "If ..."/"when ...": its own subject, else `who` when it starts with a verb,
    else the trigger alone ("asks X" -> "the person asks X"). A leading time clause moves to the end:
    "After X, the customer says Y." -> "the customer says Y (after X)"."""
    s, p, _ = split_trigger(trigger)
    if s:
        return s + " " + p
    m = _LEAD.match(p)
    if m:
        return "%s (%s)" % (m.group(2), m.group(1))
    return (who + " " + p) if verb_led(p) else p


def trigger_rest(t):
    """The trigger's sentences after the first, with a leading space ("" when there are none)."""
    r = split_trigger(t)[2]
    return (" " + r) if r else ""
