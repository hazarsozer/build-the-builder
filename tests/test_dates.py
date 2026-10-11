import datetime as dt
from builder.lib import dates as D
CAL = {"weekdays": [0,1,2,3,4], "holidays": ["2026-10-26","2026-11-11","2026-11-26","2026-12-25","2027-01-01"]}
T = dt.date(2026,10,14)  # Wednesday

def test_nth_bd_skips_weekend_and_holiday():
    assert D.nth_bd(T, 12, CAL) == dt.date(2026,11,2)    # 26 Oct holiday skipped
    assert D.nth_bd(T, 7, CAL) == dt.date(2026,10,23)

def test_last_bd_of_week_rolls_on_friday():
    assert D.attempt_date(T, "last_business_day_of_week", dt.date(2026,11,2), CAL) == dt.date(2026,10,16)
    fri = dt.date(2026,10,16)
    assert D.attempt_date(fri, "last_business_day_of_week", dt.date(2026,11,10), CAL) == dt.date(2026,10,23)

def test_attempt_capped_at_deadline():
    assert D.attempt_date(dt.date(2026,10,16), "last_business_day_of_week", dt.date(2026,10,20), CAL) == dt.date(2026,10,20)

def test_weekday_refs():
    fri = dt.date(2026,10,16)
    assert D.weekday_ref(fri, 4, "next") == dt.date(2026,10,23)
    assert D.weekday_ref(fri, 4, "this") == fri
    assert D.weekday_ref(T, 0, "next") == dt.date(2026,10,19)

def test_snap_forward_holiday_and_weekend():
    assert D.snap_forward(dt.date(2026,10,24), CAL) == dt.date(2026,10,27)  # Sat -> Mon is holiday -> Tue

def test_other_deadline_rules():
    assert D.deadline(T, {"rule":"calendar_days","n":10}, CAL) == dt.date(2026,10,24)
    assert D.deadline(T, {"rule":"end_of_month"}, CAL) == dt.date(2026,10,31)
    assert D.deadline(T, {"rule":"end_of_next_month"}, CAL) == dt.date(2026,11,30)
    assert D.deadline(T, {"rule":"nth_business_day","n":12}, CAL) == dt.date(2026,11,2)
    assert D.deadline(T, {"rule":"fixed_weekday_after_n_weeks","weekday":4,"weeks":1}, CAL) == dt.date(2026,10,23)

def test_deadline_snap():
    r = {"rule":"calendar_days","n":10,"snap":"back"}
    assert D.deadline(T, r, CAL) == dt.date(2026,10,23)
    r["snap"] = "forward"
    assert D.deadline(T, r, CAL) == dt.date(2026,10,27)

def test_attempt_kinds():
    dl = dt.date(2026,11,2)
    assert D.attempt_date(T, "today", dl, CAL) == T
    assert D.attempt_date(T, "deadline", dl, CAL) == dl
    assert D.attempt_date(T, "next_business_day", dl, CAL) == dt.date(2026,10,15)

def test_speak_and_last_bd():
    assert D.speak(dt.date(2026,10,16)) == "Friday 16 October 2026 (2026-10-16)"
    assert D.last_bd_of_week(T, CAL) == dt.date(2026,10,16)

def test_custom_weekdays():
    cal = {"weekdays": [0,1,2,3,4,5], "holidays": []}
    assert D.is_bd(dt.date(2026,10,17), cal)


def test_bad_weekdays_raise_instead_of_looping():
    import pytest
    for wd in ([], ["Monday"], [7], [True], None, "0,1"):
        cal = {"weekdays": wd, "holidays": []}
        for call in (lambda: D.next_bd(T, cal), lambda: D.prev_bd(T, cal), lambda: D.nth_bd(T, 3, cal),
                     lambda: D.deadline(T, {"rule": "nth_business_day", "n": 5}, cal)):
            with pytest.raises(ValueError, match="calendar.weekdays"):
                call()


def test_business_day_scan_is_capped():
    import pytest
    blocked = {"weekdays": [0], "holidays": [(T + dt.timedelta(days=i)).isoformat() for i in range(-900, 900)]}
    for call in (lambda: D.next_bd(T, blocked), lambda: D.prev_bd(T, blocked), lambda: D.nth_bd(T, 1, blocked)):
        with pytest.raises(ValueError, match="no business day within"):
            call()


# ---------------------------------------------------------------- A12: a date the customer names, judged in code

import pytest  # noqa: E402

WW = {"next": "following_week", "this": "current_week"}
EARLY = {"rule": "nth_business_day", "n": 12}
LATE = {"rule": "nth_business_day", "n": 7}


def _v(now, said, rule, nbd="next_business_day", after="offer_deadline"):
    today = dt.date.fromisoformat(now[:10])
    d, why = D.resolve_said(said, today, WW)
    assert d is not None, (said, why)
    k, off = D.date_verdict(d, today, D.deadline(today, rule, CAL), CAL, nbd, after)
    return k, (off.isoformat() if off else None)


# the five AFTER-PLAN §3 "now" values: (now, band rule, words, expected verdict, expected date to offer / record)
NOWS = [
    # Fri 16 Oct, late deadline Wed 28 Oct
    ("2026-10-16T11:00", LATE, "Monday the twenty-sixth", "not_business_day", "2026-10-27"),
    ("2026-10-16T11:00", LATE, "the twenty sixth of October", "not_business_day", "2026-10-27"),
    ("2026-10-16T11:00", LATE, "next Friday", "ok", "2026-10-23"),
    ("2026-10-16T11:00", LATE, "this Friday", "ok", "2026-10-16"),
    ("2026-10-16T11:00", LATE, "Saturday", "not_business_day", "2026-10-19"),
    ("2026-10-16T11:00", LATE, "the 30th", "after_deadline", "2026-10-28"),
    ("2026-10-16T11:00", LATE, "the 15th of October", "past", None),
    # Fri 23 Oct, before a weekend and a holiday Monday; late deadline Wed 4 Nov, early Thu 12 Nov
    ("2026-10-23T11:00", LATE, "Monday", "not_business_day", "2026-10-27"),
    ("2026-10-23T11:00", LATE, "next Monday", "not_business_day", "2026-10-27"),
    ("2026-10-23T11:00", LATE, "tomorrow", "not_business_day", "2026-10-27"),
    ("2026-10-23T11:00", LATE, "the eleventh of November", "after_deadline", "2026-11-04"),
    ("2026-10-23T11:00", EARLY, "the eleventh of November", "not_business_day", "2026-11-12"),
    # Wed 14 Oct 19:30, early deadline Mon 2 Nov (PUB-07)
    ("2026-10-14T19:30", EARLY, "Monday the twenty-sixth", "not_business_day", "2026-10-27"),
    ("2026-10-14T19:30", EARLY, "Monday the. 26th.", "not_business_day", "2026-10-27"),
    ("2026-10-14T19:30", EARLY, "the twenty sixth of October", "not_business_day", "2026-10-27"),
    ("2026-10-14T19:30", EARLY, "today", "ok", "2026-10-14"),
    ("2026-10-14T19:30", EARLY, "the 6th of November", "after_deadline", "2026-11-02"),
    ("2026-10-14T19:30", EARLY, "the 12th", "after_deadline", "2026-11-02"),
    ("2026-10-14T19:30", EARLY, "Monday the 12th", "past", None),
    ("2026-10-14T19:30", EARLY, "I can pay 540 on the 23rd", "ok", "2026-10-23"),
    # Fri 30 Oct, month end; early deadline Wed 18 Nov
    ("2026-10-30T11:00", EARLY, "the 31st", "not_business_day", "2026-11-02"),
    ("2026-10-30T11:00", EARLY, "the first", "not_business_day", "2026-11-02"),
    ("2026-10-30T11:00", EARLY, "the 2nd", "ok", "2026-11-02"),
    ("2026-10-30T11:00", EARLY, "November the eleventh", "not_business_day", "2026-11-12"),
    ("2026-10-30T11:00", EARLY, "the 29th", "after_deadline", "2026-11-18"),
    # Wed 23 Dec, holiday cluster; early deadline Tue 12 Jan 2027, late Tue 5 Jan
    ("2026-12-23T11:00", EARLY, "the twenty-fifth", "not_business_day", "2026-12-28"),
    ("2026-12-23T11:00", EARLY, "the 1st of January", "not_business_day", "2027-01-04"),
    ("2026-12-23T11:00", EARLY, "January the 4th", "ok", "2027-01-04"),
    ("2026-12-23T11:00", EARLY, "the 15th of January", "after_deadline", "2027-01-12"),
    ("2026-12-23T11:00", LATE, "the 8th of January", "after_deadline", "2027-01-05"),
    ("2026-12-23T11:00", EARLY, "the 22nd of December", "past", None),
    ("2026-12-23T11:00", EARLY, "tomorrow", "ok", "2026-12-24"),
]


@pytest.mark.parametrize("now,rule,said,kind,offer", NOWS)
def test_a12_verdict_at_the_five_now_values(now, rule, said, kind, offer):
    assert _v(now, said, rule) == (kind, offer)


def test_a12_verdict_follows_the_spec_policies():
    t = dt.date(2026, 10, 14)
    dl = dt.date(2026, 11, 2)
    hol = dt.date(2026, 10, 26)
    assert D.date_verdict(hol, t, dl, CAL, "previous_business_day") == ("not_business_day", dt.date(2026, 10, 23))
    assert D.date_verdict(hol, t, dl, CAL, "reject") == ("not_business_day", None)
    assert D.date_verdict(dt.date(2026, 11, 6), t, dl, CAL, after="reject") == ("after_deadline", None)
    assert D.date_verdict(dt.date(2026, 10, 13), t, dl, CAL) == ("past", None)
    assert D.date_verdict(dt.date(2026, 10, 27), t, dl, CAL) == ("ok", dt.date(2026, 10, 27))
    # a weekend day whose next business day is past the deadline: the deadline
    assert D.date_verdict(dt.date(2026, 10, 24), t, dt.date(2026, 10, 24), CAL) == ("not_business_day", dt.date(2026, 10, 24))


def test_a12_weekday_words_policy():
    occ = {"next": "next_occurrence", "this": "next_occurrence"}
    fri = dt.date(2026, 10, 16)
    assert D.resolve_said("next Friday", fri, WW)[0] == dt.date(2026, 10, 23)
    assert D.resolve_said("next Friday", fri, occ)[0] == dt.date(2026, 10, 23)
    wed = dt.date(2026, 10, 14)
    assert D.resolve_said("next Friday", wed, occ)[0] == dt.date(2026, 10, 16)
    assert D.resolve_said("this Monday", wed, WW)[0] == dt.date(2026, 10, 12)          # current week: already past
    assert D.resolve_said("this Monday", wed, occ)[0] == dt.date(2026, 10, 19)
    assert D.resolve_said("Friday next week", wed, WW)[0] == dt.date(2026, 10, 23)
    assert D.resolve_said("the Friday after next", wed, WW)[0] == dt.date(2026, 10, 30)


def test_a12_vague_and_no_day():
    t = dt.date(2026, 10, 14)
    for s in ("the week after next", "next week", "end of the month", "when my salary comes", "sometime soon"):
        assert D.resolve_said(s, t, WW) == (None, "vague"), s
    assert D.resolve_said("I do not know", t, WW) == (None, "none")
    assert D.resolve_said("the 31st of November", t, WW) == (None, "invalid")
    assert D.resolve_said("", t, WW) == (None, "none")


def test_a12_month_words_and_iso():
    t = dt.date(2026, 10, 14)
    assert D.resolve_said("I may pay on the 26th", t, WW)[0] == dt.date(2026, 10, 26)   # "may" is not a month here
    assert D.resolve_said("the 26th of May", t, WW)[0] == dt.date(2027, 5, 26)
    assert D.resolve_said("October 26", t, WW)[0] == dt.date(2026, 10, 26)
    assert D.resolve_said("2026-10-27", t, WW)[0] == dt.date(2026, 10, 27)
    assert D.resolve_said("on the second", t, WW)[0] == dt.date(2026, 11, 2)
    assert D.resolve_said("first I need to check", t, WW) == (None, "none")
