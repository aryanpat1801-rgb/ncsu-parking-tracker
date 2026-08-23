# Spring Hill Parking Tracker

Logs how many spaces are free in NC State's Spring Hill Park and Ride (and the
other nine lots, for free) every 5 minutes, and plots the result so you can pick
the times of day that are actually worth driving in.

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

Collects **every 10 minutes, 7am–5pm campus time**, on GitHub's machines — so it
keeps logging while the laptop is off. Works fine on a **private** repo.

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

### Daylight saving

GitHub cron is **always UTC and has no DST support**, so the 7am–5pm window is
written in UTC and has to be moved twice a year in `collect-parking.yml`:

| Period | Cron |
|---|---|
| EDT, roughly Mar–Nov | `*/10 11-20 * * *` ← currently active |
| EST, roughly Nov–Mar | `*/10 12-21 * * *` |

Forget, and you quietly collect 6am–4pm instead. The local Task Scheduler
collector is immune — it runs in real local time.

### Actions minutes

60 runs/day ≈ **1,860 minutes/month** against the 2,000-minute free tier for
private repos. That fits, but with only ~7% headroom, because GitHub bills each
run rounded **up to a full minute** even though a poll takes seconds. If you
ever raise the frequency, either drop weekends (`*/10 11-20 * * 1-5`, about
1,320/month) or make the repo public, where Actions is unlimited and free.

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
