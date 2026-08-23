"""Shared data layer for the NC State parking tracker.

Stdlib only, so the collector can run anywhere (Task Scheduler, a CI runner,
a VM) without a pip install. The GUI adds matplotlib/numpy on top.

All timestamps are stored as UTC ISO-8601 and converted to campus-local time
(America/New_York) for display. That way a sample collected by a cloud runner
in UTC and one collected by the laptop in Eastern land on the same clock.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sqlite3
import subprocess
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ENDPOINT = ("https://transportation.ncsu.edu/wp-json/"
            "ncsu-transportation-parking-view/v1/get-parking-data")
USER_AGENT = "ncsu-parking-logger/1.0 (personal research)"
DEFAULT_LOT = "Spring Hill Park and Ride"
CAMPUS_TZ = ZoneInfo("America/New_York")

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "parking.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts_utc    TEXT    NOT NULL,
    location  TEXT    NOT NULL,
    free      INTEGER NOT NULL,
    total     INTEGER NOT NULL,
    occupancy INTEGER NOT NULL,
    PRIMARY KEY (ts_utc, location)
);
CREATE INDEX IF NOT EXISTS idx_loc_ts ON samples(location, ts_utc);
"""


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Lets the GUI read while a collector thread writes.
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def insert_samples(conn: sqlite3.Connection, ts_utc: str, lots: list) -> int:
    """Insert one poll's worth of rows. Duplicate (ts, location) pairs are
    ignored, so re-importing the same CSV is always safe."""
    rows = [(ts_utc, lot["location_name"], int(lot["free_spaces"]),
             int(lot["total_spaces"]), int(lot["occupancy"])) for lot in lots]
    cur = conn.executemany(
        "INSERT OR IGNORE INTO samples (ts_utc, location, free, total, occupancy)"
        " VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
    return cur.rowcount


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def fetch_lots(timeout: int = 30) -> list:
    """Return the current reading for every lot. Raises on network failure."""
    req = urllib.request.Request(ENDPOINT, headers={
        "User-Agent": USER_AGENT,
        "X-REQUESTED-WITH": "XMLHttpRequest",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    # The endpoint returns a list of lists; flatten it.
    lots = [lot for group in payload for lot in group]
    if not lots:
        raise ValueError("endpoint returned no lots")
    return lots


def poll_once(conn: sqlite3.Connection):
    """Fetch and store one sample. Returns (utc timestamp, lots)."""
    lots = fetch_lots()
    ts = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    insert_samples(conn, ts, lots)
    return ts, lots


# --------------------------------------------------------------------------
# queries
# --------------------------------------------------------------------------

def to_local(ts_utc: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts_utc).astimezone(CAMPUS_TZ)


def known_locations(conn: sqlite3.Connection) -> list:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT location FROM samples ORDER BY location")]


def latest(conn: sqlite3.Connection, location: str):
    return conn.execute(
        "SELECT * FROM samples WHERE location = ? ORDER BY ts_utc DESC LIMIT 1",
        (location,)).fetchone()


def series(conn: sqlite3.Connection, location: str) -> list:
    """Every sample for a lot as (campus-local datetime, free, total)."""
    return [(to_local(r["ts_utc"]), r["free"], r["total"])
            for r in conn.execute(
                "SELECT ts_utc, free, total FROM samples WHERE location = ?"
                " ORDER BY ts_utc", (location,))]


def available_dates(conn: sqlite3.Connection, location: str) -> list:
    return sorted({t.date() for t, _, _ in series(conn, location)})


def stats(conn: sqlite3.Connection, location: str) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) n, MIN(ts_utc) first, MAX(ts_utc) last"
        " FROM samples WHERE location = ?", (location,)).fetchone()
    return {
        "n": row["n"],
        "first": to_local(row["first"]) if row["first"] else None,
        "last": to_local(row["last"]) if row["last"] else None,
    }


# --------------------------------------------------------------------------
# import / export
# --------------------------------------------------------------------------

def import_csv(conn: sqlite3.Connection, path: Path) -> int:
    """Merge a CSV written by this project (or by the old PowerShell logger)
    into the database. Safe to run repeatedly."""
    added = 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            ts = row.get("timestamp_utc") or row.get("ts_utc")
            if not ts:
                continue
            ts = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ts = ts.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()
            added += insert_samples(conn, ts, [{
                "location_name": row["location_name"],
                "free_spaces": row["free_spaces"],
                "total_spaces": row["total_spaces"],
                "occupancy": row.get("occupancy_pct") or row.get("occupancy") or 0,
            }])
    return added


def import_url(conn: sqlite3.Connection, url: str) -> int:
    """Pull a CSV published by the cloud collector and merge it in."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        text = resp.read().decode("utf-8-sig")
    tmp = DATA_DIR / ".sync-download.csv"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(text, encoding="utf-8")
    try:
        return import_csv(conn, tmp)
    finally:
        tmp.unlink(missing_ok=True)


def export_csv(conn: sqlite3.Connection, path: Path, location=None) -> int:
    q = "SELECT ts_utc, location, free, total, occupancy FROM samples"
    args = ()
    if location:
        q += " WHERE location = ?"
        args = (location,)
    q += " ORDER BY ts_utc, location"
    rows = conn.execute(q, args).fetchall()
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp_utc", "timestamp_local", "day_of_week",
                    "location_name", "free_spaces", "total_spaces", "occupancy_pct"])
        for r in rows:
            local = to_local(r["ts_utc"])
            w.writerow([r["ts_utc"], local.strftime("%Y-%m-%d %H:%M:%S"),
                        local.strftime("%A"), r["location"], r["free"],
                        r["total"], r["occupancy"]])
    return len(rows)


CLOUD_CSV = DATA_DIR / "cloud-log.csv"


def sync_via_git(conn: sqlite3.Connection, repo_dir: Path = ROOT):
    """Pull the cloud collector's commits, then merge its CSV in.

    This is the sync path for a PRIVATE repo: it reuses whatever git
    credentials the machine already has, so no access token has to be stored
    in config.json. Returns (rows added, human-readable message).
    """
    def git(*args, timeout=180):
        try:
            return subprocess.run(["git", "-C", str(repo_dir), *args],
                                  capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise RuntimeError("git is not on PATH")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"git {args[0]} timed out")

    # Checked up front: git's own message for this case is the unhelpful
    # "set-upstream-to=origin/<branch>" hint, which buries the real problem.
    if not git("remote", timeout=15).stdout.strip():
        raise RuntimeError(
            "No git remote is configured yet. Create the repo on GitHub and "
            "run:  git remote add origin <url>  &&  git push -u origin main")

    proc = git("pull", "--rebase", "--autostash")
    if proc.returncode != 0:
        lines = [l for l in (proc.stderr or proc.stdout).strip().splitlines()
                 if l.strip() and not l.startswith("hint:")]
        if any("no tracking information" in l.lower() for l in lines):
            raise RuntimeError(
                "This branch has no upstream yet. Run:  "
                "git push -u origin main")
        raise RuntimeError(lines[0] if lines else "git pull failed")

    if not CLOUD_CSV.exists():
        return 0, ("pulled, but data/cloud-log.csv does not exist yet -- "
                   "has the workflow run at least once?")
    n = import_csv(conn, CLOUD_CSV)
    return n, f"pulled and merged {n} new reading(s) from the cloud collector"


def sync(conn: sqlite3.Connection):
    """Dispatch to whichever sync the config asks for."""
    cfg = load_config()
    url = (cfg.get("sync_url") or "").strip()
    if cfg.get("sync_mode", "git") == "url" and url:
        n = import_url(conn, url)
        return n, f"merged {n} new reading(s) from {url}"
    return sync_via_git(conn)


def load_config() -> dict:
    p = ROOT / "config.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}
