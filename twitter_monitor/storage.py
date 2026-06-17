"""
SQLite-backed storage for the Twitter/X monitoring service.

Replaces the previous per-run JSON file. Provides:
  - Cross-session tweet deduplication (seen_tweets)
  - Full tweet + check history (tweets, checks)
  - Resumable sessions (sessions table stores since_id, config, progress)
  - Monthly quota tracking (quota table) for --budget enforcement

A separate Storage instance (and therefore connection) should be created per
thread; all instances may safely point at the same database file because WAL
mode is enabled and writes are serialised by SQLite.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone

# Serialises the cross-cutting quota updates that several topic threads may
# perform against the same database concurrently.
_quota_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


class Storage:
    def __init__(self, db_path: str = "monitor.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self._create_tables()

    def _create_tables(self) -> None:
        with self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id   TEXT PRIMARY KEY,
                    slug         TEXT,
                    description  TEXT,
                    query        TEXT,
                    full_query   TEXT,
                    config       TEXT,
                    since_id     TEXT,
                    started_at   TEXT,
                    ended_at     TEXT,
                    status       TEXT,
                    summary      TEXT
                );

                CREATE TABLE IF NOT EXISTS checks (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id     TEXT,
                    check_number   INTEGER,
                    checked_at     TEXT,
                    raw_count      INTEGER,
                    displayed_count INTEGER
                );

                CREATE TABLE IF NOT EXISTS tweets (
                    tweet_id     TEXT,
                    session_id   TEXT,
                    check_number INTEGER,
                    author       TEXT,
                    author_name  TEXT,
                    text         TEXT,
                    created_at   TEXT,
                    likes        INTEGER,
                    retweets     INTEGER,
                    replies      INTEGER,
                    url          TEXT,
                    PRIMARY KEY (tweet_id, session_id)
                );

                CREATE TABLE IF NOT EXISTS seen_tweets (
                    tweet_id   TEXT PRIMARY KEY,
                    slug       TEXT,
                    first_seen TEXT
                );

                CREATE TABLE IF NOT EXISTS quota (
                    month       TEXT PRIMARY KEY,
                    tweets_read INTEGER
                );
                """
            )

    # -- Sessions -----------------------------------------------------------

    def create_session(
        self,
        session_id: str,
        slug: str,
        description: str,
        query: str,
        full_query: str,
        config: dict,
    ) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO sessions
                   (session_id, slug, description, query, full_query, config,
                    since_id, started_at, ended_at, status, summary)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, 'running', NULL)""",
                (session_id, slug, description, query, full_query,
                 json.dumps(config), _now()),
            )

    def update_since_id(self, session_id: str, since_id: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE sessions SET since_id = ? WHERE session_id = ?",
                (since_id, session_id),
            )

    def finish_session(self, session_id: str, summary: str | None = None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE sessions SET ended_at = ?, status = 'finished', summary = ? "
                "WHERE session_id = ?",
                (_now(), summary, session_id),
            )

    def get_session(self, session_id: str | None = None) -> dict | None:
        """Return a session by id, or the most recent session if id is None."""
        if session_id:
            row = self.conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # -- Checks & tweets ----------------------------------------------------

    def record_check(
        self,
        session_id: str,
        check_number: int,
        checked_at: str,
        raw_count: int,
        displayed_count: int,
        tweets: list[dict],
    ) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT INTO checks
                   (session_id, check_number, checked_at, raw_count, displayed_count)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, check_number, checked_at, raw_count, displayed_count),
            )
            for t in tweets:
                self.conn.execute(
                    """INSERT OR IGNORE INTO tweets
                       (tweet_id, session_id, check_number, author, author_name,
                        text, created_at, likes, retweets, replies, url)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (t["id"], session_id, check_number, t.get("author"),
                     t.get("author_name"), t.get("text"), t.get("created_at"),
                     t.get("likes", 0), t.get("retweets", 0), t.get("replies", 0),
                     t.get("url")),
                )

    def get_session_tweets(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tweets WHERE session_id = ? ORDER BY check_number, created_at",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Cross-session dedup ------------------------------------------------

    def filter_unseen(self, slug: str, tweets: list[dict]) -> list[dict]:
        """
        Return only tweets whose id has not been recorded before (for this slug),
        and mark the returned tweets as seen. Provides deduplication across
        separate runs, not just within a single session.
        """
        unseen = []
        with self._lock, self.conn:
            for t in tweets:
                row = self.conn.execute(
                    "SELECT 1 FROM seen_tweets WHERE tweet_id = ?", (t["id"],)
                ).fetchone()
                if row is None:
                    unseen.append(t)
                    self.conn.execute(
                        "INSERT OR IGNORE INTO seen_tweets (tweet_id, slug, first_seen) "
                        "VALUES (?, ?, ?)",
                        (t["id"], slug, _now()),
                    )
        return unseen

    # -- Quota --------------------------------------------------------------

    def add_quota_usage(self, count: int) -> int:
        """Record `count` tweet reads against the current month; return the new total."""
        month = _current_month()
        with _quota_lock, self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO quota (month, tweets_read) VALUES (?, ?) "
                "ON CONFLICT(month) DO UPDATE SET tweets_read = tweets_read + ?",
                (month, count, count),
            )
            row = self.conn.execute(
                "SELECT tweets_read FROM quota WHERE month = ?", (month,)
            ).fetchone()
        return row["tweets_read"] if row else count

    def get_quota_usage(self) -> int:
        row = self.conn.execute(
            "SELECT tweets_read FROM quota WHERE month = ?", (_current_month(),)
        ).fetchone()
        return row["tweets_read"] if row else 0

    def close(self) -> None:
        self.conn.close()
