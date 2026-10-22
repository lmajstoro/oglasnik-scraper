"""SQLite zapis viđenih oglasa i vremena zadnjeg uspješnog ciklusa."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scraper import Listing, parse_time


class Store:
    def __init__(self, path: str) -> None:
        database = Path(path)
        database.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(database)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def close(self) -> None:
        self.conn.close()

    def baseline_complete(self) -> bool:
        return self._meta("baseline_complete") == "1"

    def test_sent(self) -> bool:
        return self._meta("test_sent") == "1"

    def mark_test_sent(self) -> None:
        self._set_meta("test_sent", "1")

    def last_success(self) -> datetime | None:
        return parse_time(self._meta("last_success_at"))

    def set_last_success(self, when: datetime) -> None:
        self._set_meta("last_success_at", _iso(when))

    def codes(self) -> set[int]:
        rows = self.conn.execute("SELECT code FROM seen").fetchall()
        return {int(row["code"]) for row in rows}

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM seen").fetchone()
        return int(row["n"])

    def save_baseline(self, listings: list[Listing]) -> int:
        if not listings:
            raise ValueError("Baseline nema oglasa.")
        before = self.count()
        with self.conn:
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO seen
                    (code, posted_at, renewed_at, sent_at, title, url)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [_row(listing, sent_at=None) for listing in listings],
            )
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES ('baseline_complete', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            )
        return self.count() - before

    def mark_sent(self, listings: list[Listing], sent_at: datetime) -> None:
        if not listings:
            return
        stamp = _iso(sent_at)
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO seen (code, posted_at, renewed_at, sent_at, title, url)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    posted_at = excluded.posted_at,
                    renewed_at = excluded.renewed_at,
                    sent_at = excluded.sent_at,
                    title = excluded.title,
                    url = excluded.url
                """,
                [_row(listing, sent_at=stamp) for listing in listings],
            )

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen (
                code INTEGER PRIMARY KEY,
                posted_at TEXT NOT NULL,
                renewed_at TEXT,
                sent_at TEXT,
                title TEXT NOT NULL,
                url TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

    def _meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def _set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


def _row(listing: Listing, sent_at: str | None) -> tuple:
    return (
        listing.code,
        _iso(listing.posted_at),
        _iso(listing.renewed_at) if listing.renewed_at else None,
        sent_at,
        listing.title,
        listing.url,
    )


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
