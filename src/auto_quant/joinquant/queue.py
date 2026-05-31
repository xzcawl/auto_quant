"""SQLite task queue for JoinQuant backtests."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_quant.config import project_root


@dataclass
class Task:
    id: int
    strategy_name: str
    jq_code_path: str
    status: str
    priority: int
    result_json: str | None = None


class TaskQueue:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or project_root() / "storage" / "results.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT NOT NULL,
                    jq_code_path TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    priority INTEGER DEFAULT 0,
                    result_json TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS jq_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    strategy_name TEXT,
                    annual_return REAL,
                    sharpe REAL,
                    max_drawdown REAL,
                    win_rate REAL,
                    raw_json TEXT,
                    created_at TEXT
                )
                """
            )

    def enqueue(self, strategy_name: str, jq_code_path: str, priority: int = 0) -> int:
        now = datetime.now().isoformat()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO tasks (strategy_name, jq_code_path, status, priority, created_at, updated_at) "
                "VALUES (?, ?, 'pending', ?, ?, ?)",
                (strategy_name, jq_code_path, priority, now, now),
            )
            return int(cur.lastrowid)

    def get_task(self, task_id: int) -> Task | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT id, strategy_name, jq_code_path, status, priority, result_json "
                "FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
        return Task(*row) if row else None

    def next_pending(self) -> Task | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT id, strategy_name, jq_code_path, status, priority, result_json "
                "FROM tasks WHERE status='pending' ORDER BY priority DESC, id ASC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return Task(*row)

    def update_status(self, task_id: int, status: str, result_json: str | None = None) -> None:
        now = datetime.now().isoformat()
        with self._conn() as c:
            if result_json is not None:
                c.execute(
                    "UPDATE tasks SET status=?, result_json=?, updated_at=? WHERE id=?",
                    (status, result_json, now, task_id),
                )
            else:
                c.execute(
                    "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                    (status, now, task_id),
                )

    def save_result(self, task_id: int, strategy_name: str, metrics: dict[str, Any]) -> None:
        now = datetime.now().isoformat()
        import json

        with self._conn() as c:
            c.execute(
                "INSERT INTO jq_results (task_id, strategy_name, annual_return, sharpe, max_drawdown, win_rate, raw_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    strategy_name,
                    metrics.get("annual_return"),
                    metrics.get("sharpe"),
                    metrics.get("max_drawdown"),
                    metrics.get("win_rate"),
                    json.dumps(metrics, ensure_ascii=False),
                    now,
                ),
            )
