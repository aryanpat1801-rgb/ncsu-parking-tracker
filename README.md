# Spring Hill Parking Tracker

Logs how many spaces are free in NC State's Spring Hill Park and Ride (and the
other nine lots, for free), and plots the result so you can pick the times of
day that are actually worth driving in.

The cloud collector is the one that matters: **every 10 minutes, 7am–5pm on
weekdays, whether or not the laptop is on**. The local collectors are a bonus —
they add denser **5-minute** samples whenever the machine happens to be awake.

Data comes from the public JSON endpoint behind the "Parking Availability" table
on <https://transportation.ncsu.edu/> — the same live feed the OnCampus app shows.

## Quick start

```powershell
python app.py                                                    # the GUI
powershell -ExecutionPolicy Bypass -File Register-Task.ps1       # background collector
```

## The three collectors

They all write the same `(timestamp, location)` rows and duplicates are ignored,
so running all three at once is safe and gives the best coverage.

| Collector | Runs when | Set up |
|---|---|---|
| **GUI** (`app.py`) | the window is open | just launch it |
| **Task Scheduler** (`collect.py`) | laptop is on, GUI closed or not | `Register-Task.ps1` |
| **GitHub Actions** (`cloud_run.py`) | always, including laptop off | see below |

### Why three

A desktop GUI cannot collect data while the laptop is off — nothing running
locally can. The GitHub Actions collector is what actually delivers the
"with or without my laptop being on" part; the local two just fill in denser,
more reliable samples whenever the machine happens to be awake.

## Always-on collection via GitHub Actions

Collects **every 10 minutes, 7am–5pm campus time, weekdays only**, on GitHub's
machines — so it keeps logging while the laptop is off. Daylight saving is
handled automatically. **Requires a public repo** (see *Why public* below).

1. Create an empty repo at <https://github.com/new>. Do **not** add a README,
   `.gitignore`, or licence — this folder already has its own history.

2. Point this folder at it and push:

   ```bash
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```

3. In the repo: **Settings → Actions → General → Workflow permissions** →
   *Read and write permissions*. Without this the collector cannot commit its
   samples and every run fails at the last step.

4. Press **Sync cloud** in the GUI whenever you want the laptop's copy caught
   up. It runs `git pull` and merges `data/cloud-log.csv` in.

### One trigger starts a collector, not a sample

This is the part that makes the 10-minute promise real. GitHub's cron is
**best effort**, and for this repo it was badly so: a `*/10` schedule was only
honoured about six times a day, roughly 90 minutes apart, at near-identical
times each day. Since the endpoint only ever reports *right now*, every mark
it missed was lost for good — which is why syncing never filled the gaps.

So the model is inverted. A firing no longer means "take one sample"; it
starts `cloud_run.py`, which **stays alive and polls the 10-minute grid
itself** until the window closes. Cron now only has to land once a day
instead of sixty times.

| | Old | New |
|---|---|---|
| What a firing does | one sample, then exit | poll every 10 min until 5pm |
| Firings needed per day | ~61 | 1 (with the cron as a backstop) |
| Samples per weekday | ~6 in practice | ~61 |

Details worth knowing:

- **It polls on the wall clock** — samples land on :00, :10, :20 … however
  late the run itself started, so they bin cleanly in the GUI.
- **It commits every 3 polls**, so a runner that dies loses half an hour at
  worst rather than the rest of the day. Expect ~20 commits per weekday.
- **A job is capped at 6 hours** and the window is 10, so at 5h30m the
  collector pushes, asks GitHub to start its successor via `workflow_dispatch`
  (which is why the job needs `actions: write`), and exits. The `*/10` cron
  stays on underneath: if a collector dies outright, the next firing starts a
  fresh one, and the concurrency group keeps it to one at a time.
- **The cron is now just an ignition source**, so it is scheduled early and
  wide (`*/10 10-22 * * 1-5`) rather than precisely. A firing delayed by an
  hour still gets a collector up before 7am; `cloud_run.py` then idles until
  the real open and stops at the real close.

### Daylight saving is handled automatically

GitHub cron is **always UTC with no DST support**, so no cron expression can
mean "7am–5pm campus time" year-round. `in_window.py` decides at runtime
instead, and `zoneinfo` carries the real rules — correct through the
2026-11-01 flip and every one after, with **nothing to edit**. Verify any time:

```bash
python in_window.py --check     # 18 pinned cases, incl. both 2026/2027 flips
```

To change the hours, edit `START_HOUR` / `END_HOUR` in `in_window.py`. Only
widen the *cron* if you move outside 6am–7pm, since it is the outer bound.

### Why public

Keeping a collector alive for the 10-hour window costs ~600 Actions minutes a
weekday, or ~13,200 a month. Public repos get **unlimited free Actions**;
private ones get 2,000 free minutes, so the same setup would run out in about
three days and then cost ~$90/month. There is nothing sensitive in here —
public parking counts and your own code — so public is the cheap answer.

If you ever need it private again, the honest options are to accept ~6 samples
a day, or move the collector to something with free reliable cron (Cloudflare
Workers' free tier does this well, and `parking_core.sync` already has a
`sync_mode: "url"` path built for pulling a published CSV).

### Housekeeping

GitHub **disables scheduled workflows after 60 days of no repo activity**. The
collector's own commits count, so this only bites over a long break — re-enable
it in the Actions tab. The CSV grows by ~610 rows (~60 KB) per weekday; git
deltas that well, but it is not forever-free.

## The GUI

- **Single day** — free spaces across one calendar date.
- **Weekday average** — the mean curve for every Monday (or any weekday) on
  record, with each individual week drawn faintly behind it and a ±1 standard
  deviation band. This is the view for building a parking schedule: it answers
  "what does a typical Tuesday look like" rather than "what happened last
  Tuesday".
- **Smoothing** — bin width. 5 min is raw; 15–30 min reads better once you have
  a few weeks.
- The status bar calls out the emptiest daytime slots for the current view.

## Command line

```bash
python collect.py                      # one poll into the database
python collect.py --loop 300           # poll every 5 min in a terminal
python collect.py --status             # what is stored right now
python collect.py --export out.csv     # dump everything to CSV
python collect.py --import-csv f.csv   # merge a CSV in (safe to repeat)
python collect.py --import-url URL     # merge the cloud collector's CSV
```

## Files

| File | What it is |
|---|---|
| `app.py` | Tkinter + matplotlib GUI |
| `collect.py` | command-line collector, stdlib only |
| `parking_core.py` | fetching, SQLite storage, import/export |
| `analysis.py` | time-of-day binning and weekday averaging |
| `cloud_run.py` | the long-running cloud collector — polls the 10-min grid, commits as it goes |
| `in_window.py` | when the window is open, in real campus time (`--check` self-tests) |
| `data/parking.db` | the SQLite store (all lots, all history) |
| `Register-Task.ps1` | registers the 5-minute Windows task |
| `Log-Parking.ps1` | the original CSV logger, superseded by `collect.py` |

## Being a good citizen

The endpoint needs no authentication and `robots.txt` does not exclude it, but
it is someone else's server. The collectors identify themselves honestly in the
User-Agent, poll no faster than every 5 minutes, and deliberately do **not** send the
`x-api-key` found in the page source — it is not required, and borrowing it is
the one thing here that could read as circumventing an access control. If NC
State ever asks you to stop, stop.
