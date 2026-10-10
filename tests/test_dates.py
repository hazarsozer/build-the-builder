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
