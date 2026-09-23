"""Persistent exact-page playlist for the Bilibili player."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import BilibiliCandidate


class PlaylistStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS songs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner TEXT NOT NULL,
                    bvid TEXT NOT NULL,
                    cid INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    uploader TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    page_title TEXT NOT NULL,
                    search_title TEXT NOT NULL,
                    UNIQUE(owner, bvid, cid)
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def list(self, owner: str) -> list[BilibiliCandidate]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT bvid, cid, title, uploader, duration_ms,
                          page_title, search_title FROM songs
                   WHERE owner = ? ORDER BY id""",
                (owner,),
            ).fetchall()
        return [BilibiliCandidate(*row) for row in rows]

    def add(self, owner: str, candidate: BilibiliCandidate) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO songs
                   (owner, bvid, cid, title, uploader, duration_ms,
                    page_title, search_title) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    owner, candidate.bvid, candidate.cid, candidate.title,
                    candidate.uploader, candidate.duration_ms,
                    candidate.page_title, candidate.search_title,
                ),
            )
            return cursor.rowcount == 1

    def get(self, owner: str, position: int) -> BilibiliCandidate | None:
        if position < 1:
            return None
        songs = self.list(owner)
        return songs[position - 1] if position <= len(songs) else None

    def remove(self, owner: str, position: int) -> BilibiliCandidate | None:
        candidate = self.get(owner, position)
        if candidate is None:
            return None
        with self._connect() as db:
            db.execute(
                "DELETE FROM songs WHERE owner = ? AND bvid = ? AND cid = ?",
                (owner, candidate.bvid, candidate.cid),
            )
        return candidate
