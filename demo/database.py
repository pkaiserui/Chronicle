"""Database layer for the Task Queue System supporting SQLite and PostgreSQL."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, List, Optional, Union

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

from dotenv import load_dotenv

from .models import Task, TaskCreate, TaskPriority, TaskStatus, TaskUpdate

# Load environment variables
load_dotenv()


def get_db_config() -> dict:
    """Get database configuration from environment variables."""
    db_type = os.getenv("DB_TYPE", "sqlite").lower()
    
    if db_type == "postgres":
        # Check for full DSN first
        dsn = os.getenv("POSTGRES_DSN")
        if dsn:
            return {"type": "postgres", "dsn": dsn}
        
        # Build DSN from individual components
        return {
            "type": "postgres",
            "host": os.getenv("POSTGRES_HOST", "localhost"),
            "port": int(os.getenv("POSTGRES_PORT", "5432")),
            "user": os.getenv("POSTGRES_USER", "chronicle_user"),
            "password": os.getenv("POSTGRES_PASSWORD", "chronicle_password"),
            "dbname": os.getenv("POSTGRES_DB", "chronicle_db"),
        }
    else:
        # SQLite default
        return {
            "type": "sqlite",
            "db_path": os.getenv("SQLITE_TASKS_DB", "demo_tasks.db"),
        }


class TaskDatabase:
    """Task storage supporting both SQLite and PostgreSQL."""

    def __init__(self, db_path: Optional[str] = None, db_config: Optional[dict] = None):
        """
        Initialize the database.
        
        Args:
            db_path: For SQLite, the path to the database file (deprecated, use db_config)
            db_config: Database configuration dict (if None, reads from environment)
        """
        if db_config is None:
            db_config = get_db_config()
        
        self.db_config = db_config
        self.db_type = db_config["type"]
        
        if self.db_type == "postgres":
            if not PSYCOPG2_AVAILABLE:
                raise ImportError(
                    "psycopg2-binary is required for PostgreSQL support. "
                    "Install it with: pip install psycopg2-binary"
                )
            # Store connection parameters
            if "dsn" in db_config:
                self.connection_string = db_config["dsn"]
            else:
                self.connection_string = (
                    f"host={db_config['host']} "
                    f"port={db_config['port']} "
                    f"user={db_config['user']} "
                    f"password={db_config['password']} "
                    f"dbname={db_config['dbname']}"
                )
        else:
            # SQLite
            self.db_path = Path(db_path or db_config.get("db_path", "demo_tasks.db"))
        
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if self.db_type == "postgres":
                # PostgreSQL schema
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        id VARCHAR(255) PRIMARY KEY,
                        title VARCHAR(500) NOT NULL,
                        description TEXT DEFAULT '',
                        status VARCHAR(50) NOT NULL DEFAULT 'pending',
                        priority VARCHAR(50) NOT NULL DEFAULT 'medium',
                        payload TEXT DEFAULT '{}',
                        result TEXT,
                        error_message TEXT,
                        created_at TIMESTAMP NOT NULL,
                        updated_at TIMESTAMP NOT NULL,
                        claimed_at TIMESTAMP,
                        completed_at TIMESTAMP,
                        claimed_by VARCHAR(255),
                        retry_count INTEGER DEFAULT 0,
                        max_retries INTEGER DEFAULT 3
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)
                """)
            else:
                # SQLite schema
                cursor.execute("""
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
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)
                """)
            
            conn.commit()

    @contextmanager
    def _get_connection(self) -> Generator[Any, None, None]:
        """Get a database connection."""
        if self.db_type == "postgres":
            conn = psycopg2.connect(self.connection_string)
            try:
                yield conn
            finally:
                conn.close()
        else:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

    def _get_param_placeholder(self) -> str:
        """Get the parameter placeholder for the current database type."""
        return "%s" if self.db_type == "postgres" else "?"

    def _row_to_task(self, row: Any) -> Task:
        """Convert a database row to a Task object."""
        # Handle both sqlite3.Row and psycopg2 RealDictRow
        if hasattr(row, "keys"):
            row_dict = dict(row)
        else:
            # Fallback for tuple rows
            row_dict = {
                "id": row[0], "title": row[1], "description": row[2],
                "status": row[3], "priority": row[4], "payload": row[5],
                "result": row[6], "error_message": row[7],
                "created_at": row[8], "updated_at": row[9],
                "claimed_at": row[10], "completed_at": row[11],
                "claimed_by": row[12], "retry_count": row[13],
                "max_retries": row[14],
            }
        
        # Parse datetime fields
        def parse_datetime(value: Any) -> Optional[datetime]:
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            return None
        
        return Task(
            id=row_dict["id"],
            title=row_dict["title"],
            description=row_dict.get("description") or "",
            status=TaskStatus(row_dict["status"]),
            priority=TaskPriority(row_dict["priority"]),
            payload=json.loads(row_dict.get("payload") or "{}"),
            result=json.loads(row_dict["result"]) if row_dict.get("result") else None,
            error_message=row_dict.get("error_message"),
            created_at=parse_datetime(row_dict["created_at"]),
            updated_at=parse_datetime(row_dict["updated_at"]),
            claimed_at=parse_datetime(row_dict.get("claimed_at")),
            completed_at=parse_datetime(row_dict.get("completed_at")),
            claimed_by=row_dict.get("claimed_by"),
            retry_count=row_dict.get("retry_count") or 0,
            max_retries=row_dict.get("max_retries") or 3,
        )

    def create_task(self, task_create: TaskCreate) -> Task:
        """Create a new task."""
        import uuid

        now = datetime.utcnow()
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

        param_placeholder = self._get_param_placeholder()
        created_at_str = now.isoformat() if self.db_type == "sqlite" else now
        updated_at_str = now.isoformat() if self.db_type == "sqlite" else now

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                INSERT INTO tasks (
                    id, title, description, status, priority, payload,
                    result, error_message, created_at, updated_at,
                    claimed_at, completed_at, claimed_by, retry_count, max_retries
                ) VALUES ({', '.join([param_placeholder] * 15)})
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
                    created_at_str,
                    updated_at_str,
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
        param_placeholder = self._get_param_placeholder()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if self.db_type == "postgres":
                cursor.execute(
                    f"SELECT * FROM tasks WHERE id = {param_placeholder}",
                    (task_id,)
                )
            else:
                cursor.execute(f"SELECT * FROM tasks WHERE id = {param_placeholder}", (task_id,))
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
        param_placeholder = self._get_param_placeholder()
        query = "SELECT * FROM tasks WHERE 1=1"
        params: List = []

        if status:
            query += f" AND status = {param_placeholder}"
            params.append(status.value)

        if priority:
            query += f" AND priority = {param_placeholder}"
            params.append(priority.value)

        query += f" ORDER BY created_at DESC LIMIT {param_placeholder} OFFSET {param_placeholder}"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_task(row) for row in rows]

    def update_task(self, task_id: str, update: TaskUpdate) -> Optional[Task]:
        """Update a task."""
        task = self.get_task(task_id)
        if not task:
            return None

        param_placeholder = self._get_param_placeholder()
        updates = []
        params = []

        if update.title is not None:
            updates.append(f"title = {param_placeholder}")
            params.append(update.title)

        if update.description is not None:
            updates.append(f"description = {param_placeholder}")
            params.append(update.description)

        if update.priority is not None:
            updates.append(f"priority = {param_placeholder}")
            params.append(update.priority.value)

        if update.payload is not None:
            updates.append(f"payload = {param_placeholder}")
            params.append(json.dumps(update.payload))

        if updates:
            updated_at = datetime.utcnow()
            updated_at_str = updated_at.isoformat() if self.db_type == "sqlite" else updated_at
            updates.append(f"updated_at = {param_placeholder}")
            params.append(updated_at_str)
            params.append(task_id)

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"UPDATE tasks SET {', '.join(updates)} WHERE id = {param_placeholder}",
                    params,
                )
                conn.commit()

        return self.get_task(task_id)

    def delete_task(self, task_id: str) -> bool:
        """Delete a task."""
        param_placeholder = self._get_param_placeholder()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM tasks WHERE id = {param_placeholder}", (task_id,))
            conn.commit()
            return cursor.rowcount > 0

    def claim_task(self, task_id: str, worker_id: str) -> Optional[Task]:
        """Claim a pending task for processing."""
        now = datetime.utcnow()
        now_str = now.isoformat() if self.db_type == "sqlite" else now
        param_placeholder = self._get_param_placeholder()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE tasks
                SET status = {param_placeholder}, claimed_at = {param_placeholder}, 
                    claimed_by = {param_placeholder}, updated_at = {param_placeholder}
                WHERE id = {param_placeholder} AND status = {param_placeholder}
                """,
                (
                    TaskStatus.CLAIMED.value,
                    now_str,
                    worker_id,
                    now_str,
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
        param_placeholder = self._get_param_placeholder()
        query = f"""
            SELECT id FROM tasks
            WHERE status = {param_placeholder}
        """
        params: List = [TaskStatus.PENDING.value]

        if priority:
            query += f" AND priority = {param_placeholder}"
            params.append(priority.value)

        # Priority order: critical > high > medium > low
        query += f"""
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
            cursor = conn.cursor()
            cursor.execute(query, params)
            row = cursor.fetchone()

            if not row:
                return None

            task_id = row[0] if isinstance(row, (list, tuple)) else row["id"]
            return self.claim_task(task_id, worker_id)

    def start_processing(self, task_id: str) -> Optional[Task]:
        """Mark a claimed task as processing."""
        now = datetime.utcnow()
        now_str = now.isoformat() if self.db_type == "sqlite" else now
        param_placeholder = self._get_param_placeholder()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE tasks
                SET status = {param_placeholder}, updated_at = {param_placeholder}
                WHERE id = {param_placeholder} AND status = {param_placeholder}
                """,
                (
                    TaskStatus.PROCESSING.value,
                    now_str,
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
        now = datetime.utcnow()
        now_str = now.isoformat() if self.db_type == "sqlite" else now
        param_placeholder = self._get_param_placeholder()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE tasks
                SET status = {param_placeholder}, result = {param_placeholder}, 
                    completed_at = {param_placeholder}, updated_at = {param_placeholder}
                WHERE id = {param_placeholder} AND status = {param_placeholder}
                """,
                (
                    TaskStatus.COMPLETED.value,
                    json.dumps(result) if result else None,
                    now_str,
                    now_str,
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

        now = datetime.utcnow()
        now_str = now.isoformat() if self.db_type == "sqlite" else now
        param_placeholder = self._get_param_placeholder()

        # Check if we should retry
        if task.retry_count < task.max_retries:
            new_status = TaskStatus.PENDING
            new_retry_count = task.retry_count + 1
        else:
            new_status = TaskStatus.FAILED
            new_retry_count = task.retry_count

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE tasks
                SET status = {param_placeholder}, error_message = {param_placeholder}, 
                    retry_count = {param_placeholder},
                    claimed_at = NULL, claimed_by = NULL, updated_at = {param_placeholder}
                WHERE id = {param_placeholder}
                """,
                (
                    new_status.value,
                    error_message,
                    new_retry_count,
                    now_str,
                    task_id,
                ),
            )
            conn.commit()

        return self.get_task(task_id)

    def get_stats(self) -> dict:
        """Get task queue statistics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            stats = {
                "total": 0,
                "by_status": {},
                "by_priority": {},
                "avg_processing_time_ms": 0,
                "failed_rate": 0,
            }

            # Count by status
            cursor.execute("SELECT status, COUNT(*) as count FROM tasks GROUP BY status")
            for row in cursor.fetchall():
                status = row[0] if isinstance(row, (list, tuple)) else row["status"]
                count = row[1] if isinstance(row, (list, tuple)) else row["count"]
                stats["by_status"][status] = count
                stats["total"] += count

            # Count by priority
            cursor.execute("SELECT priority, COUNT(*) as count FROM tasks GROUP BY priority")
            for row in cursor.fetchall():
                priority = row[0] if isinstance(row, (list, tuple)) else row["priority"]
                count = row[1] if isinstance(row, (list, tuple)) else row["count"]
                stats["by_priority"][priority] = count

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
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tasks")
            conn.commit()
            return cursor.rowcount
