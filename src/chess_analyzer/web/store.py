"""SQLite storage for analysis jobs and their results."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL,
    status TEXT NOT NULL,
    pgn TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    error TEXT,
    state TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS analyses_key ON analyses (key);
CREATE TABLE IF NOT EXISTS puzzle_reviews (
    username TEXT NOT NULL,
    key TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (username, key)
);
"""

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
# Waiting for the user's browser to search positions.
DEVICE = "device"


@dataclass
class Job:
    id: str
    key: str
    status: str
    pgn: str
    done: int
    total: int
    result: dict | None
    error: str | None
    created_at: float
    updated_at: float
    state: dict | None = None


class Store:
    """Thread-safe persistence for analysis jobs.

    Parameters
    ----------
    path : str or Path
        SQLite database file; ``":memory:"`` keeps everything in memory.
    """

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            columns = {r[1] for r in self._db.execute("PRAGMA table_info(analyses)")}
            if "state" not in columns:  # databases from before device analysis
                self._db.execute("ALTER TABLE analyses ADD COLUMN state TEXT")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _row_to_job(self, row: sqlite3.Row | None) -> Job | None:
        if row is None:
            return None
        data = dict(row)
        data["result"] = json.loads(data["result"]) if data["result"] else None
        data["state"] = json.loads(data["state"]) if data["state"] else None
        return Job(**data)

    def create(
        self,
        job_id: str,
        key: str,
        pgn: str,
        total: int,
        status: str = QUEUED,
        state: dict | None = None,
    ) -> Job:
        now = time.time()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO analyses (id, key, status, pgn, total, state,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    key,
                    status,
                    pgn,
                    total,
                    json.dumps(state) if state is not None else None,
                    now,
                    now,
                ),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM analyses WHERE id = ?", (job_id,)
            ).fetchone()
        return self._row_to_job(row)

    def find_by_key(self, key: str) -> Job | None:
        """Return the newest job for ``key`` that has not failed."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM analyses WHERE key = ? AND status != ?"
                " ORDER BY created_at DESC LIMIT 1",
                (key, FAILED),
            ).fetchone()
        return self._row_to_job(row)

    def results_for_player(self, username: str) -> list[tuple[str, dict]]:
        """Return ``(id, result)`` of finished analyses ``username`` played in.

        Games analysed more than once appear once, with the newest result.
        """
        name = username.lower()
        with self._lock:
            rows = self._db.execute(
                "SELECT id, result FROM analyses WHERE status = ? AND ("
                " lower(json_extract(result, '$.headers.White')) = ? OR"
                " lower(json_extract(result, '$.headers.Black')) = ?)"
                " ORDER BY created_at DESC",
                (DONE, name, name),
            ).fetchall()
        results, seen = [], set()
        for row in rows:
            result = json.loads(row["result"])
            moves = " ".join(m["uci"] for m in result.get("moves", []))
            game = result.get("headers", {}).get("Link") or (
                result.get("start_fen"),
                moves,
            )
            if game not in seen:
                seen.add(game)
                results.append((row["id"], result))
        return results[::-1]

    def pending_ids(self) -> list[str]:
        """Return queued and running jobs, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM analyses WHERE status IN (?, ?) ORDER BY created_at",
                (QUEUED, RUNNING),
            ).fetchall()
        return [r["id"] for r in rows]

    def _update(self, job_id: str, **fields) -> None:
        fields["updated_at"] = time.time()
        columns = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, self._db:
            self._db.execute(
                f"UPDATE analyses SET {columns} WHERE id = ?",
                (*fields.values(), job_id),
            )

    def set_running(self, job_id: str) -> None:
        self._update(job_id, status=RUNNING, done=0)

    def set_progress(self, job_id: str, done: int, total: int) -> None:
        self._update(job_id, done=done, total=total)

    def set_done(self, job_id: str, result: dict) -> None:
        self._update(job_id, status=DONE, result=json.dumps(result), state=None)

    def set_state(self, job_id: str, state: dict, done: int, total: int) -> None:
        """Save a device analysis that is still waiting for searches."""
        self._update(job_id, state=json.dumps(state), done=done, total=total)

    def set_failed(self, job_id: str, error: str) -> None:
        self._update(job_id, status=FAILED, error=error)

    def puzzle_reviews(self, username: str) -> dict[str, dict]:
        """Return ``username``'s puzzle reviews by puzzle key.

        See :func:`chess_analyzer.puzzles.review`.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT key, data FROM puzzle_reviews WHERE username = ?",
                (username.lower(),),
            ).fetchall()
        return {row["key"]: json.loads(row["data"]) for row in rows}

    def save_puzzle_review(self, username: str, key: str, data: dict) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO puzzle_reviews (username, key, data,"
                " updated_at) VALUES (?, ?, ?, ?)",
                (username.lower(), key, json.dumps(data), time.time()),
            )
