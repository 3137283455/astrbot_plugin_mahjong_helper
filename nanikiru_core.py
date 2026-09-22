from __future__ import annotations

import json
import random
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Question:
    id: str
    global_id: int
    book: str
    book_question_id: int
    question_image: str
    answer_image: str
    source_question_page: int
    source_answer_page: int


class QuestionStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        payload = json.loads((self.data_dir / "questions.json").read_text(encoding="utf-8"))
        self.questions = [Question(**item) for item in payload]
        self.by_id = {item.global_id: item for item in self.questions}
        expected = list(range(1, len(self.questions) + 1))
        if [item.global_id for item in self.questions] != expected:
            raise ValueError("题库全局编号不连续")
        for item in self.questions:
            for relative in (item.question_image, item.answer_image):
                if not (self.data_dir / relative).is_file():
                    raise FileNotFoundError(f"题库图片不存在: {relative}")

    def get(self, question_id: int) -> Question | None:
        return self.by_id.get(question_id)

    def image_path(self, relative: str) -> Path:
        return (self.data_dir / relative).resolve()


class StateStore:
    def __init__(self, db_path: Path, question_ids: Iterable[int]):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.question_ids = list(question_ids)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    current_question INTEGER,
                    round_number INTEGER NOT NULL DEFAULT 1,
                    remaining_questions TEXT NOT NULL DEFAULT '[]',
                    answer_seen INTEGER NOT NULL DEFAULT 0,
                    auto_enabled INTEGER NOT NULL DEFAULT 0,
                    auto_time TEXT NOT NULL DEFAULT '20:00',
                    last_auto_date TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _fresh_pile(self) -> list[int]:
        pile = list(self.question_ids)
        random.SystemRandom().shuffle(pile)
        return pile

    def _ensure(self, conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO sessions
                    (session_id, remaining_questions, updated_at)
                VALUES (?, ?, ?)
                """,
                (session_id, json.dumps(self._fresh_pile()), self._now()),
            )
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return row

    def draw(self, session_id: str) -> tuple[int, bool]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._ensure(conn, session_id)
            pile = json.loads(row["remaining_questions"])
            new_round = False
            round_number = row["round_number"]
            if not pile:
                pile = self._fresh_pile()
                round_number += 1
                new_round = True
            question_id = pile.pop()
            conn.execute(
                """
                UPDATE sessions SET current_question = ?, round_number = ?,
                    remaining_questions = ?, answer_seen = 0, updated_at = ?
                WHERE session_id = ?
                """,
                (question_id, round_number, json.dumps(pile), self._now(), session_id),
            )
            return question_id, new_round

    def set_current(self, session_id: str, question_id: int) -> None:
        with self._connection() as conn:
            self._ensure(conn, session_id)
            conn.execute(
                """
                UPDATE sessions SET current_question = ?, answer_seen = 0, updated_at = ?
                WHERE session_id = ?
                """,
                (question_id, self._now(), session_id),
            )

    def mark_answer_seen(self, session_id: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE sessions SET answer_seen = 1, updated_at = ? WHERE session_id = ?",
                (self._now(), session_id),
            )

    def get(self, session_id: str) -> dict:
        with self._connection() as conn:
            row = self._ensure(conn, session_id)
            result = dict(row)
            result["remaining_questions"] = json.loads(result["remaining_questions"])
            return result

    def reset(self, session_id: str) -> None:
        with self._connection() as conn:
            row = self._ensure(conn, session_id)
            conn.execute(
                """
                UPDATE sessions SET current_question = NULL, round_number = ?,
                    remaining_questions = ?, answer_seen = 0, updated_at = ?
                WHERE session_id = ?
                """,
                (
                    row["round_number"] + 1,
                    json.dumps(self._fresh_pile()),
                    self._now(),
                    session_id,
                ),
            )

    def set_auto(self, session_id: str, enabled: bool, auto_time: str) -> None:
        with self._connection() as conn:
            self._ensure(conn, session_id)
            conn.execute(
                """
                UPDATE sessions SET auto_enabled = ?, auto_time = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (int(enabled), auto_time, self._now(), session_id),
            )

    def due_auto_sessions(self, date_text: str, time_text: str) -> list[str]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT session_id FROM sessions
                WHERE auto_enabled = 1 AND auto_time = ?
                    AND (last_auto_date IS NULL OR last_auto_date <> ?)
                """,
                (time_text, date_text),
            ).fetchall()
            return [row["session_id"] for row in rows]

    def claim_auto(self, session_id: str, date_text: str) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE sessions SET last_auto_date = ?, updated_at = ?
                WHERE session_id = ? AND auto_enabled = 1
                    AND (last_auto_date IS NULL OR last_auto_date <> ?)
                """,
                (date_text, self._now(), session_id, date_text),
            )
            return cursor.rowcount == 1
