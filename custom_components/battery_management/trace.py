"""One CSV row per control tick, on disk.

Why a file and not the diagnostics download. The in-memory tick log holds a
thousand rows - about four hours - and dies with the process. Every question
worth asking so far has been about a day that had already scrolled off it:
"why did the pack not charge on Saturday", "why did the probe answer 2 when it
answered 3 last week". Those are answerable by arithmetic or not at all, and
arithmetic needs the numbers to still exist.

So: one file per day under the config directory, one row per tick, every input
and every bound the loop used. Old files are deleted by age, which is a policy
anyone can check by looking at the folder.

Two rules this module keeps to:

* **Never raise into the control loop.** A full disk must cost the trace, not
  the batteries. Every entry point swallows and counts instead.
* **Never block the event loop.** Rows are buffered and flushed from an
  executor thread, so a slow SD card cannot stall a tick.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import time
from datetime import datetime, timedelta, timezone

_LOGGER = logging.getLogger(__name__)

#: Flush at most this often, so a 15 s tick does not mean a write every 15 s.
FLUSH_SECONDS = 60
#: ...or when this many rows are waiting, whichever comes first.
FLUSH_ROWS = 20


class Trace:
    """Buffered, append-only, one file per day."""

    def __init__(self, directory: str, keep_days: int = 14) -> None:
        self._dir = directory
        self._keep = keep_days
        self._rows: list[dict] = []
        self._fields: list[str] = []
        # started now, not at zero: at zero the very first tick looks
        # overdue and writes a single-row file, which turns a 15 s loop
        # into a 15 s disk write for as long as one row keeps arriving
        self._last_flush = time.monotonic()
        # which file the current field set resolved to, so the header is
        # only read back when the columns actually change
        self._resolved: tuple | None = None
        self._path: str = ""
        #: Surfaced in diagnostics: a trace that is silently not being written
        #: is worse than none, because it is discovered a week later.
        self.written = 0
        self.errors = 0
        self.last_error: str | None = None

    # -- writing --------------------------------------------------------------

    def add(self, row: dict) -> bool:
        """Buffer a row. Returns True when a flush is due."""
        # the first row decides the column order; later keys are appended so a
        # new field mid-run does not shift every earlier column
        for key in row:
            if key not in self._fields:
                self._fields.append(key)
        self._rows.append(row)
        due = (
            len(self._rows) >= FLUSH_ROWS
            or time.monotonic() - self._last_flush >= FLUSH_SECONDS
        )
        return due

    def path_for(self, moment: datetime) -> str:
        return os.path.join(self._dir, f"{moment:%Y-%m-%d}.csv")

    def _target(self, now: datetime) -> tuple[str, bool]:
        """Which file today's rows belong in, and whether to write a header.

        The columns are not fixed for all time: an upgrade adds some, a unit
        coming online adds its own. Appending rows with a different shape under
        a header that was written earlier silently shifts every value into the
        wrong column - which is exactly what happened the first day this ran,
        when an upgrade took the row from 37 fields to 46 and the file kept the
        old 37-column header.

        So the existing header is read back and compared. Same shape, append.
        Different shape, start `<day>.2.csv` and leave the old file intact and
        readable. Rotating is the only option that neither loses rows nor
        rewrites a file that something else may be reading.
        """
        base = self.path_for(now)
        if self._resolved == (base, tuple(self._fields)):
            return self._path, False       # unchanged since the last flush
        path, part = base, 1
        while os.path.exists(path):
            with open(path, encoding="utf-8", newline="") as handle:
                existing = (handle.readline().rstrip("\r\n") or "").split(",")
            if existing == self._fields:
                self._resolved = (base, tuple(self._fields))
                self._path = path
                return path, False
            part += 1
            path = base[:-4] + f".{part}.csv"
            _LOGGER.info(
                "trace columns changed; continuing in %s", os.path.basename(path)
            )
        self._resolved = (base, tuple(self._fields))
        self._path = path
        return path, True

    @staticmethod
    def _moment_of(row: dict, fallback: datetime) -> datetime:
        """When this row happened, according to the row itself."""
        stamp = row.get("at")
        if isinstance(stamp, str):
            try:
                return datetime.fromisoformat(stamp)
            except ValueError:
                pass
        return fallback

    def flush(self) -> None:
        """Append the buffer. Runs in an executor; must not raise.

        Rows are filed by their own timestamp, not by the clock at flush time.
        Buffering up to a minute means a tick at 23:59:52 is written after
        midnight, and keying the filename off `now` put it in tomorrow - four
        rows of 2026-08-16 turned up in `2026-08-17.csv` on the first real day
        of this. Harmless to read, but it quietly breaks anyone slicing the
        trace by day, and `_prune` deletes by the name in the filename.

        Grouping also means a buffer that straddles midnight splits correctly
        instead of all landing on whichever side won.
        """
        rows, self._rows = self._rows, []
        self._last_flush = time.monotonic()
        if not rows:
            return
        try:
            os.makedirs(self._dir, exist_ok=True)
            now = datetime.now(timezone.utc)
            # dicts keep insertion order, so days are written oldest first
            days: dict[str, tuple[datetime, list[dict]]] = {}
            for row in rows:
                moment = self._moment_of(row, now)
                days.setdefault(f"{moment:%Y-%m-%d}", (moment, []))[1].append(row)
            for moment, group in days.values():
                path, fresh = self._target(moment)
                # csv wants a text handle; build the text first so a failure
                # part way through cannot leave half a row in the file
                buffer = io.StringIO()
                writer = csv.DictWriter(
                    buffer,
                    fieldnames=self._fields,
                    extrasaction="ignore",
                    lineterminator="\n",
                )
                if fresh:
                    writer.writeheader()
                for row in group:
                    writer.writerow(row)
                with open(path, "a", encoding="utf-8", newline="") as handle:
                    handle.write(buffer.getvalue())
                self.written += len(group)
                if fresh:
                    # by the real clock: retention is about age, not about
                    # which day the rows we happen to be writing belong to
                    self._prune(now)
        except Exception as err:  # noqa: BLE001 - a trace must never break a tick
            self.errors += len(rows)
            self.last_error = f"{type(err).__name__}: {err}"
            _LOGGER.warning("could not write the trace: %s", err)

    def _prune(self, now: datetime) -> None:
        """Delete whole days older than the retention window."""
        cutoff = (now - timedelta(days=self._keep)).date()
        try:
            names = os.listdir(self._dir)
        except OSError:
            return
        for name in names:
            if not name.endswith(".csv"):
                continue
            try:
                # "2026-08-16.csv" and "2026-08-16.2.csv" are the same day
                day = datetime.strptime(name[:10], "%Y-%m-%d").date()
            except ValueError:
                continue  # not ours; leave it alone
            if day < cutoff:
                try:
                    os.remove(os.path.join(self._dir, name))
                except OSError as err:
                    _LOGGER.debug("could not remove %s: %s", name, err)

    # -- reading back ---------------------------------------------------------

    def files(self) -> list[str]:
        """Which days are on disk, newest first."""
        try:
            names = sorted(
                (n for n in os.listdir(self._dir) if n.endswith(".csv")), reverse=True
            )
        except OSError:
            return []
        return names

    def summary(self) -> dict:
        """What the diagnostics should say about the trace itself."""
        out: dict = {
            "directory": self._dir,
            "keep_days": self._keep,
            "rows_written": self.written,
            "rows_lost": self.errors,
            "buffered": len(self._rows),
            "last_error": self.last_error,
        }
        days = []
        for name in self.files():
            path = os.path.join(self._dir, name)
            try:
                days.append({"file": name, "bytes": os.path.getsize(path)})
            except OSError:
                continue
        out["days"] = days
        return out
