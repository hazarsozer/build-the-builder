"""Deterministic business-calendar helpers.

Embedded as source text into Freya code nodes (sandbox: json, re, datetime only).
Keep imports to `datetime` and never write the two-character brace sequences
that Freya templating would interpret.
"""
import datetime

WD = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
MON = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
       'September', 'October', 'November', 'December']


def is_bd(d, cal):
    return d.weekday() in cal["weekdays"] and d.isoformat() not in cal["holidays"]


def next_bd(d, cal):
    d = d + datetime.timedelta(days=1)
    while not is_bd(d, cal):
        d = d + datetime.timedelta(days=1)
    return d


def prev_bd(d, cal):
    d = d - datetime.timedelta(days=1)
    while not is_bd(d, cal):
        d = d - datetime.timedelta(days=1)
    return d


def nth_bd(start, n, cal):
    d = start
    c = 0
    while c < n:
        d = d + datetime.timedelta(days=1)
        if is_bd(d, cal):
            c += 1
    return d


def last_bd_of_week(d, cal):
    """Last business day of the Monday-based week containing d."""
    monday = d - datetime.timedelta(days=d.weekday())
    f = monday + datetime.timedelta(days=6)
    k = 0
    while not is_bd(f, cal) and k < 6:
        f = f - datetime.timedelta(days=1)
        k += 1
    return f


def snap_forward(d, cal):
    if is_bd(d, cal):
        return d
    return next_bd(d, cal)


def snap_back(d, cal):
    if is_bd(d, cal):
        return d
    return prev_bd(d, cal)


def weekday_ref(today, weekday, which):
    """The given weekday (0=Monday) in this week, or in next week."""
    monday = today - datetime.timedelta(days=today.weekday())
    d = monday + datetime.timedelta(days=weekday)
    if which == "next":
        d = d + datetime.timedelta(days=7)
    return d


def deadline(today, rule, cal):
    kind = rule["rule"]
    if kind == "nth_business_day":
        d = nth_bd(today, rule["n"], cal)
    elif kind == "calendar_days":
        d = today + datetime.timedelta(days=rule["n"])
    elif kind == "end_of_month":
        first_next = datetime.date(today.year + today.month // 12, today.month % 12 + 1, 1)
        d = first_next - datetime.timedelta(days=1)
    elif kind == "end_of_next_month":
        m = today.month % 12 + 1
        y = today.year + today.month // 12
        first_next = datetime.date(y + m // 12, m % 12 + 1, 1)
        d = first_next - datetime.timedelta(days=1)
    elif kind == "fixed_weekday_after_n_weeks":
        d = weekday_ref(today, rule["weekday"], "this") + datetime.timedelta(days=7 * rule["weeks"])
    else:
        raise ValueError("unknown deadline rule: " + str(kind))
    snap = rule.get("snap")
    if snap == "back":
        d = snap_back(d, cal)
    elif snap == "forward":
        d = snap_forward(d, cal)
    return d


def attempt_date(today, kind, deadline_d, cal):
    if kind == "today":
        d = today
    elif kind == "last_business_day_of_week":
        d = last_bd_of_week(today, cal)
        if d <= today:
            d = last_bd_of_week(today + datetime.timedelta(days=7), cal)
    elif kind == "deadline":
        d = deadline_d
    elif kind == "next_business_day":
        d = next_bd(today, cal)
    else:
        raise ValueError("unknown attempt kind: " + str(kind))
    if d > deadline_d:
        d = deadline_d
    return d


def speak(d):
    return WD[d.weekday()] + ' ' + str(d.day) + ' ' + MON[d.month - 1] + ' ' + str(d.year) + ' (' + d.isoformat() + ')'
