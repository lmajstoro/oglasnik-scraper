"""SQLite zapis viđenih oglasa i vremena zadnjeg uspješnog ciklusa."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scraper import Listing, parse_time

INDEX = "index"
PLAVI = "plavi"
_META_KEYS = ("baseline_complete", "test_sent", "last_success_at")


class Store:
    def __init__(self, path: str, source: str = INDEX) -> None:
        if source not in {INDEX, PLAVI}:
            raise ValueError(f"Nepoznat izvor {source}.")
        self.source = source
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
        rows = self.conn.execute(
            "SELECT code FROM seen WHERE source = ?",
            (self.source,),
        ).fetchall()
        return {int(row["code"]) for row in rows}

    def max_code(self) -> int | None:
        row = self.conn.execute(
            "SELECT MAX(code) AS n FROM seen WHERE source = ?",
            (self.source,),
        ).fetchone()
        if row is None or row["n"] is None:
            return None
        return int(row["n"])

    def count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM seen WHERE source = ?",
            (self.source,),
        ).fetchone()
        return int(row["n"])

    def remember(self, listings: list[Listing]) -> int:
        if not listings:
            return 0
        before = self.count()
        with self.conn:
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO seen
                    (source, code, posted_at, renewed_at, sent_at, title, url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [_row(self.source, listing, sent_at=None) for listing in listings],
            )
        return self.count() - before

    def save_baseline(self, listings: list[Listing]) -> int:
        if not listings:
            raise ValueError("Baseline nema oglasa.")
        added = self.remember(listings)
        self._set_meta("baseline_complete", "1")
        return added

    def mark_sent(self, listings: list[Listing], sent_at: datetime) -> None:
        if not listings:
            return
        stamp = _iso(sent_at)
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO seen (source, code, posted_at, renewed_at, sent_at, title, url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, code) DO UPDATE SET
                    posted_at = excluded.posted_at,
                    renewed_at = excluded.renewed_at,
                    sent_at = excluded.sent_at,
                    title = excluded.title,
                    url = excluded.url
                """,
                [_row(self.source, listing, sent_at=stamp) for listing in listings],
            )

    def _init(self) -> None:
        columns = [row[1] for row in self.conn.execute("PRAGMA table_info(seen)")]
        if not columns:
            self._create()
        elif "source" not in columns:
            self._migrate_source()
        self._migrate_meta_keys()

    def _create(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen (
                source TEXT NOT NULL,
                code INTEGER NOT NULL,
                posted_at TEXT NOT NULL,
                renewed_at TEXT,
                sent_at TEXT,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                PRIMARY KEY (source, code)
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

    def _migrate_source(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE seen_next (
                    source TEXT NOT NULL,
                    code INTEGER NOT NULL,
                    posted_at TEXT NOT NULL,
                    renewed_at TEXT,
                    sent_at TEXT,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    PRIMARY KEY (source, code)
                )
                """
            )
            self.conn.execute(
                """
                INSERT INTO seen_next (source, code, posted_at, renewed_at, sent_at, title, url)
                SELECT ?, code, posted_at, renewed_at, sent_at, title, url FROM seen
                """,
                (INDEX,),
            )
            self.conn.execute("DROP TABLE seen")
            self.conn.execute("ALTER TABLE seen_next RENAME TO seen")

    def _migrate_meta_keys(self) -> None:
        for key in _META_KEYS:
            row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            if row is None:
                continue
            prefixed = f"{INDEX}:{key}"
            existing = self.conn.execute(
                "SELECT 1 FROM meta WHERE key = ?",
                (prefixed,),
            ).fetchone()
            with self.conn:
                if existing is None:
                    self.conn.execute(
                        "INSERT INTO meta (key, value) VALUES (?, ?)",
                        (prefixed, str(row["value"])),
                    )
                self.conn.execute("DELETE FROM meta WHERE key = ?", (key,))

    def _meta(self, name: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?",
            (self._key(name),),
        ).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def _set_meta(self, name: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (self._key(name), value),
            )

    def _key(self, name: str) -> str:
        return f"{self.source}:{name}"


def _row(source: str, listing: Listing, sent_at: str | None) -> tuple:
    return (
        source,
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
