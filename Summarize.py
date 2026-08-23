"""Summarize the Spring Hill parking log: typical free spaces by day and hour.

    python Summarize.py                 # Spring Hill
    python Summarize.py "West Deck"     # any other lot in the log
"""
import csv, statistics, sys
from collections import defaultdict
from pathlib import Path

LOT = sys.argv[1] if len(sys.argv) > 1 else "Spring Hill"
CSV = Path(__file__).parent / "data" / "parking-log.csv"

rows = [r for r in csv.DictReader(CSV.open(encoding="utf-8-sig"))
        if LOT.lower() in r["location_name"].lower()]
if not rows:
    sys.exit(f"No rows matching {LOT!r} in {CSV}")

print(f"{rows[0]['location_name']}  -  {len(rows)} samples, "
      f"{rows[0]['timestamp_local']} to {rows[-1]['timestamp_local']}\n")

days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
buckets = defaultdict(list)
for r in rows:
    buckets[(r["day_of_week"], int(r["hour_local"]))].append(int(r["free_spaces"]))

total = int(rows[-1]["total_spaces"])
print(f"{'day':<10}{'hour':>5}{'n':>5}{'median free':>13}{'min':>6}{'max':>6}   bar (of {total} spaces)")
for day in days:
    for hour in range(24):
        vals = buckets.get((day, hour))
        if not vals:
            continue
        med = statistics.median(vals)
        bar = "#" * round(med / max(total, 1) * 40)
        print(f"{day:<10}{hour:>5}{len(vals):>5}{med:>13.0f}{min(vals):>6}{max(vals):>6}   {bar}")
