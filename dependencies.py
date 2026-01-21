"""
Dependency tracking hooks for capturing external calls.

Provides automatic instrumentation for:
- Database calls (SQLAlchemy, psycopg2, sqlite3)
- HTTP calls (requests, httpx, aiohttp)
- File I/O operations
"""

from __future__ import annotations

import functools
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, TypeVar
from unittest.mock import patch

from .capture import CaptureContext


F = TypeVar("F", bound=Callable[..., Any])


# =============================================================================
# Database Tracking
# =============================================================================

def track_sqlalchemy():
    """
    Install SQLAlchemy event hooks to capture database queries.
    
    Usage:
        from behaviorflow.dependencies import track_sqlalchemy
        track_sqlalchemy()  # Call once at startup
    """
    try:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine
    except ImportError:
        return
    
    @event.listens_for(Engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("query_start_time", []).append(time.perf_counter())
    
    @event.listens_for(Engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        start_times = conn.info.get("query_start_time", [])
        duration_ms = (time.perf_counter() - start_times.pop()) * 1000 if start_times else 0
        
        ctx = CaptureContext.get_current()
        if ctx:
            ctx.record_dependency("database", {
                "statement": statement[:500],  # Truncate long queries
                "parameters": str(parameters)[:200] if parameters else None,
                "duration_ms": duration_ms,
                "executemany": executemany,
            })


def track_psycopg2():
    """
    Monkey-patch psycopg2 to capture database queries.
    
    Usage:
        from behaviorflow.dependencies import track_psycopg2
        track_psycopg2()  # Call once at startup
    """
    try:
        import psycopg2
        import psycopg2.extensions
    except ImportError:
        return
    
    original_execute = psycopg2.extensions.cursor.execute
    original_executemany = psycopg2.extensions.cursor.executemany
    
    @functools.wraps(original_execute)
    def tracked_execute(self, query, vars=None):
        start = time.perf_counter()
        try:
            result = original_execute(self, query, vars)
            return result
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            ctx = CaptureContext.get_current()
            if ctx:
                ctx.record_dependency("database", {
                    "statement": str(query)[:500],
                    "parameters": str(vars)[:200] if vars else None,
                    "duration_ms": duration_ms,
                    "driver": "psycopg2",
                })
    
    @functools.wraps(original_executemany)
    def tracked_executemany(self, query, vars_list):
        start = time.perf_counter()
        try:
            result = original_executemany(self, query, vars_list)
            return result
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            ctx = CaptureContext.get_current()
            if ctx:
                ctx.record_dependency("database", {
                    "statement": str(query)[:500],
                    "parameters_count": len(vars_list) if vars_list else 0,
                    "duration_ms": duration_ms,
                    "driver": "psycopg2",
                    "executemany": True,
                })
    
    psycopg2.extensions.cursor.execute = tracked_execute
    psycopg2.extensions.cursor.executemany = tracked_executemany


# =============================================================================
# HTTP Tracking
# =============================================================================

def track_requests():
    """
    Monkey-patch requests library to capture HTTP calls.
    
    Usage:
        from behaviorflow.dependencies import track_requests
        track_requests()  # Call once at startup
    """
    try:
        import requests
    except ImportError:
        return
    
    original_request = requests.Session.request
    
    @functools.wraps(original_request)
    def tracked_request(self, method, url, **kwargs):
        start = time.perf_counter()
        try:
            response = original_request(self, method, url, **kwargs)
            status_code = response.status_code
            return response
        except Exception as e:
            status_code = None
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            ctx = CaptureContext.get_current()
            if ctx:
                ctx.record_dependency("http", {
                    "method": method,
                    "url": url[:500],
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "library": "requests",
                })
    
    requests.Session.request = tracked_request


def track_httpx():
    """
    Monkey-patch httpx library to capture HTTP calls.
    
    Usage:
        from behaviorflow.dependencies import track_httpx
        track_httpx()  # Call once at startup
    """
    try:
        import httpx
    except ImportError:
        return
    
    original_send = httpx.Client.send
    original_async_send = httpx.AsyncClient.send
    
    @functools.wraps(original_send)
    def tracked_send(self, request, **kwargs):
        start = time.perf_counter()
        try:
            response = original_send(self, request, **kwargs)
            status_code = response.status_code
            return response
        except Exception as e:
            status_code = None
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            ctx = CaptureContext.get_current()
            if ctx:
                ctx.record_dependency("http", {
                    "method": request.method,
                    "url": str(request.url)[:500],
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "library": "httpx",
                })
    
    @functools.wraps(original_async_send)
    async def tracked_async_send(self, request, **kwargs):
        start = time.perf_counter()
        try:
            response = await original_async_send(self, request, **kwargs)
            status_code = response.status_code
            return response
        except Exception as e:
            status_code = None
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            ctx = CaptureContext.get_current()
            if ctx:
                ctx.record_dependency("http", {
                    "method": request.method,
                    "url": str(request.url)[:500],
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "library": "httpx",
                    "async": True,
                })
    
    httpx.Client.send = tracked_send
    httpx.AsyncClient.send = tracked_async_send


# =============================================================================
# File I/O Tracking
# =============================================================================

_original_open = open


def track_file_io():
    """
    Patch built-in open() to capture file operations.
    
    Usage:
        from behaviorflow.dependencies import track_file_io
        track_file_io()  # Call once at startup
    
    Note: This is a global patch and may affect all code.
    Consider using track_file_context() for more targeted tracking.
    """
    import builtins
    
    @functools.wraps(_original_open)
    def tracked_open(file, mode='r', *args, **kwargs):
        start = time.perf_counter()
        try:
            result = _original_open(file, mode, *args, **kwargs)
            return result
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            ctx = CaptureContext.get_current()
            if ctx:
                ctx.record_dependency("file", {
                    "path": str(file)[:500],
                    "mode": mode,
                    "duration_ms": duration_ms,
                })
    
    builtins.open = tracked_open


@contextmanager
def track_file_context():
    """
    Context manager for tracking file I/O in a specific block.
    
    Usage:
        with track_file_context():
            with open("data.json") as f:
                data = json.load(f)
    """
    import builtins
    original = builtins.open
    
    @functools.wraps(_original_open)
    def tracked_open(file, mode='r', *args, **kwargs):
        start = time.perf_counter()
        try:
            result = _original_open(file, mode, *args, **kwargs)
            return result
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            ctx = CaptureContext.get_current()
            if ctx:
                ctx.record_dependency("file", {
                    "path": str(file)[:500],
                    "mode": mode,
                    "duration_ms": duration_ms,
                })
    
    builtins.open = tracked_open
    try:
        yield
    finally:
        builtins.open = original


# =============================================================================
# Generic Dependency Wrapper
# =============================================================================

def track_dependency(
    dep_type: str,
    name: Optional[str] = None,
) -> Callable[[F], F]:
    """
    Decorator to manually track a dependency call.
    
    Usage:
        @track_dependency("external_api", "payment_gateway")
        def process_payment(amount: float) -> bool:
            # ... call external payment API ...
            return True
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            exception_info = None
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                exception_info = str(e)
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                ctx = CaptureContext.get_current()
                if ctx:
                    ctx.record_dependency(dep_type, {
                        "name": name or func.__name__,
                        "duration_ms": duration_ms,
                        "error": exception_info,
                    })
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.perf_counter()
            exception_info = None
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                exception_info = str(e)
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                ctx = CaptureContext.get_current()
                if ctx:
                    ctx.record_dependency(dep_type, {
                        "name": name or func.__name__,
                        "duration_ms": duration_ms,
                        "error": exception_info,
                        "async": True,
                    })
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return wrapper  # type: ignore
    
    return decorator


# =============================================================================
# All-in-one Setup
# =============================================================================

def install_all_hooks():
    """
    Install all available dependency tracking hooks.
    
    Call this once at application startup to automatically
    capture database, HTTP, and file I/O operations.
    
    Usage:
        from behaviorflow.dependencies import install_all_hooks
        install_all_hooks()
    """
    track_sqlalchemy()
    track_psycopg2()
    track_requests()
    track_httpx()
    # Note: track_file_io() is not included by default due to its global nature
    # Call it explicitly if needed
