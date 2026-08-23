"""Spring Hill parking tracker -- desktop GUI.

    python app.py

Polls the NC State parking endpoint every 5 minutes while open, stores each
reading in data/parking.db, and plots it two ways:

  * Single day      -- free spaces across one specific calendar date
  * Weekday average -- the average curve for e.g. every Monday on record,
                       so you can see what a typical Monday looks like

The GUI is only one of the collectors. Task Scheduler keeps logging when this
window is closed, and the cloud cron keeps logging when the laptop is off.
All three write the same (timestamp, location) rows, and duplicates are
ignored, so they can safely overlap.
"""
from __future__ import annotations

import datetime as dt
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")

import numpy as np
from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                               NavigationToolbar2Tk)
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

import analysis
import parking_core as core

POLL_SECONDS = 300          # 5 minutes, as requested
ACCENT = "#CC0000"          # NC State red
MUTED = "#B0B4BC"


class Collector(threading.Thread):
    """Polls in the background so the window never freezes on a slow request."""

    def __init__(self, out_queue: queue.Queue, interval: int = POLL_SECONDS):
        super().__init__(daemon=True)
        self.q = out_queue
        self.interval = interval
        self.next_at = time.time()
        self._stop = threading.Event()
        self._wake = threading.Event()

    def poll_now(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        # Its own connection: SQLite objects must not cross threads.
        conn = core.connect()
        while not self._stop.is_set():
            try:
                ts, lots = core.poll_once(conn)
                self.q.put(("ok", ts, lots))
            except Exception as exc:
                self.q.put(("err", str(exc), None))
            self.next_at = time.time() + self.interval
            self._wake.wait(self.interval)
            self._wake.clear()
        conn.close()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Spring Hill Parking Tracker")
        self.geometry("1080x720")
        self.minsize(880, 600)

        self.conn = core.connect()
        self.events: queue.Queue = queue.Queue()
        self.collector = Collector(self.events)

        self._build_header()
        self._build_controls()
        self._build_plot()
        self._build_statusbar()

        self.refresh_sources()
        self.collector.start()
        self.after(250, self._drain_events)
        self.after(1000, self._tick_countdown)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.redraw()

    # ---------------------------------------------------------------- layout

    def _build_header(self) -> None:
        bar = ttk.Frame(self, padding=(12, 10, 12, 4))
        bar.pack(fill="x")

        self.lot_var = tk.StringVar(value=core.DEFAULT_LOT)
        ttk.Label(bar, text="Lot:").pack(side="left")
        self.lot_box = ttk.Combobox(bar, textvariable=self.lot_var,
                                    state="readonly", width=34)
        self.lot_box.pack(side="left", padx=(6, 16))
        self.lot_box.bind("<<ComboboxSelected>>", lambda _e: self.on_lot_change())

        self.live_var = tk.StringVar(value="waiting for first poll...")
        ttk.Label(bar, textvariable=self.live_var,
                  font=("Segoe UI", 12, "bold")).pack(side="left")

        self.countdown_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.countdown_var,
                  foreground="#666").pack(side="right")

    def _build_controls(self) -> None:
        box = ttk.LabelFrame(self, text="View", padding=10)
        box.pack(fill="x", padx=12, pady=6)

        self.mode = tk.StringVar(value="weekday")
        ttk.Radiobutton(box, text="Single day", value="day", variable=self.mode,
                        command=self.on_mode_change).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(box, text="Weekday average", value="weekday",
                        variable=self.mode, command=self.on_mode_change
                        ).grid(row=1, column=0, sticky="w")

        ttk.Separator(box, orient="vertical").grid(row=0, column=1, rowspan=2,
                                                   sticky="ns", padx=14)

        ttk.Label(box, text="Date:").grid(row=0, column=2, sticky="e", padx=(0, 6))
        self.date_var = tk.StringVar()
        self.date_box = ttk.Combobox(box, textvariable=self.date_var,
                                     state="readonly", width=20)
        self.date_box.grid(row=0, column=3, sticky="w")
        self.date_box.bind("<<ComboboxSelected>>", lambda _e: self.redraw())

        ttk.Label(box, text="Weekday:").grid(row=1, column=2, sticky="e", padx=(0, 6))
        self.weekday_var = tk.StringVar(value=analysis.WEEKDAYS[dt.date.today().weekday()])
        self.weekday_box = ttk.Combobox(box, textvariable=self.weekday_var,
                                        values=analysis.WEEKDAYS,
                                        state="readonly", width=20)
        self.weekday_box.grid(row=1, column=3, sticky="w")
        self.weekday_box.bind("<<ComboboxSelected>>", lambda _e: self.redraw())

        ttk.Separator(box, orient="vertical").grid(row=0, column=4, rowspan=2,
                                                   sticky="ns", padx=14)

        self.show_weeks = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="Show each week", variable=self.show_weeks,
                        command=self.redraw).grid(row=0, column=5, sticky="w")
        self.show_band = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="Show spread (±1 sd)", variable=self.show_band,
                        command=self.redraw).grid(row=1, column=5, sticky="w")

        ttk.Label(box, text="Smoothing:").grid(row=0, column=6, sticky="e", padx=(14, 6))
        self.bin_var = tk.StringVar(value="15 min")
        bins = ttk.Combobox(box, textvariable=self.bin_var, state="readonly", width=9,
                            values=["5 min", "10 min", "15 min", "30 min", "60 min"])
        bins.grid(row=0, column=7, sticky="w")
        bins.bind("<<ComboboxSelected>>", lambda _e: self.redraw())

        btns = ttk.Frame(box)
        btns.grid(row=1, column=6, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Button(btns, text="Poll now", command=self.on_poll_now).pack(side="left", padx=3)
        ttk.Button(btns, text="Sync cloud", command=self.on_sync).pack(side="left", padx=3)
        ttk.Button(btns, text="Export CSV", command=self.on_export).pack(side="left", padx=3)

        box.columnconfigure(8, weight=1)

    def _build_plot(self) -> None:
        holder = ttk.Frame(self)
        holder.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.fig = Figure(figsize=(9, 4.6), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=holder)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        NavigationToolbar2Tk(self.canvas, holder).update()

    def _build_statusbar(self) -> None:
        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status_var, relief="sunken",
                  anchor="w", padding=(8, 4)).pack(fill="x", side="bottom")

    # ----------------------------------------------------------- data wiring

    @property
    def lot(self) -> str:
        return self.lot_var.get()

    @property
    def bin_minutes(self) -> int:
        return int(self.bin_var.get().split()[0])

    def refresh_sources(self) -> None:
        """Repopulate the lot and date pickers from whatever is in the db."""
        lots = core.known_locations(self.conn) or [core.DEFAULT_LOT]
        self.lot_box["values"] = lots
        if self.lot not in lots:
            self.lot_var.set(lots[0])

        dates = core.available_dates(self.conn, self.lot)
        labels = [d.strftime("%Y-%m-%d (%a)") for d in dates]
        self.date_box["values"] = labels
        if labels and self.date_var.get() not in labels:
            self.date_var.set(labels[-1])

        s = core.stats(self.conn, self.lot)
        if s["n"]:
            self.status_var.set(
                f"{s['n']} samples for {self.lot}  |  "
                f"{s['first']:%b %d %H:%M} to {s['last']:%b %d %H:%M}  |  "
                f"{len(dates)} day(s) on record  |  {core.DB_PATH}")
        else:
            self.status_var.set(f"no samples yet  |  {core.DB_PATH}")

    def _drain_events(self) -> None:
        changed = False
        try:
            while True:
                kind, a, b = self.events.get_nowait()
                if kind == "ok":
                    self._on_poll_ok(a, b)
                    changed = True
                else:
                    self.live_var.set("poll failed")
                    self.status_var.set(f"last poll failed: {a}")
        except queue.Empty:
            pass
        if changed:
            self.refresh_sources()
            self.redraw()
        self.after(250, self._drain_events)

    def _on_poll_ok(self, ts: str, lots: list) -> None:
        match = next((l for l in lots
                      if l["location_name"] == self.lot), None)
        if match is None:
            self.live_var.set(f"polled {len(lots)} lots")
            return
        local = core.to_local(ts)
        self.live_var.set(
            f"{match['free_spaces']} free of {match['total_spaces']}"
            f"  ({match['occupancy']}% full)   as of {local:%H:%M:%S}")

    def _tick_countdown(self) -> None:
        remain = max(0, int(self.collector.next_at - time.time()))
        self.countdown_var.set(f"next poll in {remain // 60}:{remain % 60:02d}")
        self.after(1000, self._tick_countdown)

    # --------------------------------------------------------------- actions

    def on_mode_change(self) -> None:
        day = self.mode.get() == "day"
        self.date_box.configure(state="readonly" if day else "disabled")
        self.weekday_box.configure(state="disabled" if day else "readonly")
        self.redraw()

    def on_lot_change(self) -> None:
        self.refresh_sources()
        self.redraw()

    def on_poll_now(self) -> None:
        self.collector.poll_now()
        self.status_var.set("polling...")

    def on_sync(self) -> None:
        """Pull in everything the cloud collector logged while the laptop was
        off. Synchronous on purpose: a git pull takes a second or two, and a
        brief busy cursor is clearer than a silent background task."""
        self.status_var.set("syncing from the cloud collector...")
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            n, message = core.sync(self.conn)
        except Exception as exc:
            messagebox.showerror(
                "Sync failed",
                f"{exc}\n\nThe cloud collector syncs by pulling this repo. "
                "Check that a remote is configured (git remote -v) and that "
                "the workflow has run at least once.")
            self.status_var.set("sync failed")
            return
        finally:
            self.configure(cursor="")
        self.status_var.set(message)
        if n:
            self.refresh_sources()
            self.redraw()

    def on_export(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile=f"{self.lot.replace(' ', '-').lower()}.csv")
        if not path:
            return
        n = core.export_csv(self.conn, Path(path), self.lot)
        self.status_var.set(f"exported {n} rows to {path}")

    def _on_close(self) -> None:
        self.collector.stop()
        self.conn.close()
        self.destroy()

    # --------------------------------------------------------------- drawing

    def _style_axes(self, title: str, total: int | None) -> None:
        self.ax.set_title(title, fontsize=12, pad=12)
        self.ax.set_xlabel("time of day")
        self.ax.set_ylabel("free spaces")
        self.ax.set_xlim(0, 24 * 60)
        self.ax.set_xticks(np.arange(0, 24 * 60 + 1, 120))
        self.ax.xaxis.set_major_formatter(
            FuncFormatter(lambda v, _p: analysis.format_clock(v)))
        self.ax.grid(True, alpha=0.25, linewidth=0.7)
        if total:
            self.ax.set_ylim(0, total * 1.05)
            self.ax.axhline(total, color=MUTED, linestyle=":", linewidth=1)
            self.ax.text(10, total, f" capacity {total}", va="bottom",
                         fontsize=8, color="#777")

    def _empty(self, message: str) -> None:
        self.ax.text(0.5, 0.5, message, transform=self.ax.transAxes,
                     ha="center", va="center", fontsize=11, color="#777")
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.canvas.draw_idle()

    def redraw(self) -> None:
        self.ax.clear()
        row = core.latest(self.conn, self.lot)
        total = row["total"] if row else None
        if self.mode.get() == "day":
            self._draw_single_day(total)
        else:
            self._draw_weekday(total)
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _draw_single_day(self, total) -> None:
        label = self.date_var.get()
        if not label:
            self._empty("No data collected yet.\nLeave this running, or wait for "
                        "the scheduled collector.")
            return
        day = dt.date.fromisoformat(label.split()[0])
        grid, vals = analysis.single_day(self.conn, self.lot, day, self.bin_minutes)
        if np.all(np.isnan(vals)):
            self._empty(f"No samples stored for {day}.")
            return

        self.ax.plot(grid, vals, color=ACCENT, linewidth=2,
                     label=day.strftime("%A %b %d"))
        self.ax.fill_between(grid, 0, vals, color=ACCENT, alpha=0.10)
        self._style_axes(f"{self.lot} — {day:%A, %B %d, %Y}", total)

        valid = ~np.isnan(vals)
        low = int(np.nanmin(vals))
        low_at = analysis.format_clock(grid[valid][np.nanargmin(vals[valid])])
        self.ax.legend(loc="lower left", frameon=False, fontsize=9)
        self.status_var.set(
            f"{day:%A %b %d}: {int(valid.sum())} filled bins, "
            f"fullest at {low_at} with {low} spaces free")

    def _draw_weekday(self, total) -> None:
        weekday = analysis.WEEKDAYS.index(self.weekday_var.get())
        grid, per_date, mean, std, coverage = analysis.weekday_profile(
            self.conn, self.lot, weekday, self.bin_minutes)

        if not per_date:
            self._empty(f"No {self.weekday_var.get()} on record yet.\n"
                        "The average appears once at least one has been logged.")
            return

        if self.show_weeks.get():
            for i, (day, curve) in enumerate(sorted(per_date.items())):
                self.ax.plot(grid, curve, color=MUTED, linewidth=1, alpha=0.75,
                             label="individual weeks" if i == 0 else None)

        if self.show_band.get() and np.any(~np.isnan(std)):
            self.ax.fill_between(grid, mean - std, mean + std, color=ACCENT,
                                 alpha=0.15, linewidth=0, label="±1 sd")

        n = len(per_date)
        self.ax.plot(grid, mean, color=ACCENT, linewidth=2.4,
                     label=f"average of {n} {self.weekday_var.get()}"
                           f"{'s' if n != 1 else ''}")
        self._style_axes(
            f"{self.lot} — typical {self.weekday_var.get()}", total)
        self.ax.legend(loc="lower left", frameon=False, fontsize=9)

        best = analysis.best_windows(grid, mean, top=3)
        if best and n >= 2:
            pretty = ",  ".join(f"{t} ({v:.0f} free)" for t, v in best)
            self.status_var.set(
                f"averaged over {n} {self.weekday_var.get()}s  |  "
                f"emptiest daytime slots: {pretty}")
        else:
            self.status_var.set(
                f"only {n} {self.weekday_var.get()}"
                f"{'s' if n != 1 else ''} on record -- the average gets "
                "meaningful after a few weeks")


if __name__ == "__main__":
    App().mainloop()
