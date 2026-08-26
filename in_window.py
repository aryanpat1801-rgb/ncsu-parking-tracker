"""Is the collection window open on campus right now?

GitHub Actions cron is always UTC and has no daylight-saving support, so a
hard-coded UTC window silently drifts by an hour twice a year. Instead the
workflow schedules the *union* of the EDT and EST windows and calls this to
decide whether to actually collect.

zoneinfo carries the real DST rules, so this stays correct through every
transition -- including 2026-11-01, when EDT ends -- with no edit.

    python in_window.py           print the decision; also writes the
                                  'collect' output when run under Actions
    python in_window.py --check   run the built-in self-test
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

CAMPUS_TZ = ZoneInfo("America/New_York")
START_HOUR = 7          # 7am campus time, inclusive
END_HOUR = 17           # 5pm campus time, exclusive
WEEKDAYS_ONLY = True


def window_bounds(day: dt.date):
    """(opens, closes) for one campus calendar date, or None if it never
    opens that day. Both are aware campus-local datetimes, so the offset is
    whatever really applied on that date -- EDT or EST.

    7am and 5pm are safe wall-clock times to attach a zone to: the DST flips
    happen at 2am, so neither hour is ever ambiguous or non-existent.
    """
    if WEEKDAYS_ONLY and day.weekday() >= 5:      # 5=Sat, 6=Sun
        return None
    return (dt.datetime.combine(day, dt.time(START_HOUR), tzinfo=CAMPUS_TZ),
            dt.datetime.combine(day, dt.time(END_HOUR), tzinfo=CAMPUS_TZ))


def window_close(now: dt.datetime | None = None):
    """When the window that is open *right now* closes, or None if it is
    shut. The long-running cloud collector polls until this comes back None.
    """
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(CAMPUS_TZ)
    bounds = window_bounds(now.date())
    if bounds and bounds[0] <= now < bounds[1]:
        return bounds[1]
    return None


def next_open(now: dt.datetime | None = None):
    """The next instant the window opens. None only if nothing opens within
    the next week, which cannot happen while weekdays are in the window.
    """
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(CAMPUS_TZ)
    for ahead in range(8):
        bounds = window_bounds((now + dt.timedelta(days=ahead)).date())
        if bounds and now < bounds[0]:
            return bounds[0]
    return None


def is_open(now: dt.datetime | None = None) -> bool:
    """True if `now` (any timezone; converted to campus time) is in-window."""
    return window_close(now) is not None


def describe(now: dt.datetime | None = None) -> str:
    local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(CAMPUS_TZ)
    verdict = "collect" if is_open(now) else "skip"
    return (f"{local:%Y-%m-%d %H:%M %Z} on campus "
            f"({local:%A}) -> {verdict}")


def _self_test() -> int:
    """Pin the behaviour around the DST transitions and the window edges."""
    utc = dt.timezone.utc
    cases = [
        # (UTC instant, expected, why)
        (dt.datetime(2026, 8, 24, 11, 0, tzinfo=utc), True,
         "EDT: 11:00 UTC is 07:00 ET Monday, window opens"),
        (dt.datetime(2026, 8, 24, 10, 50, tzinfo=utc), False,
         "EDT: 10:50 UTC is 06:50 ET, before the window"),
        (dt.datetime(2026, 8, 24, 20, 50, tzinfo=utc), True,
         "EDT: 20:50 UTC is 16:50 ET, last slot"),
        (dt.datetime(2026, 8, 24, 21, 0, tzinfo=utc), False,
         "EDT: 21:00 UTC is 17:00 ET, window closed"),
        # 2026-11-01 is the first Sunday in November: EDT -> EST.
        (dt.datetime(2026, 10, 30, 11, 0, tzinfo=utc), True,
         "last Friday on EDT: 11:00 UTC is 07:00 ET"),
        (dt.datetime(2026, 11, 2, 11, 0, tzinfo=utc), False,
         "first Monday on EST: 11:00 UTC is 06:00 ET, too early"),
        (dt.datetime(2026, 11, 2, 12, 0, tzinfo=utc), True,
         "first Monday on EST: 12:00 UTC is 07:00 ET, window opens"),
        (dt.datetime(2026, 11, 2, 21, 50, tzinfo=utc), True,
         "EST: 21:50 UTC is 16:50 ET, last slot"),
        (dt.datetime(2026, 11, 2, 22, 0, tzinfo=utc), False,
         "EST: 22:00 UTC is 17:00 ET, window closed"),
        # Spring forward 2027-03-14.
        (dt.datetime(2027, 3, 12, 12, 0, tzinfo=utc), True,
         "still EST before the spring transition: 07:00 ET"),
        (dt.datetime(2027, 3, 16, 11, 0, tzinfo=utc), True,
         "back on EDT after it: 07:00 ET"),
        (dt.datetime(2027, 3, 16, 21, 30, tzinfo=utc), False,
         "back on EDT: 17:30 ET, closed"),
        # Weekends are off entirely.
        (dt.datetime(2026, 8, 22, 15, 0, tzinfo=utc), False, "Saturday"),
        (dt.datetime(2026, 8, 23, 15, 0, tzinfo=utc), False, "Sunday"),
    ]
    failures = 0
    for when, expected, why in cases:
        got = is_open(when)
        ok = got == expected
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {describe(when):<52}  {why}")

    # The long-running collector polls until window_close() goes None and
    # idles until next_open(), so both need pinning against the flips too.
    def fmt(when):
        return None if when is None else f"{when:%Y-%m-%d %H:%M %Z}"

    edges = [
        # (UTC instant, expected window_close(), expected next_open(), why)
        (dt.datetime(2026, 8, 24, 15, 0, tzinfo=utc), "2026-08-24 17:00 EDT",
         "2026-08-25 07:00 EDT", "mid-window Monday: shuts 5pm today"),
        (dt.datetime(2026, 8, 24, 21, 0, tzinfo=utc), None,
         "2026-08-25 07:00 EDT", "just shut: next open is Tuesday 7am"),
        (dt.datetime(2026, 8, 22, 15, 0, tzinfo=utc), None,
         "2026-08-24 07:00 EDT", "Saturday: skips the weekend to Monday"),
        (dt.datetime(2026, 10, 30, 22, 0, tzinfo=utc), None,
         "2026-11-02 07:00 EST", "last Friday on EDT -> Monday, now on EST"),
    ]
    for when, want_close, want_open, why in edges:
        got_close, got_open = fmt(window_close(when)), fmt(next_open(when))
        ok = got_close == want_close and got_open == want_open
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  close={got_close!s:<21} "
              f"open={got_open!s:<21}  {why}")

    total = len(cases) + len(edges)
    print(f"\n{total - failures}/{total} cases passed")
    return 1 if failures else 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--check" in argv:
        return _self_test()

    # A manual "Run workflow" click means "collect a sample now" -- otherwise
    # testing the workflow outside 7am-5pm on a weekday looks like a pass but
    # silently collects nothing.
    forced = os.environ.get("FORCE", "").lower() == "true"
    open_now = forced or is_open()
    print(describe() + ("  [FORCED by manual run]" if forced else ""))
    # Consumed by the workflow's step-level `if:` conditions.
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"collect={'true' if open_now else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
