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


def is_open(now: dt.datetime | None = None) -> bool:
    """True if `now` (any timezone; converted to campus time) is in-window."""
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(CAMPUS_TZ)
    if WEEKDAYS_ONLY and now.weekday() >= 5:      # 5=Sat, 6=Sun
        return False
    return START_HOUR <= now.hour < END_HOUR


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
    print(f"\n{len(cases) - failures}/{len(cases)} cases passed")
    return 1 if failures else 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--check" in argv:
        return _self_test()

    open_now = is_open()
    print(describe())
    # Consumed by the workflow's step-level `if:` conditions.
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"collect={'true' if open_now else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
