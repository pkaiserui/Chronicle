"""
Capture module for the Chronicle demo.

Provides decorators and utilities to capture function inputs/outputs,
storing them for later analysis by BehaviorAgent.
"""

from __future__ import annotations

import functools
import json
import os
import sqlite3
import time
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import local
from typing import Any, Callable, Dict, List, Optional, TypeVar

try:
    import psycopg2
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

from dotenv import load_dotenv

F = TypeVar("F", bound=Callable[..., Any])

# Load environment variables
load_dotenv()

# Thread-local storage for capture context
_context_storage = local()


def get_capture_db_config() -> dict:
    """Get capture database configuration from environment variables."""
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
            "db_path": os.getenv("SQLITE_CAPTURES_DB", "chronicle_captures.db"),
        }


@dataclass
class CapturedCall:
    """A captured function call with inputs, outputs, and metadata."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    function_name: str = ""
    module: str = ""
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    result: Any = None
    exception: Optional[str] = None
    exception_type: Optional[str] = None
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = None
    duration_ms: float = 0.0
    dependencies: List[Dict[str, Any]] = field(default_factory=list)
    trace_id: Optional[str] = None
    span_id: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""

        def serialize(obj: Any) -> Any:
            """Serialize objects for JSON storage."""
            if obj is None:
                return None
            if isinstance(obj, (str, int, float, bool)):
                return obj
            if isinstance(obj, (list, tuple)):
                return [serialize(item) for item in obj]
            if isinstance(obj, dict):
                return {str(k): serialize(v) for k, v in obj.items()}
            if isinstance(obj, datetime):
                return obj.isoformat()
            if hasattr(obj, "to_dict"):
                return obj.to_dict()
            if hasattr(obj, "__dict__"):
                return serialize(obj.__dict__)
            return str(obj)

        return {
            "id": self.id,
            "function_name": self.function_name,
            "module": self.module,
            "args": serialize(self.args),
            "kwargs": serialize(self.kwargs),
            "result": serialize(self.result),
            "exception": self.exception,
            "exception_type": self.exception_type,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": self.duration_ms,
            "dependencies": self.dependencies,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CapturedCall":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            function_name=data["function_name"],
            module=data.get("module", ""),
            args=tuple(data.get("args", [])),
            kwargs=data.get("kwargs", {}),
            result=data.get("result"),
            exception=data.get("exception"),
            exception_type=data.get("exception_type"),
            start_time=datetime.fromisoformat(data["start_time"]),
            end_time=datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None,
            duration_ms=data.get("duration_ms", 0.0),
            dependencies=data.get("dependencies", []),
            trace_id=data.get("trace_id"),
            span_id=data.get("span_id"),
        )


class CaptureContext:
    """Context for capturing function calls and dependencies."""

    def __init__(self, call: CapturedCall):
        self.call = call
        self._parent: Optional[CaptureContext] = None

    def record_dependency(self, dep_type: str, details: Dict[str, Any]) -> None:
        """Record an external dependency call."""
        self.call.dependencies.append({
            "type": dep_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **details,
        })

    @classmethod
    def get_current(cls) -> Optional["CaptureContext"]:
        """Get the current capture context."""
        return getattr(_context_storage, "current_context", None)

    @classmethod
    def set_current(cls, ctx: Optional["CaptureContext"]) -> None:
        """Set the current capture context."""
        _context_storage.current_context = ctx


class CaptureStorage:
    """Storage for captured calls supporting both SQLite and PostgreSQL."""

    def __init__(self, db_path: Optional[str] = None, db_config: Optional[dict] = None):
        """
        Initialize the capture storage.
        
        Args:
            db_path: For SQLite, the path to the database file (deprecated, use db_config)
            db_config: Database configuration dict (if None, reads from environment)
        """
        if db_config is None:
            db_config = get_capture_db_config()
        
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
            self.db_path = Path(db_path or db_config.get("db_path", "chronicle_captures.db"))
        
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if self.db_type == "postgres":
                # PostgreSQL schema
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS captured_calls (
                        id VARCHAR(255) PRIMARY KEY,
                        function_name VARCHAR(500) NOT NULL,
                        module VARCHAR(500),
                        data TEXT NOT NULL,
                        start_time TIMESTAMP NOT NULL,
                        duration_ms REAL,
                        has_error INTEGER DEFAULT 0,
                        created_at TIMESTAMP NOT NULL
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_captured_function_name
                    ON captured_calls(function_name)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_captured_start_time
                    ON captured_calls(start_time)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_captured_has_error
                    ON captured_calls(has_error)
                """)
            else:
                # SQLite schema
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS captured_calls (
                        id TEXT PRIMARY KEY,
                        function_name TEXT NOT NULL,
                        module TEXT,
                        data TEXT NOT NULL,
                        start_time TEXT NOT NULL,
                        duration_ms REAL,
                        has_error INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_captured_function_name
                    ON captured_calls(function_name)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_captured_start_time
                    ON captured_calls(start_time)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_captured_has_error
                    ON captured_calls(has_error)
                """)
            
            conn.commit()

    @contextmanager
    def _get_connection(self):
        """Get a database connection."""
        if self.db_type == "postgres":
            conn = psycopg2.connect(self.connection_string)
            try:
                yield conn
            finally:
                conn.close()
        else:
            conn = sqlite3.connect(str(self.db_path))
            try:
                yield conn
            finally:
                conn.close()

    def _get_param_placeholder(self) -> str:
        """Get the parameter placeholder for the current database type."""
        return "%s" if self.db_type == "postgres" else "?"

    def store(self, call: CapturedCall) -> None:
        """Store a captured call."""
        param_placeholder = self._get_param_placeholder()
        start_time = call.start_time
        start_time_str = start_time.isoformat() if self.db_type == "sqlite" else start_time
        created_at = datetime.now(timezone.utc)
        created_at_str = created_at.isoformat() if self.db_type == "sqlite" else created_at
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                INSERT INTO captured_calls
                (id, function_name, module, data, start_time, duration_ms, has_error, created_at)
                VALUES ({', '.join([param_placeholder] * 8)})
                """,
                (
                    call.id,
                    call.function_name,
                    call.module,
                    json.dumps(call.to_dict()),
                    start_time_str,
                    call.duration_ms,
                    1 if call.exception else 0,
                    created_at_str,
                ),
            )
            conn.commit()

    def get_calls(
        self,
        function_name: Optional[str] = None,
        has_error: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[CapturedCall]:
        """Retrieve captured calls."""
        param_placeholder = self._get_param_placeholder()
        query = "SELECT data FROM captured_calls WHERE 1=1"
        params: List[Any] = []

        if function_name:
            query += f" AND function_name = {param_placeholder}"
            params.append(function_name)

        if has_error is not None:
            query += f" AND has_error = {param_placeholder}"
            params.append(1 if has_error else 0)

        query += f" ORDER BY start_time DESC LIMIT {param_placeholder} OFFSET {param_placeholder}"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [CapturedCall.from_dict(json.loads(row[0])) for row in rows]

    def get_stats(self) -> dict:
        """Get capture statistics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            stats = {"total_calls": 0, "by_function": {}, "error_rate": 0.0}

            # Total count
            cursor.execute("SELECT COUNT(*) FROM captured_calls")
            row = cursor.fetchone()
            stats["total_calls"] = row[0] if isinstance(row, (list, tuple)) else row["count"]

            if stats["total_calls"] == 0:
                return stats

            # By function
            cursor.execute("""
                SELECT function_name, COUNT(*) as count
                FROM captured_calls
                GROUP BY function_name
                ORDER BY count DESC
                LIMIT 20
            """)
            for row in cursor.fetchall():
                func_name = row[0] if isinstance(row, (list, tuple)) else row["function_name"]
                count = row[1] if isinstance(row, (list, tuple)) else row["count"]
                stats["by_function"][func_name] = count

            # Error rate
            cursor.execute("""
                SELECT
                    SUM(CASE WHEN has_error = 1 THEN 1 ELSE 0 END) as errors,
                    COUNT(*) as total
                FROM captured_calls
            """)
            row = cursor.fetchone()
            if isinstance(row, (list, tuple)):
                errors, total = row[0], row[1]
            else:
                errors, total = row["errors"], row["total"]
            
            if total > 0:
                stats["error_rate"] = round(errors / total * 100, 2)

            # Average duration
            cursor.execute("SELECT AVG(duration_ms) FROM captured_calls")
            row = cursor.fetchone()
            avg = row[0] if isinstance(row, (list, tuple)) else row.get("avg", 0)
            stats["avg_duration_ms"] = round(avg, 2) if avg else 0.0

            return stats

    def clear(self) -> int:
        """Clear all captured calls."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM captured_calls")
            conn.commit()
            return cursor.rowcount


# Global storage instance
_storage: Optional[CaptureStorage] = None


def get_storage() -> CaptureStorage:
    """Get the global capture storage instance."""
    global _storage
    if _storage is None:
        _storage = CaptureStorage()
    return _storage


def configure_storage(db_path: Optional[str] = None, db_config: Optional[dict] = None) -> CaptureStorage:
    """Configure the capture storage path or config."""
    global _storage
    _storage = CaptureStorage(db_path=db_path, db_config=db_config)
    return _storage


def capture(func: F) -> F:
    """
    Decorator to capture function inputs and outputs.

    Usage:
        @capture
        def my_function(x, y):
            return x + y
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Get OpenTelemetry context if available
        trace_id = None
        span_id = None
        try:
            from opentelemetry import trace

            current_span = trace.get_current_span()
            if current_span:
                ctx = current_span.get_span_context()
                if ctx.is_valid:
                    trace_id = format(ctx.trace_id, "032x")
                    span_id = format(ctx.span_id, "016x")
        except ImportError:
            pass

        call = CapturedCall(
            function_name=func.__name__,
            module=func.__module__,
            args=args,
            kwargs=kwargs,
            trace_id=trace_id,
            span_id=span_id,
        )

        # Set up context
        ctx = CaptureContext(call)
        parent = CaptureContext.get_current()
        ctx._parent = parent
        CaptureContext.set_current(ctx)

        start = time.perf_counter()
        try:
            result = func(*args, **kwargs)
            call.result = result
            return result
        except Exception as e:
            call.exception = str(e)
            call.exception_type = type(e).__name__
            raise
        finally:
            call.duration_ms = (time.perf_counter() - start) * 1000
            call.end_time = datetime.now(timezone.utc)

            # Store the call
            try:
                get_storage().store(call)
            except Exception:
                pass  # Don't fail the function if capture fails

            # Restore parent context
            CaptureContext.set_current(parent)

    return wrapper  # type: ignore


@contextmanager
def capture_context(name: str):
    """
    Context manager for capturing a block of code.

    Usage:
        with capture_context("process_order"):
            # ... code to capture ...
    """
    call = CapturedCall(function_name=name, module="__context__")
    ctx = CaptureContext(call)
    parent = CaptureContext.get_current()
    ctx._parent = parent
    CaptureContext.set_current(ctx)

    start = time.perf_counter()
    try:
        yield ctx
    except Exception as e:
        call.exception = str(e)
        call.exception_type = type(e).__name__
        raise
    finally:
        call.duration_ms = (time.perf_counter() - start) * 1000
        call.end_time = datetime.now(timezone.utc)

        try:
            get_storage().store(call)
        except Exception:
            pass

        CaptureContext.set_current(parent)
