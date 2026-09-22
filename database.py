from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class MahjongDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS majsoul_bindings (
                    actor_id TEXT NOT NULL,
                    uid TEXT NOT NULL,
                    nickname TEXT,
                    is_main INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (actor_id, uid)
                );
                CREATE INDEX IF NOT EXISTS idx_majsoul_bindings_actor
                    ON majsoul_bindings(actor_id);

                CREATE TABLE IF NOT EXISTS majsoul_subscriptions (
                    session_id TEXT NOT NULL,
                    uid TEXT NOT NULL,
                    nickname TEXT NOT NULL,
                    mode INTEGER NOT NULL CHECK (mode IN (3, 4)),
                    active INTEGER NOT NULL DEFAULT 1,
                    last_uuid TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (session_id, uid, mode)
                );
                CREATE INDEX IF NOT EXISTS idx_majsoul_subscriptions_active
                    ON majsoul_subscriptions(active, mode);

                CREATE TABLE IF NOT EXISTS plugin_secrets (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )

    @staticmethod
    def _now() -> int:
        return int(time.time())

    def list_bindings(self, actor_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT uid, nickname, is_main FROM majsoul_bindings
                WHERE actor_id = ? ORDER BY created_at, rowid
                """,
                (actor_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_main_uid(self, actor_id: str) -> str | None:
        bindings = self.list_bindings(actor_id)
        if not bindings:
            return None
        main = next((item for item in bindings if item["is_main"]), bindings[0])
        return main["uid"]

    def add_binding(self, actor_id: str, uid: str, nickname: str = "") -> bool:
        now = self._now()
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM majsoul_bindings WHERE actor_id = ? AND uid = ?",
                (actor_id, uid),
            ).fetchone()
            if exists:
                return False
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM majsoul_bindings WHERE actor_id = ?",
                (actor_id,),
            ).fetchone()["n"]
            conn.execute(
                """
                INSERT INTO majsoul_bindings
                    (actor_id, uid, nickname, is_main, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (actor_id, uid, nickname or None, int(count == 0), now, now),
            )
        return True

    def set_main_uid(self, actor_id: str, uid: str) -> bool:
        now = self._now()
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM majsoul_bindings WHERE actor_id = ? AND uid = ?",
                (actor_id, uid),
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                "UPDATE majsoul_bindings SET is_main = 0, updated_at = ? WHERE actor_id = ?",
                (now, actor_id),
            )
            conn.execute(
                """
                UPDATE majsoul_bindings SET is_main = 1, updated_at = ?
                WHERE actor_id = ? AND uid = ?
                """,
                (now, actor_id, uid),
            )
        return True

    def remove_binding(self, actor_id: str, uid: str | None = None) -> int:
        with self.connect() as conn:
            if uid is None:
                cursor = conn.execute(
                    "DELETE FROM majsoul_bindings WHERE actor_id = ?", (actor_id,)
                )
                return cursor.rowcount
            was_main = conn.execute(
                """
                SELECT is_main FROM majsoul_bindings
                WHERE actor_id = ? AND uid = ?
                """,
                (actor_id, uid),
            ).fetchone()
            cursor = conn.execute(
                "DELETE FROM majsoul_bindings WHERE actor_id = ? AND uid = ?",
                (actor_id, uid),
            )
            if cursor.rowcount and was_main and was_main["is_main"]:
                first = conn.execute(
                    """
                    SELECT uid FROM majsoul_bindings
                    WHERE actor_id = ? ORDER BY created_at, rowid LIMIT 1
                    """,
                    (actor_id,),
                ).fetchone()
                if first:
                    conn.execute(
                        """
                        UPDATE majsoul_bindings SET is_main = 1, updated_at = ?
                        WHERE actor_id = ? AND uid = ?
                        """,
                        (self._now(), actor_id, first["uid"]),
                    )
            return cursor.rowcount

    def upsert_subscription(
        self,
        session_id: str,
        uid: str,
        nickname: str,
        mode: int,
        last_uuid: str | None,
    ) -> None:
        now = self._now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO majsoul_subscriptions
                    (session_id, uid, nickname, mode, active, last_uuid, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(session_id, uid, mode) DO UPDATE SET
                    nickname = excluded.nickname,
                    active = 1,
                    last_uuid = COALESCE(majsoul_subscriptions.last_uuid, excluded.last_uuid),
                    updated_at = excluded.updated_at
                """,
                (session_id, uid, nickname, mode, last_uuid, now, now),
            )

    def set_subscription_active(
        self, session_id: str, uid: str, mode: int, active: bool
    ) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE majsoul_subscriptions SET active = ?, updated_at = ?
                WHERE session_id = ? AND uid = ? AND mode = ?
                """,
                (int(active), self._now(), session_id, uid, mode),
            )
            return cursor.rowcount == 1

    def delete_subscription(self, session_id: str, uid: str, mode: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM majsoul_subscriptions
                WHERE session_id = ? AND uid = ? AND mode = ?
                """,
                (session_id, uid, mode),
            )
            return cursor.rowcount == 1

    def list_subscriptions(
        self, session_id: str | None = None, mode: int | None = None, active_only=False
    ) -> list[dict]:
        clauses, values = [], []
        if session_id is not None:
            clauses.append("session_id = ?")
            values.append(session_id)
        if mode is not None:
            clauses.append("mode = ?")
            values.append(mode)
        if active_only:
            clauses.append("active = 1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM majsoul_subscriptions" + where + " ORDER BY created_at",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def update_subscription_cursor(
        self, session_id: str, uid: str, mode: int, last_uuid: str
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE majsoul_subscriptions SET last_uuid = ?, updated_at = ?
                WHERE session_id = ? AND uid = ? AND mode = ?
                """,
                (last_uuid, self._now(), session_id, uid, mode),
            )

    def set_secret(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO plugin_secrets(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, self._now()),
            )

    def get_secret(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM plugin_secrets WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

