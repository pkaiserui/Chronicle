"""
Dependency tracking hooks for capturing external calls.

Provides automatic instrumentation for:
- Database calls (SQLAlchemy, psycopg2, sqlite3)
- HTTP calls (requests, httpx, aiohttp) with full payload capture
- File I/O operations
"""

from __future__ import annotations

import functools
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, TypeVar, Union
from unittest.mock import patch

from .capture import CaptureContext


F = TypeVar("F", bound=Callable[..., Any])


# =============================================================================
# HTTP Capture Configuration
# =============================================================================

@dataclass
class HTTPCaptureConfig:
    """
    Configuration for HTTP request/response payload capture.

    Usage:
        from Chronicle.dependencies import HTTPCaptureConfig, configure_http_capture

        config = HTTPCaptureConfig(
            capture_request_body=True,
            capture_response_body=True,
            max_body_size=10000,
            redact_headers=["Authorization", "X-API-Key"],
        )
        configure_http_capture(config)
    """

    # What to capture
    capture_request_body: bool = True
    capture_request_headers: bool = True
    capture_response_body: bool = True
    capture_response_headers: bool = True

    # Size limits (bytes)
    max_body_size: int = 65536  # 64KB default
    max_header_value_size: int = 1000

    # Headers to redact (case-insensitive)
    redact_headers: Set[str] = field(default_factory=lambda: {
        "authorization",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "cookie",
        "set-cookie",
        "x-csrf-token",
        "x-access-token",
        "x-refresh-token",
        "proxy-authorization",
        "www-authenticate",
    })

    # Content types to capture body for (None = all)
    capture_content_types: Optional[Set[str]] = field(default_factory=lambda: {
        "application/json",
        "application/xml",
        "text/plain",
        "text/html",
        "text/xml",
        "application/x-www-form-urlencoded",
    })

    # URL patterns to exclude from capture (e.g., health checks)
    exclude_url_patterns: List[str] = field(default_factory=list)

    def should_capture_content_type(self, content_type: Optional[str]) -> bool:
        """Check if the content type should have its body captured."""
        if self.capture_content_types is None:
            return True
        if not content_type:
            return True  # Capture if no content type specified
        # Check if any allowed type is in the content type string
        content_type_lower = content_type.lower()
        return any(ct in content_type_lower for ct in self.capture_content_types)

    def should_exclude_url(self, url: str) -> bool:
        """Check if the URL should be excluded from capture."""
        url_lower = url.lower()
        return any(pattern.lower() in url_lower for pattern in self.exclude_url_patterns)


# Global HTTP capture configuration
_http_config = HTTPCaptureConfig()


def configure_http_capture(config: HTTPCaptureConfig) -> None:
    """
    Configure HTTP payload capture settings.

    Call this before installing HTTP tracking hooks.

    Usage:
        configure_http_capture(HTTPCaptureConfig(
            max_body_size=100000,
            redact_headers={"Authorization", "X-Custom-Secret"},
        ))
    """
    global _http_config
    _http_config = config


def get_http_config() -> HTTPCaptureConfig:
    """Get the current HTTP capture configuration."""
    return _http_config


# =============================================================================
# HTTP Payload Helpers
# =============================================================================

def _redact_headers(
    headers: Dict[str, str],
    config: HTTPCaptureConfig,
) -> Dict[str, str]:
    """Redact sensitive headers based on configuration."""
    if not headers:
        return {}

    redacted = {}
    redact_set = {h.lower() for h in config.redact_headers}

    for key, value in headers.items():
        if key.lower() in redact_set:
            redacted[key] = "[REDACTED]"
        else:
            # Truncate long header values
            if len(str(value)) > config.max_header_value_size:
                redacted[key] = str(value)[:config.max_header_value_size] + "...[truncated]"
            else:
                redacted[key] = value

    return redacted


def _safe_get_body(
    body: Any,
    content_type: Optional[str],
    config: HTTPCaptureConfig,
) -> Optional[Union[str, dict]]:
    """
    Safely extract and truncate body content.

    Returns the body as a string or parsed JSON dict.
    """
    if body is None:
        return None

    if not config.should_capture_content_type(content_type):
        return f"[Body not captured - content-type: {content_type}]"

    try:
        # Handle bytes
        if isinstance(body, bytes):
            if len(body) > config.max_body_size:
                body_str = body[:config.max_body_size].decode("utf-8", errors="replace")
                return body_str + f"...[truncated, total {len(body)} bytes]"
            body_str = body.decode("utf-8", errors="replace")
        elif isinstance(body, str):
            if len(body) > config.max_body_size:
                return body[:config.max_body_size] + f"...[truncated, total {len(body)} chars]"
            body_str = body
        else:
            # Try to serialize other types
            body_str = str(body)
            if len(body_str) > config.max_body_size:
                return body_str[:config.max_body_size] + "...[truncated]"

        # Try to parse as JSON for better structure
        if content_type and "json" in content_type.lower():
            try:
                return json.loads(body_str)
            except (json.JSONDecodeError, ValueError):
                pass

        return body_str

    except Exception as e:
        return f"[Error capturing body: {str(e)}]"


def _extract_request_body_requests(kwargs: Dict[str, Any]) -> Optional[Any]:
    """Extract request body from requests library kwargs."""
    # requests uses: data, json, files
    if "json" in kwargs and kwargs["json"] is not None:
        return kwargs["json"]
    if "data" in kwargs and kwargs["data"] is not None:
        return kwargs["data"]
    return None


def _get_content_type_from_headers(headers: Optional[Dict[str, str]]) -> Optional[str]:
    """Extract content-type from headers dict (case-insensitive)."""
    if not headers:
        return None
    for key, value in headers.items():
        if key.lower() == "content-type":
            return value
    return None


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
    Monkey-patch requests library to capture HTTP calls with full payload.

    Captures:
    - Request: method, URL, headers, body (JSON/form data)
    - Response: status code, headers, body

    Configure capture settings with configure_http_capture() before calling.

    Usage:
        from Chronicle.dependencies import track_requests, configure_http_capture
        configure_http_capture(HTTPCaptureConfig(max_body_size=100000))
        track_requests()  # Call once at startup
    """
    try:
        import requests
    except ImportError:
        return

    original_request = requests.Session.request

    @functools.wraps(original_request)
    def tracked_request(self, method, url, **kwargs):
        config = get_http_config()
        start = time.perf_counter()

        # Check if URL should be excluded
        if config.should_exclude_url(url):
            return original_request(self, method, url, **kwargs)

        # Capture request details before making the call
        request_data: Dict[str, Any] = {
            "method": method,
            "url": url[:2000],  # Allow longer URLs
            "library": "requests",
        }

        # Capture request headers
        if config.capture_request_headers:
            req_headers = kwargs.get("headers", {})
            if req_headers:
                request_data["request_headers"] = _redact_headers(dict(req_headers), config)

        # Capture request body
        if config.capture_request_body:
            req_body = _extract_request_body_requests(kwargs)
            if req_body is not None:
                content_type = _get_content_type_from_headers(kwargs.get("headers"))
                request_data["request_body"] = _safe_get_body(req_body, content_type, config)

        response = None
        status_code = None
        error_info = None

        try:
            response = original_request(self, method, url, **kwargs)
            status_code = response.status_code
            return response
        except Exception as e:
            error_info = {
                "type": type(e).__name__,
                "message": str(e)[:500],
            }
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            request_data["duration_ms"] = duration_ms
            request_data["status_code"] = status_code

            if error_info:
                request_data["error"] = error_info

            # Capture response details
            if response is not None:
                # Response headers
                if config.capture_response_headers:
                    request_data["response_headers"] = _redact_headers(
                        dict(response.headers), config
                    )

                # Response body
                if config.capture_response_body:
                    content_type = response.headers.get("content-type")
                    try:
                        # Use response.content for bytes, avoiding stream consumption issues
                        response_body = response.content
                        request_data["response_body"] = _safe_get_body(
                            response_body, content_type, config
                        )
                        request_data["response_size"] = len(response_body)
                    except Exception as e:
                        request_data["response_body"] = f"[Error reading response: {e}]"

            ctx = CaptureContext.get_current()
            if ctx:
                ctx.record_dependency("http", request_data)

    requests.Session.request = tracked_request


def track_httpx():
    """
    Monkey-patch httpx library to capture HTTP calls with full payload.

    Captures:
    - Request: method, URL, headers, body
    - Response: status code, headers, body

    Configure capture settings with configure_http_capture() before calling.

    Usage:
        from Chronicle.dependencies import track_httpx, configure_http_capture
        track_httpx()  # Call once at startup
    """
    try:
        import httpx
    except ImportError:
        return

    original_send = httpx.Client.send
    original_async_send = httpx.AsyncClient.send

    def _capture_httpx_request(request, config: HTTPCaptureConfig) -> Dict[str, Any]:
        """Extract request data from httpx Request object."""
        request_data: Dict[str, Any] = {
            "method": request.method,
            "url": str(request.url)[:2000],
            "library": "httpx",
        }

        # Capture request headers
        if config.capture_request_headers:
            request_data["request_headers"] = _redact_headers(
                dict(request.headers), config
            )

        # Capture request body
        if config.capture_request_body:
            content_type = request.headers.get("content-type")
            try:
                # httpx stores body in request.content
                body = request.content
                if body:
                    request_data["request_body"] = _safe_get_body(body, content_type, config)
            except Exception as e:
                request_data["request_body"] = f"[Error reading request body: {e}]"

        return request_data

    def _capture_httpx_response(
        response, request_data: Dict[str, Any], config: HTTPCaptureConfig
    ) -> None:
        """Add response data to request_data dict."""
        # Response headers
        if config.capture_response_headers:
            request_data["response_headers"] = _redact_headers(
                dict(response.headers), config
            )

        # Response body
        if config.capture_response_body:
            content_type = response.headers.get("content-type")
            try:
                # Read response content
                body = response.content
                request_data["response_body"] = _safe_get_body(body, content_type, config)
                request_data["response_size"] = len(body)
            except Exception as e:
                request_data["response_body"] = f"[Error reading response: {e}]"

    @functools.wraps(original_send)
    def tracked_send(self, request, **kwargs):
        config = get_http_config()
        start = time.perf_counter()
        url_str = str(request.url)

        # Check if URL should be excluded
        if config.should_exclude_url(url_str):
            return original_send(self, request, **kwargs)

        # Capture request details
        request_data = _capture_httpx_request(request, config)

        response = None
        status_code = None
        error_info = None

        try:
            response = original_send(self, request, **kwargs)
            status_code = response.status_code
            return response
        except Exception as e:
            error_info = {
                "type": type(e).__name__,
                "message": str(e)[:500],
            }
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            request_data["duration_ms"] = duration_ms
            request_data["status_code"] = status_code

            if error_info:
                request_data["error"] = error_info

            if response is not None:
                _capture_httpx_response(response, request_data, config)

            ctx = CaptureContext.get_current()
            if ctx:
                ctx.record_dependency("http", request_data)

    @functools.wraps(original_async_send)
    async def tracked_async_send(self, request, **kwargs):
        config = get_http_config()
        start = time.perf_counter()
        url_str = str(request.url)

        # Check if URL should be excluded
        if config.should_exclude_url(url_str):
            return await original_async_send(self, request, **kwargs)

        # Capture request details
        request_data = _capture_httpx_request(request, config)
        request_data["async"] = True

        response = None
        status_code = None
        error_info = None

        try:
            response = await original_async_send(self, request, **kwargs)
            status_code = response.status_code
            return response
        except Exception as e:
            error_info = {
                "type": type(e).__name__,
                "message": str(e)[:500],
            }
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            request_data["duration_ms"] = duration_ms
            request_data["status_code"] = status_code

            if error_info:
                request_data["error"] = error_info

            if response is not None:
                _capture_httpx_response(response, request_data, config)

            ctx = CaptureContext.get_current()
            if ctx:
                ctx.record_dependency("http", request_data)

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
