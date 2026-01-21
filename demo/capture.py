"""
Capture module for the Chronicle demo.

Provides decorators and utilities to capture function inputs/outputs,
storing them for later analysis by BehaviorAgent.
"""

from __future__ import annotations

import functools
import json
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

F = TypeVar("F", bound=Callable[..., Any])

# Thread-local storage for capture context
_context_storage = local()


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
    """SQLite storage for captured calls."""

    def __init__(self, db_path: str = "chronicle_captures.db"):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
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
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_captured_function_name
                ON captured_calls(function_name)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_captured_start_time
                ON captured_calls(start_time)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_captured_has_error
                ON captured_calls(has_error)
            """)
            conn.commit()

    def store(self, call: CapturedCall) -> None:
        """Store a captured call."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO captured_calls
                (id, function_name, module, data, start_time, duration_ms, has_error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call.id,
                    call.function_name,
                    call.module,
                    json.dumps(call.to_dict()),
                    call.start_time.isoformat(),
                    call.duration_ms,
                    1 if call.exception else 0,
                    datetime.now(timezone.utc).isoformat(),
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
        query = "SELECT data FROM captured_calls WHERE 1=1"
        params: List[Any] = []

        if function_name:
            query += " AND function_name = ?"
            params.append(function_name)

        if has_error is not None:
            query += " AND has_error = ?"
            params.append(1 if has_error else 0)

        query += " ORDER BY start_time DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            return [CapturedCall.from_dict(json.loads(row[0])) for row in rows]

    def get_stats(self) -> dict:
        """Get capture statistics."""
        with sqlite3.connect(str(self.db_path)) as conn:
            stats = {"total_calls": 0, "by_function": {}, "error_rate": 0.0}

            # Total count
            cursor = conn.execute("SELECT COUNT(*) FROM captured_calls")
            stats["total_calls"] = cursor.fetchone()[0]

            if stats["total_calls"] == 0:
                return stats

            # By function
            cursor = conn.execute("""
                SELECT function_name, COUNT(*) as count
                FROM captured_calls
                GROUP BY function_name
                ORDER BY count DESC
                LIMIT 20
            """)
            stats["by_function"] = {row[0]: row[1] for row in cursor.fetchall()}

            # Error rate
            cursor = conn.execute("""
                SELECT
                    SUM(CASE WHEN has_error = 1 THEN 1 ELSE 0 END) as errors,
                    COUNT(*) as total
                FROM captured_calls
            """)
            row = cursor.fetchone()
            if row[1] > 0:
                stats["error_rate"] = round(row[0] / row[1] * 100, 2)

            # Average duration
            cursor = conn.execute("SELECT AVG(duration_ms) FROM captured_calls")
            avg = cursor.fetchone()[0]
            stats["avg_duration_ms"] = round(avg, 2) if avg else 0.0

            return stats

    def clear(self) -> int:
        """Clear all captured calls."""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute("DELETE FROM captured_calls")
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


def configure_storage(db_path: str) -> CaptureStorage:
    """Configure the capture storage path."""
    global _storage
    _storage = CaptureStorage(db_path)
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
