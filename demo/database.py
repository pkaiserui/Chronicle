"""SQLite database layer for the Task Queue System."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, List, Optional

from .models import Task, TaskCreate, TaskPriority, TaskStatus, TaskUpdate


class TaskDatabase:
    """SQLite-backed task storage."""

    def __init__(self, db_path: str = "demo_tasks.db"):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority TEXT NOT NULL DEFAULT 'medium',
                    payload TEXT DEFAULT '{}',
                    result TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    claimed_at TEXT,
                    completed_at TEXT,
                    claimed_by TEXT,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)
            """)
            conn.commit()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        """Convert a database row to a Task object."""
        return Task(
            id=row["id"],
            title=row["title"],
            description=row["description"] or "",
            status=TaskStatus(row["status"]),
            priority=TaskPriority(row["priority"]),
            payload=json.loads(row["payload"] or "{}"),
            result=json.loads(row["result"]) if row["result"] else None,
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            claimed_at=datetime.fromisoformat(row["claimed_at"]) if row["claimed_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            claimed_by=row["claimed_by"],
            retry_count=row["retry_count"] or 0,
            max_retries=row["max_retries"] or 3,
        )

    def create_task(self, task_create: TaskCreate) -> Task:
        """Create a new task."""
        import uuid

        now = datetime.now(timezone.utc)
        task = Task(
            id=str(uuid.uuid4()),
            title=task_create.title,
            description=task_create.description,
            priority=task_create.priority,
            payload=task_create.payload,
            max_retries=task_create.max_retries,
            created_at=now,
            updated_at=now,
        )

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, title, description, status, priority, payload,
                    result, error_message, created_at, updated_at,
                    claimed_at, completed_at, claimed_by, retry_count, max_retries
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.title,
                    task.description,
                    task.status.value,
                    task.priority.value,
                    json.dumps(task.payload),
                    None,
                    None,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                    None,
                    None,
                    None,
                    task.retry_count,
                    task.max_retries,
                ),
            )
            conn.commit()

        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
            return self._row_to_task(row) if row else None

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        priority: Optional[TaskPriority] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Task]:
        """List tasks with optional filtering."""
        query = "SELECT * FROM tasks WHERE 1=1"
        params: List = []

        if status:
            query += " AND status = ?"
            params.append(status.value)

        if priority:
            query += " AND priority = ?"
            params.append(priority.value)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_task(row) for row in cursor.fetchall()]

    def update_task(self, task_id: str, update: TaskUpdate) -> Optional[Task]:
        """Update a task."""
        task = self.get_task(task_id)
        if not task:
            return None

        updates = []
        params = []

        if update.title is not None:
            updates.append("title = ?")
            params.append(update.title)

        if update.description is not None:
            updates.append("description = ?")
            params.append(update.description)

        if update.priority is not None:
            updates.append("priority = ?")
            params.append(update.priority.value)

        if update.payload is not None:
            updates.append("payload = ?")
            params.append(json.dumps(update.payload))

        if updates:
            updates.append("updated_at = ?")
            params.append(datetime.now(timezone.utc).isoformat())
            params.append(task_id)

            with self._get_connection() as conn:
                conn.execute(
                    f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                conn.commit()

        return self.get_task(task_id)

    def delete_task(self, task_id: str) -> bool:
        """Delete a task."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            return cursor.rowcount > 0

    def claim_task(self, task_id: str, worker_id: str) -> Optional[Task]:
        """Claim a pending task for processing."""
        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = ?, claimed_at = ?, claimed_by = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    TaskStatus.CLAIMED.value,
                    now.isoformat(),
                    worker_id,
                    now.isoformat(),
                    task_id,
                    TaskStatus.PENDING.value,
                ),
            )
            conn.commit()

            if cursor.rowcount == 0:
                return None

        return self.get_task(task_id)

    def claim_next_task(self, worker_id: str, priority: Optional[TaskPriority] = None) -> Optional[Task]:
        """Claim the next available pending task."""
        query = """
            SELECT id FROM tasks
            WHERE status = ?
        """
        params: List = [TaskStatus.PENDING.value]

        if priority:
            query += " AND priority = ?"
            params.append(priority.value)

        # Priority order: critical > high > medium > low
        query += """
            ORDER BY
                CASE priority
                    WHEN 'critical' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                END,
                created_at ASC
            LIMIT 1
        """

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            row = cursor.fetchone()

            if not row:
                return None

            return self.claim_task(row["id"], worker_id)

    def start_processing(self, task_id: str) -> Optional[Task]:
        """Mark a claimed task as processing."""
        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    TaskStatus.PROCESSING.value,
                    now.isoformat(),
                    task_id,
                    TaskStatus.CLAIMED.value,
                ),
            )
            conn.commit()

            if cursor.rowcount == 0:
                return None

        return self.get_task(task_id)

    def complete_task(self, task_id: str, result: Optional[dict] = None) -> Optional[Task]:
        """Mark a task as completed."""
        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = ?, result = ?, completed_at = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    TaskStatus.COMPLETED.value,
                    json.dumps(result) if result else None,
                    now.isoformat(),
                    now.isoformat(),
                    task_id,
                    TaskStatus.PROCESSING.value,
                ),
            )
            conn.commit()

            if cursor.rowcount == 0:
                return None

        return self.get_task(task_id)

    def fail_task(self, task_id: str, error_message: str) -> Optional[Task]:
        """Mark a task as failed."""
        task = self.get_task(task_id)
        if not task:
            return None

        now = datetime.now(timezone.utc)

        # Check if we should retry
        if task.retry_count < task.max_retries:
            new_status = TaskStatus.PENDING
            new_retry_count = task.retry_count + 1
        else:
            new_status = TaskStatus.FAILED
            new_retry_count = task.retry_count

        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = ?, error_message = ?, retry_count = ?,
                    claimed_at = NULL, claimed_by = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_status.value,
                    error_message,
                    new_retry_count,
                    now.isoformat(),
                    task_id,
                ),
            )
            conn.commit()

        return self.get_task(task_id)

    def get_stats(self) -> dict:
        """Get task queue statistics."""
        with self._get_connection() as conn:
            stats = {
                "total": 0,
                "by_status": {},
                "by_priority": {},
                "avg_processing_time_ms": 0,
                "failed_rate": 0,
            }

            # Count by status
            cursor = conn.execute(
                "SELECT status, COUNT(*) as count FROM tasks GROUP BY status"
            )
            for row in cursor.fetchall():
                stats["by_status"][row["status"]] = row["count"]
                stats["total"] += row["count"]

            # Count by priority
            cursor = conn.execute(
                "SELECT priority, COUNT(*) as count FROM tasks GROUP BY priority"
            )
            for row in cursor.fetchall():
                stats["by_priority"][row["priority"]] = row["count"]

            # Calculate failed rate
            if stats["total"] > 0:
                failed = stats["by_status"].get("failed", 0)
                completed = stats["by_status"].get("completed", 0)
                total_finished = failed + completed
                if total_finished > 0:
                    stats["failed_rate"] = round(failed / total_finished * 100, 2)

            return stats

    def clear_all(self) -> int:
        """Clear all tasks. Returns number of deleted tasks."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM tasks")
            conn.commit()
            return cursor.rowcount
