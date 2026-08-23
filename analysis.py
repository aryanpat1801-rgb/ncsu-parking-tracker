"""Time-of-day analysis for the parking log.

The collector samples on a rough 5-minute cadence, but gaps happen (laptop
asleep, a CI runner firing late). So nothing here assumes evenly spaced data:
every series is resampled onto a fixed time-of-day grid, with empty bins left
as NaN and ignored by the averaging.
"""
from __future__ import annotations

import datetime as dt
import warnings

import numpy as np

import parking_core as core

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"]


def minutes_of_day(when: dt.datetime) -> float:
    return when.hour * 60 + when.minute + when.second / 60.0


def make_grid(bin_minutes: int) -> np.ndarray:
    """Left edge of each time-of-day bin, in minutes since local midnight."""
    return np.arange(0, 24 * 60, bin_minutes, dtype=float)


def bin_day(samples: list, bin_minutes: int) -> np.ndarray:
    """Average the samples of a single day onto the time-of-day grid.

    `samples` is a list of (local datetime, free). Bins with no sample are NaN
    so that a laptop-asleep gap reads as missing rather than as zero free
    spaces -- which would badly skew any average taken over it.
    """
    grid = make_grid(bin_minutes)
    out = np.full(grid.size, np.nan)
    if not samples:
        return out
    mins = np.array([minutes_of_day(t) for t, _ in samples])
    vals = np.array([float(v) for _, v in samples])
    idx = np.clip((mins // bin_minutes).astype(int), 0, grid.size - 1)
    for i in range(grid.size):
        hit = vals[idx == i]
        if hit.size:
            out[i] = hit.mean()
    return out


def group_by_date(conn, location: str) -> dict:
    """All samples for a lot, bucketed by local calendar date."""
    buckets: dict = {}
    for when, free, _total in core.series(conn, location):
        buckets.setdefault(when.date(), []).append((when, free))
    return buckets


def single_day(conn, location: str, day: dt.date, bin_minutes: int = 5):
    """Returns (grid, binned values) for one specific calendar date."""
    samples = group_by_date(conn, location).get(day, [])
    return make_grid(bin_minutes), bin_day(samples, bin_minutes)


def weekday_profile(conn, location: str, weekday: int, bin_minutes: int = 15):
    """Average time-of-day curve for one weekday across every week on record.

    weekday is 0=Monday .. 6=Sunday, matching date.weekday().

    Returns (grid, per_date, mean, std, coverage) where per_date maps each
    matching date to its own binned curve, and coverage counts how many dates
    contributed to each bin.
    """
    grid = make_grid(bin_minutes)
    per_date = {}
    for day, samples in sorted(group_by_date(conn, location).items()):
        if day.weekday() == weekday:
            per_date[day] = bin_day(samples, bin_minutes)

    if not per_date:
        empty = np.full(grid.size, np.nan)
        return grid, {}, empty, empty, np.zeros(grid.size, dtype=int)

    stack = np.vstack(list(per_date.values()))
    coverage = np.sum(~np.isnan(stack), axis=0)
    with warnings.catch_warnings():
        # All-NaN columns are expected -- nobody logged 4am in the first week.
        # np.where still evaluates both branches, so nanmean/nanstd see those
        # empty slices and warn; NaN is exactly the answer we want there.
        # (np.errstate does not cover this: it is a RuntimeWarning, not an fp
        # error state.)
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.where(coverage > 0, np.nanmean(stack, axis=0), np.nan)
        std = np.where(coverage > 1, np.nanstd(stack, axis=0), np.nan)
    return grid, per_date, mean, std, coverage


def format_clock(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60 % 24:02d}:{m % 60:02d}"


def best_windows(grid: np.ndarray, mean: np.ndarray, top: int = 3,
                 start_hour: int = 7, end_hour: int = 20) -> list:
    """The times of day with the most free spaces, for a quick text summary."""
    mask = (grid >= start_hour * 60) & (grid < end_hour * 60) & ~np.isnan(mean)
    if not mask.any():
        return []
    idx = np.where(mask)[0]
    order = idx[np.argsort(-mean[idx])]
    return [(format_clock(grid[i]), float(mean[i])) for i in order[:top]]
