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

1. Create a repo and push this folder:

   ```bash
   git init && git add . && git commit -m "parking tracker"
   gh repo create ncsu-parking-tracker --private --source=. --push
   ```

2. In the repo: **Settings → Actions → General → Workflow permissions** →
   *Read and write permissions*. Without this the collector cannot commit.

3. It now appends to `data/cloud-log.csv` on GitHub. Copy that file's **raw**
   URL into `config.json`:

   ```json
   { "sync_url": "https://raw.githubusercontent.com/<you>/ncsu-parking-tracker/main/data/cloud-log.csv" }
   ```

4. Press **Sync cloud** in the GUI to merge every reading taken while the laptop
   was off. (Private repo? Use a public one, or sync with `git pull` plus
   `python collect.py --import-csv data/cloud-log.csv`.)

Two things to know about Actions cron: runs are **best-effort** and often land
several minutes late or get skipped under load, so expect roughly 5–15 minute
spacing rather than a clean 5; and GitHub **disables scheduled workflows after
60 days of no repo activity**, so push something occasionally or re-enable it in
the Actions tab. Neither hurts the analysis — every sample carries its own
timestamp and gaps are handled as missing rather than as zero.

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
