import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    topic_id TEXT NOT NULL,
    search_query TEXT,
    topic_title TEXT,
    title TEXT NOT NULL,
    episodes TEXT NOT NULL DEFAULT '[]',
    interval_minutes INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT,
    last_checked_at TEXT,
    last_grabbed_at TEXT,
    next_check_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sonarr_series_id INTEGER,
    sonarr_series_title TEXT,
    sonarr_series_type TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id INTEGER NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_releases_due ON releases(enabled, next_check_at);
CREATE INDEX IF NOT EXISTS idx_history_release ON history(release_id, id DESC);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(releases)")}
            migrations = {
                "sonarr_series_id": "INTEGER",
                "sonarr_series_title": "TEXT",
                "sonarr_series_type": "TEXT",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE releases ADD COLUMN {name} {definition}")

    @staticmethod
    def _release(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        release = dict(row)
        release["enabled"] = bool(release["enabled"])
        release["episodes"] = json.loads(release["episodes"])
        return release

    def list_releases(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM releases ORDER BY id DESC").fetchall()
        return [self._release(row) for row in rows]

    def get_release(self, release_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM releases WHERE id = ?", (release_id,)).fetchone()
        return self._release(row)

    def get_by_url(self, url: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM releases WHERE url = ?", (url,)).fetchone()
        return self._release(row)

    def due_releases(self, now: str, limit: int = 10) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM releases WHERE enabled = 1 AND next_check_at <= ? ORDER BY next_check_at LIMIT ?",
                (now, limit),
            ).fetchall()
        return [self._release(row) for row in rows]

    def create_release(self, data: dict) -> dict:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO releases (
                    url, topic_id, search_query, topic_title, title, episodes,
                    interval_minutes, enabled, last_status, last_error,
                    last_checked_at, last_grabbed_at, next_check_at, created_at, updated_at,
                    sonarr_series_id, sonarr_series_title, sonarr_series_type
                ) VALUES (
                    :url, :topic_id, :search_query, :topic_title, :title, :episodes,
                    :interval_minutes, :enabled, :last_status, :last_error,
                    :last_checked_at, :last_grabbed_at, :next_check_at, :created_at, :updated_at,
                    :sonarr_series_id, :sonarr_series_title, :sonarr_series_type
                )
                """,
                {**data, "episodes": json.dumps(data["episodes"]), "enabled": int(data["enabled"])},
            )
            release_id = cursor.lastrowid
        return self.get_release(release_id)

    def update_release(self, release_id: int, **fields) -> dict | None:
        allowed = {
            "search_query", "topic_title", "title", "episodes", "interval_minutes",
            "enabled", "last_status", "last_error", "last_checked_at",
            "last_grabbed_at", "next_check_at", "updated_at",
            "sonarr_series_id", "sonarr_series_title", "sonarr_series_type",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown release fields: {unknown}")
        if "episodes" in fields:
            fields["episodes"] = json.dumps(fields["episodes"])
        if "enabled" in fields:
            fields["enabled"] = int(fields["enabled"])
        assignments = ", ".join(f"{name} = :{name}" for name in fields)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE releases SET {assignments} WHERE id = :release_id",
                {**fields, "release_id": release_id},
            )
        return self.get_release(release_id)

    def delete_release(self, release_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM releases WHERE id = ?", (release_id,))
        return cursor.rowcount > 0

    def add_history(self, release_id: int, created_at: str, status: str, title: str, message: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO history (release_id, created_at, status, title, message) VALUES (?, ?, ?, ?, ?)",
                (release_id, created_at, status, title, message),
            )
            connection.execute(
                """
                DELETE FROM history WHERE release_id = ? AND id NOT IN (
                    SELECT id FROM history WHERE release_id = ? ORDER BY id DESC LIMIT 100
                )
                """,
                (release_id, release_id),
            )

    def list_history(self, release_id: int, limit: int = 30) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM history WHERE release_id = ? ORDER BY id DESC LIMIT ?",
                (release_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_settings(self) -> dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def save_settings(self, values: dict[str, str]) -> None:
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO settings (key, value) VALUES (:key, :value)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                [{"key": key, "value": value} for key, value in values.items()],
            )
