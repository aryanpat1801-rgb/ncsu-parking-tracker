# Spring Hill Parking Tracker

Logs how many spaces are free in NC State's Spring Hill Park and Ride (and the
other nine lots, for free), and plots the result so you can pick the times of
day that are actually worth driving in.

Locally it samples **every 5 minutes** whenever the machine is awake; the cloud
collector fills the gaps **every 10 minutes, 7am–5pm on weekdays**.

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
| **GitHub Actions** (`collect-parking.yml`) | always, including laptop off | see below |

### Why three

A desktop GUI cannot collect data while the laptop is off — nothing running
locally can. The GitHub Actions collector is what actually delivers the
"with or without my laptop being on" part; the local two just fill in denser,
more reliable samples whenever the machine happens to be awake.

## Always-on collection via GitHub Actions

Collects **every 10 minutes, 7am–5pm campus time, weekdays only**, on GitHub's
machines — so it keeps logging while the laptop is off. Daylight saving is
handled automatically. Works fine on a **private** repo.

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
   up. It runs `git pull` and merges `data/cloud-log.csv` in — which is why a
   private repo is fine: it reuses the git credentials you already have, so no
   access token is ever stored in `config.json`.

### Daylight saving is handled automatically

GitHub cron is **always UTC with no DST support**, so no single cron expression
can mean "7am–5pm campus time" year-round. Instead the workflow schedules the
**union** of both windows — `*/10 11-21 * * 1-5`, i.e. 11:00–21:59 UTC, which
covers 7am–5pm ET under both EDT and EST — and `in_window.py` decides at
runtime whether each firing is genuinely in-window.

`zoneinfo` carries the real DST rules, so this is correct through the
2026-11-01 transition and every one after it, with **nothing to edit**. Verify
it yourself any time:

```bash
python in_window.py --check     # 14 pinned cases, incl. both 2026/2027 flips
```

To change the hours, edit `START_HOUR` / `END_HOUR` in `in_window.py`. Only
widen the *cron* if you go outside 7am–5pm, since the cron is the outer bound.

### Actions minutes

Weekdays only, so the worst case is a month with 22 weekdays:

| | Runs billed | Actually collect |
|---|---|---|
| Sep 2026 (EDT) | 1,452 | 1,320 |
| Nov 2026 (DST flips) | 1,386 | 1,260 |
| Jan 2027 (EST) | 1,386 | 1,260 |

Against the 2,000-minute free tier for private repos, that leaves ~27% headroom.

Two things drive those numbers. GitHub bills each run rounded **up to a full
minute** even though a poll takes seconds. And ~132 runs/month fire but skip
immediately — the hour of the union window that is out-of-window under the
current offset. Those skipped runs still cost a billed minute each; that is the
price of automatic DST, and it is cheaper than the alternative of splitting the
guard into its own job (which would bill a second minute on every *real* run).

Public repos get unlimited free Actions, so none of this applies if you make the
repo public.

### Cron reliability

Scheduled runs are **best-effort**: they often land several minutes late and can
be skipped entirely under load, so expect roughly 10–20 minute spacing rather
than a clean 10. GitHub also **disables scheduled workflows after 60 days of no
repo activity** — push something occasionally, or re-enable it in the Actions
tab. Neither hurts the analysis: every sample carries its own timestamp, and
gaps are treated as missing rather than as zero.

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
| `in_window.py` | decides EDT vs EST for the cloud collector (`--check` self-tests) |
| `data/parking.db` | the SQLite store (all lots, all history) |
| `Register-Task.ps1` | registers the 5-minute Windows task |
| `Log-Parking.ps1` | the original CSV logger, superseded by `collect.py` |

## Being a good citizen

The endpoint needs no authentication and `robots.txt` does not exclude it, but
it is someone else's server. The collectors identify themselves honestly in the
User-Agent, poll at 5-minute intervals, and deliberately do **not** send the
`x-api-key` found in the page source — it is not required, and borrowing it is
the one thing here that could read as circumventing an access control. If NC
State ever asks you to stop, stop.
