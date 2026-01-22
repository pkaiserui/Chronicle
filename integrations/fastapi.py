"""
FastAPI middleware for automatic Chronicle capture.

Provides zero-config request/response capture for FastAPI applications.

Usage:
    from fastapi import FastAPI
    from Chronicle.integrations import ChronicleMiddleware

    app = FastAPI()
    app.add_middleware(ChronicleMiddleware)

With configuration:
    from Chronicle.integrations import (
        ChronicleMiddleware,
        configure_sampling,
        SamplingConfig,
        SamplingStrategy,
    )

    configure_sampling(SamplingConfig(
        strategy=SamplingStrategy.CLUSTERING,
        base_rate=0.1,
    ))

    app.add_middleware(
        ChronicleMiddleware,
        capture_request_body=True,
        capture_response_body=True,
    )
"""

from __future__ import annotations

import json
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Union

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Match

from .sampling import Sampler, get_sampler

# Import type limiter if available (lazy import to avoid circular deps)
def _get_type_limiter():
    try:
        from .ui import get_type_limiter
        return get_type_limiter()
    except ImportError:
        return None


@dataclass
class CaptureConfig:
    """Configuration for the Chronicle middleware."""

    # What to capture
    capture_request_body: bool = True
    capture_request_headers: bool = True
    capture_response_body: bool = True
    capture_response_headers: bool = True
    capture_query_params: bool = True
    capture_path_params: bool = True

    # Size limits
    max_request_body_size: int = 65536  # 64KB
    max_response_body_size: int = 65536  # 64KB
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
    })

    # Fields to redact in request/response bodies
    redact_body_fields: Set[str] = field(default_factory=lambda: {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "credit_card",
        "ssn",
        "social_security",
    })

    # Content types to capture body for
    capture_content_types: Set[str] = field(default_factory=lambda: {
        "application/json",
        "application/xml",
        "text/plain",
        "text/html",
        "text/xml",
        "application/x-www-form-urlencoded",
    })


@dataclass
class CapturedRequest:
    """A captured HTTP request with full details."""

    id: str
    timestamp: datetime
    method: str
    path: str
    full_url: str

    # Request details
    query_params: Optional[Dict[str, Any]] = None
    path_params: Optional[Dict[str, Any]] = None
    request_headers: Optional[Dict[str, str]] = None
    request_body: Optional[Union[str, dict]] = None
    request_body_size: int = 0

    # Response details
    status_code: Optional[int] = None
    response_headers: Optional[Dict[str, str]] = None
    response_body: Optional[Union[str, dict]] = None
    response_body_size: int = 0

    # Timing
    duration_ms: float = 0.0

    # Context
    route_name: Optional[str] = None
    client_ip: Optional[str] = None
    user_agent: Optional[str] = None

    # Error info
    error: Optional[Dict[str, str]] = None

    # Sampling info
    sampled: bool = True
    sampling_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "method": self.method,
            "path": self.path,
            "full_url": self.full_url,
            "query_params": self.query_params,
            "path_params": self.path_params,
            "request_headers": self.request_headers,
            "request_body": self.request_body,
            "request_body_size": self.request_body_size,
            "status_code": self.status_code,
            "response_headers": self.response_headers,
            "response_body": self.response_body,
            "response_body_size": self.response_body_size,
            "duration_ms": self.duration_ms,
            "route_name": self.route_name,
            "client_ip": self.client_ip,
            "user_agent": self.user_agent,
            "error": self.error,
            "sampled": self.sampled,
            "sampling_reason": self.sampling_reason,
        }


# Storage for captured requests (in-memory by default)
_captured_requests: List[CapturedRequest] = []
_max_stored_requests: int = 10000

# Callbacks for custom storage
_capture_callbacks: List[Callable[[CapturedRequest], None]] = []


def add_capture_callback(callback: Callable[[CapturedRequest], None]) -> None:
    """
    Add a callback to be invoked for each captured request.

    Use this to integrate with custom storage backends.

    Usage:
        def store_to_database(captured: CapturedRequest):
            db.insert(captured.to_dict())

        add_capture_callback(store_to_database)
    """
    _capture_callbacks.append(callback)


def get_captured_requests(
    limit: int = 100,
    method: Optional[str] = None,
    path_prefix: Optional[str] = None,
    status_code: Optional[int] = None,
    has_error: Optional[bool] = None,
) -> List[CapturedRequest]:
    """
    Retrieve captured requests with optional filtering.

    Returns most recent first.
    """
    results = _captured_requests.copy()

    if method:
        results = [r for r in results if r.method == method.upper()]

    if path_prefix:
        results = [r for r in results if r.path.startswith(path_prefix)]

    if status_code is not None:
        results = [r for r in results if r.status_code == status_code]

    if has_error is not None:
        if has_error:
            results = [r for r in results if r.error is not None]
        else:
            results = [r for r in results if r.error is None]

    # Most recent first
    results.sort(key=lambda r: r.timestamp, reverse=True)

    return results[:limit]


def clear_captured_requests() -> int:
    """Clear all captured requests. Returns count cleared."""
    global _captured_requests
    count = len(_captured_requests)
    _captured_requests = []
    return count


def _store_captured_request(captured: CapturedRequest) -> None:
    """Store a captured request."""
    global _captured_requests

    # Add to in-memory storage
    _captured_requests.append(captured)

    # Trim if over limit
    if len(_captured_requests) > _max_stored_requests:
        _captured_requests = _captured_requests[-_max_stored_requests:]

    # Call custom callbacks
    for callback in _capture_callbacks:
        try:
            callback(captured)
        except Exception:
            pass  # Don't let callback errors break the middleware


class ChronicleMiddleware(BaseHTTPMiddleware):
    """
    FastAPI/Starlette middleware for automatic request/response capture.

    Captures full request and response details with configurable
    sampling, redaction, and size limits.
    """

    def __init__(
        self,
        app,
        config: Optional[CaptureConfig] = None,
        sampler: Optional[Sampler] = None,
        # Convenience kwargs that override config
        capture_request_body: bool = True,
        capture_response_body: bool = True,
        capture_request_headers: bool = True,
        capture_response_headers: bool = True,
        max_body_size: int = 65536,
        redact_headers: Optional[Set[str]] = None,
    ):
        super().__init__(app)

        self.config = config or CaptureConfig()
        self.sampler = sampler

        # Apply convenience overrides
        self.config.capture_request_body = capture_request_body
        self.config.capture_response_body = capture_response_body
        self.config.capture_request_headers = capture_request_headers
        self.config.capture_response_headers = capture_response_headers
        self.config.max_request_body_size = max_body_size
        self.config.max_response_body_size = max_body_size

        if redact_headers:
            self.config.redact_headers = redact_headers

    def _get_sampler(self) -> Sampler:
        """Get the sampler to use."""
        return self.sampler or get_sampler()

    def _redact_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        """Redact sensitive headers."""
        redacted = {}
        redact_set = {h.lower() for h in self.config.redact_headers}

        for key, value in headers.items():
            if key.lower() in redact_set:
                redacted[key] = "[REDACTED]"
            elif len(str(value)) > self.config.max_header_value_size:
                redacted[key] = str(value)[:self.config.max_header_value_size] + "...[truncated]"
            else:
                redacted[key] = value

        return redacted

    def _redact_body_fields(self, body: Any) -> Any:
        """Recursively redact sensitive fields in body."""
        if isinstance(body, dict):
            redacted = {}
            redact_set = {f.lower() for f in self.config.redact_body_fields}

            for key, value in body.items():
                if key.lower() in redact_set:
                    redacted[key] = "[REDACTED]"
                else:
                    redacted[key] = self._redact_body_fields(value)

            return redacted

        elif isinstance(body, list):
            return [self._redact_body_fields(item) for item in body]

        return body

    def _should_capture_content_type(self, content_type: Optional[str]) -> bool:
        """Check if content type should have body captured."""
        if not content_type:
            return True
        content_type_lower = content_type.lower()
        return any(ct in content_type_lower for ct in self.config.capture_content_types)

    async def _get_request_body(self, request: Request) -> tuple[Optional[Union[str, dict]], int]:
        """Extract and process request body."""
        if not self.config.capture_request_body:
            return None, 0

        content_type = request.headers.get("content-type", "")

        if not self._should_capture_content_type(content_type):
            return f"[Body not captured - content-type: {content_type}]", 0

        try:
            body_bytes = await request.body()
            body_size = len(body_bytes)

            if body_size == 0:
                return None, 0

            if body_size > self.config.max_request_body_size:
                truncated = body_bytes[:self.config.max_request_body_size].decode("utf-8", errors="replace")
                return truncated + f"...[truncated, total {body_size} bytes]", body_size

            body_str = body_bytes.decode("utf-8", errors="replace")

            # Try to parse as JSON
            if "json" in content_type.lower():
                try:
                    body_dict = json.loads(body_str)
                    return self._redact_body_fields(body_dict), body_size
                except (json.JSONDecodeError, ValueError):
                    pass

            return body_str, body_size

        except Exception as e:
            return f"[Error reading body: {e}]", 0

    def _get_response_body(
        self,
        body_bytes: bytes,
        content_type: Optional[str],
    ) -> tuple[Optional[Union[str, dict]], int]:
        """Process response body."""
        if not self.config.capture_response_body:
            return None, 0

        if not self._should_capture_content_type(content_type):
            return f"[Body not captured - content-type: {content_type}]", len(body_bytes)

        body_size = len(body_bytes)

        if body_size == 0:
            return None, 0

        try:
            if body_size > self.config.max_response_body_size:
                truncated = body_bytes[:self.config.max_response_body_size].decode("utf-8", errors="replace")
                return truncated + f"...[truncated, total {body_size} bytes]", body_size

            body_str = body_bytes.decode("utf-8", errors="replace")

            # Try to parse as JSON
            if content_type and "json" in content_type.lower():
                try:
                    body_dict = json.loads(body_str)
                    return self._redact_body_fields(body_dict), body_size
                except (json.JSONDecodeError, ValueError):
                    pass

            return body_str, body_size

        except Exception as e:
            return f"[Error reading body: {e}]", body_size

    def _get_route_name(self, request: Request) -> Optional[str]:
        """Extract route name from request."""
        try:
            for route in request.app.routes:
                match, _ = route.matches(request.scope)
                if match == Match.FULL:
                    return getattr(route, "name", None) or getattr(route, "path", None)
        except Exception:
            pass
        return None

    def _get_path_params(self, request: Request) -> Optional[Dict[str, Any]]:
        """Extract path parameters."""
        if not self.config.capture_path_params:
            return None
        path_params = dict(request.path_params)
        return path_params if path_params else None

    def _get_query_params(self, request: Request) -> Optional[Dict[str, Any]]:
        """Extract query parameters."""
        if not self.config.capture_query_params:
            return None
        query_params = dict(request.query_params)
        return query_params if query_params else None

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Process the request and capture details."""
        # Early exit: check if this path should be excluded before any processing
        path = request.url.path
        path_lower = path.lower()
        
        # Hardcoded exclusion for dashboard paths (most common case)
        if path_lower.startswith("/_chronicle"):
            return await call_next(request)
        
        # Check exclusion list from config
        sampler = self._get_sampler()
        for excluded in sampler.config.never_capture_endpoints:
            excluded_lower = excluded.lower().rstrip("/")
            path_normalized = path_lower.rstrip("/")
            # Match exact path or any sub-path (must start with excluded path + "/")
            if (path_normalized == excluded_lower or 
                path_lower.startswith(excluded_lower + "/")):
                # Skip all capture processing for excluded endpoints
                return await call_next(request)
        
        start_time = time.perf_counter()
        request_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc)

        # Extract request details early
        method = request.method
        full_url = str(request.url)
        query_params = self._get_query_params(request)
        path_params = self._get_path_params(request)
        route_name = self._get_route_name(request)
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        # Get request body (need to do this before call_next consumes it)
        request_body, request_body_size = await self._get_request_body(request)

        # Get request headers
        request_headers = None
        if self.config.capture_request_headers:
            request_headers = self._redact_headers(dict(request.headers))

        # Process the actual request
        response = None
        error_info = None
        status_code = None

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as e:
            error_info = {
                "type": type(e).__name__,
                "message": str(e)[:1000],
                "traceback": traceback.format_exc()[:2000],
            }
            raise

        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Check sampling AFTER we have status code and duration
            sampler = self._get_sampler()
            should_capture = sampler.should_capture(
                endpoint=path,
                method=method,
                status_code=status_code,
                duration_ms=duration_ms,
                request_body=request_body,
                query_params=query_params,
            )

            # Check type limits BEFORE doing any capture work
            if should_capture:
                type_limiter = _get_type_limiter()
                if type_limiter and type_limiter._enabled:
                    type_allowed, type_value = type_limiter.should_capture(path, request_body)
                    if not type_allowed:
                        # Type limit reached - skip capture entirely
                        # This prevents the request from being stored
                        should_capture = False
            
            if should_capture:
                # Capture response details
                response_headers = None
                response_body = None
                response_body_size = 0

                if response is not None:
                    # Response headers
                    if self.config.capture_response_headers:
                        response_headers = self._redact_headers(dict(response.headers))

                    # Response body - need to intercept it
                    if self.config.capture_response_body:
                        # For StreamingResponse, we need to collect the body
                        if isinstance(response, StreamingResponse):
                            body_parts = []
                            async for chunk in response.body_iterator:
                                body_parts.append(chunk)
                            body_bytes = b"".join(body_parts)

                            content_type = response.headers.get("content-type")
                            response_body, response_body_size = self._get_response_body(
                                body_bytes, content_type
                            )

                            # Recreate the response with the body
                            response = Response(
                                content=body_bytes,
                                status_code=response.status_code,
                                headers=dict(response.headers),
                                media_type=response.media_type,
                            )

                # Create captured request record
                captured = CapturedRequest(
                    id=request_id,
                    timestamp=timestamp,
                    method=method,
                    path=path,
                    full_url=full_url,
                    query_params=query_params,
                    path_params=path_params,
                    request_headers=request_headers,
                    request_body=request_body,
                    request_body_size=request_body_size,
                    status_code=status_code,
                    response_headers=response_headers,
                    response_body=response_body,
                    response_body_size=response_body_size,
                    duration_ms=duration_ms,
                    route_name=route_name,
                    client_ip=client_ip,
                    user_agent=user_agent,
                    error=error_info,
                    sampled=True,
                )

                # Store the captured request
                _store_captured_request(captured)

        return response


# Convenience function to get capture stats
def get_capture_stats() -> Dict[str, Any]:
    """Get statistics about captured requests."""
    requests = _captured_requests

    if not requests:
        return {
            "total_captured": 0,
            "by_method": {},
            "by_status": {},
            "error_count": 0,
            "avg_duration_ms": 0,
        }

    by_method: Dict[str, int] = {}
    by_status: Dict[int, int] = {}
    error_count = 0
    total_duration = 0.0

    for req in requests:
        by_method[req.method] = by_method.get(req.method, 0) + 1

        if req.status_code:
            by_status[req.status_code] = by_status.get(req.status_code, 0) + 1

        if req.error:
            error_count += 1

        total_duration += req.duration_ms

    return {
        "total_captured": len(requests),
        "by_method": by_method,
        "by_status": by_status,
        "error_count": error_count,
        "error_rate": round(error_count / len(requests) * 100, 2) if requests else 0,
        "avg_duration_ms": round(total_duration / len(requests), 2) if requests else 0,
        "sampling_stats": get_sampler().get_stats(),
    }
