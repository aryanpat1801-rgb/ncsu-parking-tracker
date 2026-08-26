"""Cloud collector that stays alive for as long as the campus window is open.

GitHub treats `cron` as best effort. The `*/10` schedule this repo used to
rely on was only being honoured about six times a day, ~90 minutes apart --
so most 10-minute marks were simply never sampled, and the endpoint only ever
reports *right now*, so a missed mark is gone for good.

The fix is to stop asking cron for sixty firings a day. A trigger no longer
means "take one sample", it means "start a collector that polls on the
10-minute grid until the window closes". Cron now only has to land once.

    python cloud_run.py

  * before the window   idles until it opens, if that fits in the runtime
  * inside the window   polls every 10 minutes, on the wall-clock mark
  * after the window    exits straight away
  * every COMMIT_EVERY polls it commits and pushes data/cloud-log.csv, so a
    runner that dies loses half an hour at worst rather than a whole day
  * on SIGTERM (a cancelled job) it commits what it has before leaving
  * if it runs out of runtime with the window still open, it asks GitHub to
    start its successor -- the cron stays on underneath as a backstop

FORCE=true, which the workflow sets for a manual "Run workflow" click, takes
a single sample outside the window and exits, so this is testable at any hour.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import signal
import subprocess
import time
import urllib.request

import collect
import in_window
import parking_core as core

POLL_SECONDS = 600          # the 10-minute cadence the whole point rests on
COMMIT_EVERY = 3            # push every 3 polls, i.e. every half hour
# A GitHub job is killed at 6 hours. Stop well short so the final push and
# the successor dispatch both have room.
MAX_RUNTIME = 5 * 3600 + 30 * 60
WORKFLOW_FILE = "collect-parking.yml"

CSV = core.DATA_DIR / "cloud-log.csv"

_stopping = False


def log(msg: str) -> None:
    now = dt.datetime.now(dt.timezone.utc).astimezone(in_window.CAMPUS_TZ)
    print(f"{now:%H:%M:%S} {msg}", flush=True)


def _on_sigterm(_signum, _frame) -> None:
    """A cancelled Actions job gets SIGTERM then SIGKILL ~10s later. Note it
    and let the loop unwind so the samples already taken still get pushed."""
    global _stopping
    _stopping = True
    log("SIGTERM -- committing what we have and stopping")


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------

def git(*args: str, check: bool = True, timeout: int = 180):
    proc = subprocess.run(["git", *args], capture_output=True, text=True,
                          timeout=timeout, cwd=core.ROOT)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {args[0]}: {(proc.stderr or proc.stdout).strip()}")
    return proc


def commit_and_push() -> bool:
    """Commit and push the CSV. Returns whether there was anything to send.

    The concurrency group means another collector should never be pushing at
    the same time, but a rebase retry is cheap insurance for the handover
    window where the outgoing and incoming runs briefly overlap.
    """
    if not CSV.exists():                # every poll in this run failed
        return False
    git("add", str(CSV))
    if git("diff", "--staged", "--quiet", check=False).returncode == 0:
        return False
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    git("commit", "-m", f"parking samples through {stamp}")
    for attempt in (1, 2, 3):
        if git("push", check=False).returncode == 0:
            return True
        log(f"push rejected, rebasing (attempt {attempt})")
        git("pull", "--rebase", "--autostash", check=False)
    raise RuntimeError("could not push after 3 attempts")


def safe_commit() -> None:
    """Never let a push problem end the run -- the samples are still in the
    CSV, and the next commit a few minutes later will carry them."""
    try:
        commit_and_push()
    except Exception as exc:
        log(f"commit failed, will retry with the next batch: {exc}")


# --------------------------------------------------------------------------
# polling
# --------------------------------------------------------------------------

def poll() -> bool:
    """One reading appended to the CSV. Nothing touches SQLite here: the
    runner's database would be thrown away with the container, and the CSV is
    what the GUI actually syncs from."""
    try:
        lots = core.fetch_lots()
    except Exception as exc:                    # one bad mark, not a bad run
        log(f"poll failed: {exc}")
        return False
    ts = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    collect.append_csv(CSV, ts, lots)
    match = next((l for l in lots if l["location_name"] == core.DEFAULT_LOT), None)
    if match:
        log(f"{core.DEFAULT_LOT}: {match['free_spaces']} free of "
            f"{match['total_spaces']} ({match['occupancy']}% full)")
    else:
        log(f"stored {len(lots)} lots")
    return True


def next_mark() -> float:
    """The next wall-clock 10-minute boundary, so samples land on :00, :10,
    :20 ... and bin cleanly however late the run itself started."""
    now = time.time()
    return now + (POLL_SECONDS - now % POLL_SECONDS)


def sleep_until(when: float) -> None:
    """Sleep in slices so a SIGTERM is acted on in seconds, not minutes."""
    while not _stopping:
        remain = when - time.time()
        if remain <= 0:
            return
        time.sleep(min(remain, 5))


# --------------------------------------------------------------------------
# handover
# --------------------------------------------------------------------------

def dispatch_successor() -> None:
    """Ask GitHub to start the next collector.

    workflow_dispatch is one of the two events GITHUB_TOKEN is allowed to
    raise, so this chains without a personal access token. Needs
    `actions: write` on the job.
    """
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        log("not running under Actions -- no successor to start")
        return
    branch = os.environ.get("GITHUB_REF_NAME") or "main"
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/"
        f"{WORKFLOW_FILE}/dispatches",
        data=json.dumps({"ref": branch}).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": core.USER_AGENT})
    try:
        urllib.request.urlopen(req, timeout=30)
        log("asked GitHub to start the next collector")
    except Exception as exc:
        # Not fatal: the cron underneath will pick the window back up.
        log(f"could not start a successor ({exc}) -- leaving it to the cron")


# --------------------------------------------------------------------------

def main() -> int:
    signal.signal(signal.SIGTERM, _on_sigterm)
    deadline = time.time() + MAX_RUNTIME
    forced = os.environ.get("FORCE", "").lower() == "true"

    if in_window.window_close() is None:
        if forced:
            log("outside the window, but FORCE is set -- one sample then out")
            poll()
            safe_commit()
            return 0
        opens = in_window.next_open()
        wait = (opens - dt.datetime.now(dt.timezone.utc)).total_seconds()
        if time.time() + wait > deadline:
            log(f"window opens {opens:%a %H:%M %Z} -- too far off to wait, "
                "leaving it to the next trigger")
            return 0
        log(f"window opens {opens:%a %H:%M %Z}, waiting {wait / 60:.0f} min")
        sleep_until(time.time() + wait)

    close = in_window.window_close()
    if close is None:                           # SIGTERM during the wait
        return 0
    log(f"collecting every {POLL_SECONDS // 60} min until {close:%H:%M %Z}")

    pending = 0
    while not _stopping and in_window.window_close() is not None:
        if poll():
            pending += 1
        if pending >= COMMIT_EVERY:
            safe_commit()
            pending = 0
        mark = next_mark()
        if mark > deadline:
            log("out of runtime with the window still open -- handing over")
            safe_commit()
            dispatch_successor()
            return 0
        sleep_until(mark)

    log("window closed" if not _stopping else "stopping")
    safe_commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
