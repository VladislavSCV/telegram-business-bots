"""SQLite storage for leads, bookings and per-user usage limits."""

import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from bot.booking import overlaps

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    username TEXT,
    need TEXT NOT NULL,
    budget TEXT NOT NULL,
    deadline TEXT NOT NULL,
    contact TEXT NOT NULL,
    temperature TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    username TEXT,
    service TEXT NOT NULL,
    master TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    duration_min INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    reminded INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bookings_master_time ON bookings (master, starts_at);
CREATE TABLE IF NOT EXISTS usage (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (user_id, day, kind)
);
"""


@dataclass
class Booking:
    id: int
    user_id: int
    username: str | None
    service: str
    master: str
    starts_at: datetime
    duration_min: int
    status: str


class Storage:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def _booking(self, row: sqlite3.Row) -> Booking:
        return Booking(
            row["id"],
            row["user_id"],
            row["username"],
            row["service"],
            row["master"],
            datetime.fromisoformat(row["starts_at"]),
            row["duration_min"],
            row["status"],
        )

    # --- usage limits -------------------------------------------------------------------------
    def hit(self, user_id: int, kind: str, today: date) -> int:
        """Increment and return today's counter for `kind`."""
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO usage (user_id, day, kind, count) VALUES (?, ?, ?, 1) "
                "ON CONFLICT (user_id, day, kind) DO UPDATE SET count = count + 1",
                (user_id, today.isoformat(), kind),
            )
            return self.db.execute(
                "SELECT count FROM usage WHERE user_id = ? AND day = ? AND kind = ?", (user_id, today.isoformat(), kind)
            ).fetchone()[0]

    # --- leads --------------------------------------------------------------------------------
    def add_lead(
        self,
        user_id: int,
        username: str | None,
        need: str,
        budget: str,
        deadline: str,
        contact: str,
        temperature: str,
        now: datetime,
    ) -> int:
        with self.lock, self.db:
            cursor = self.db.execute(
                "INSERT INTO leads (user_id, username, need, budget, deadline, contact, temperature, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, username, need, budget, deadline, contact, temperature, now.isoformat()),
            )
            return cursor.lastrowid

    def recent_leads(self, limit: int = 10) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute("SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # --- bookings -----------------------------------------------------------------------------
    def bookings_for_master(self, master: str, day: date) -> list[Booking]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM bookings WHERE master = ? AND status = 'active' AND substr(starts_at, 1, 10) = ?",
                (master, day.isoformat()),
            ).fetchall()
        return [self._booking(r) for r in rows]

    def add_booking(
        self,
        user_id: int,
        username: str | None,
        service: str,
        master: str,
        starts_at: datetime,
        duration_min: int,
        now: datetime,
    ) -> int | None:
        """Insert unless the slot was taken meanwhile (returns None)."""
        with self.lock, self.db:
            rows = self.db.execute(
                "SELECT * FROM bookings WHERE master = ? AND status = 'active' AND substr(starts_at, 1, 10) = ?",
                (master, starts_at.date().isoformat()),
            ).fetchall()
            if any(overlaps(starts_at, duration_min, self._booking(r).starts_at, r["duration_min"]) for r in rows):
                return None
            cursor = self.db.execute(
                "INSERT INTO bookings (user_id, username, service, master, starts_at, duration_min, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, username, service, master, starts_at.isoformat(), duration_min, now.isoformat()),
            )
            return cursor.lastrowid

    def user_bookings(self, user_id: int, now: datetime) -> list[Booking]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM bookings WHERE user_id = ? AND status = 'active' AND starts_at >= ? ORDER BY starts_at",
                (user_id, now.isoformat()),
            ).fetchall()
        return [self._booking(r) for r in rows]

    def cancel_booking(self, booking_id: int, user_id: int) -> bool:
        with self.lock, self.db:
            cursor = self.db.execute(
                "UPDATE bookings SET status = 'cancelled' WHERE id = ? AND user_id = ? AND status = 'active'",
                (booking_id, user_id),
            )
            return cursor.rowcount == 1

    def upcoming_bookings(self, now: datetime, limit: int = 50) -> list[Booking]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM bookings WHERE status = 'active' AND starts_at >= ? ORDER BY starts_at LIMIT ?",
                (now.isoformat(), limit),
            ).fetchall()
        return [self._booking(r) for r in rows]

    def due_reminders(self, until: datetime, now: datetime) -> list[Booking]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM bookings WHERE status = 'active' AND reminded = 0 AND starts_at <= ? AND starts_at > ?",
                (until.isoformat(), now.isoformat()),
            ).fetchall()
        return [self._booking(r) for r in rows]

    def mark_reminded(self, booking_id: int):
        with self.lock, self.db:
            self.db.execute("UPDATE bookings SET reminded = 1 WHERE id = ?", (booking_id,))

    def all_bookings(self) -> list[Booking]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM bookings ORDER BY starts_at").fetchall()
        return [self._booking(r) for r in rows]
