"""Command-line collector for the NC State parking tracker.

Stdlib only -- this is what Task Scheduler and the cloud cron run.

    python collect.py                     one poll into the local database
    python collect.py --csv data/log.csv  also append to a CSV (cloud runner)
    python collect.py --loop 300          poll every 300s until interrupted
    python collect.py --import-csv F      merge a CSV into the database
    python collect.py --import-url U      merge a published CSV (cloud sync)
    python collect.py --export F          write the whole database out as CSV
    python collect.py --status            what is in the database right now
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from pathlib import Path

import parking_core as core


def append_csv(path: Path, ts_utc: str, lots: list) -> None:
    """Append one poll to a CSV, writing the header if the file is new.

    The cloud collector commits this file; the local GUI imports it. CSV
    rather than the database itself because a text file merges cleanly in git
    and a SQLite binary does not.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    local = core.to_local(ts_utc)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["timestamp_utc", "timestamp_local", "day_of_week",
                        "location_name", "free_spaces", "total_spaces",
                        "occupancy_pct"])
        for lot in lots:
            w.writerow([ts_utc, local.strftime("%Y-%m-%d %H:%M:%S"),
                        local.strftime("%A"), lot["location_name"],
                        lot["free_spaces"], lot["total_spaces"],
                        lot["occupancy"]])


def one_poll(conn, csv_path: Path | None, lot_name: str) -> str:
    ts, lots = core.poll_once(conn)
    if csv_path:
        append_csv(csv_path, ts, lots)
    local = core.to_local(ts).strftime("%Y-%m-%d %H:%M:%S")
    match = next((l for l in lots if lot_name.lower() in l["location_name"].lower()), None)
    if match:
        return (f"{local}  {match['location_name']}: {match['free_spaces']} free "
                f"of {match['total_spaces']} ({match['occupancy']}% full)")
    return f"{local}  stored {len(lots)} lots"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", type=Path, help="also append each poll to this CSV")
    p.add_argument("--loop", type=int, metavar="SECONDS",
                   help="keep polling on this interval instead of exiting")
    p.add_argument("--import-csv", type=Path, metavar="FILE")
    p.add_argument("--import-url", metavar="URL")
    p.add_argument("--export", type=Path, metavar="FILE")
    p.add_argument("--status", action="store_true")
    p.add_argument("--lot", default=core.DEFAULT_LOT)
    args = p.parse_args(argv)

    conn = core.connect()

    if args.import_csv:
        n = core.import_csv(conn, args.import_csv)
        print(f"imported {n} new rows from {args.import_csv}")
        return 0

    if args.import_url:
        n = core.import_url(conn, args.import_url)
        print(f"imported {n} new rows from {args.import_url}")
        return 0

    if args.export:
        n = core.export_csv(conn, args.export)
        print(f"exported {n} rows to {args.export}")
        return 0

    if args.status:
        for loc in core.known_locations(conn):
            s = core.stats(conn, loc)
            row = core.latest(conn, loc)
            span = (f"{s['first']:%Y-%m-%d %H:%M} to {s['last']:%Y-%m-%d %H:%M}"
                    if s["first"] else "no data")
            print(f"{loc:<36} {s['n']:>6} samples  {span}   now: {row['free']} free")
        return 0

    if args.loop:
        print(f"polling every {args.loop}s -- Ctrl+C to stop")
        while True:
            try:
                print(one_poll(conn, args.csv, args.lot), flush=True)
            except Exception as exc:                      # keep the loop alive
                print(f"{dt.datetime.now():%H:%M:%S}  poll failed: {exc}",
                      file=sys.stderr, flush=True)
            time.sleep(args.loop)

    try:
        print(one_poll(conn, args.csv, args.lot))
    except Exception as exc:
        print(f"poll failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
