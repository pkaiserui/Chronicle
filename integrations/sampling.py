"""
Smart sampling strategies for Chronicle.

Provides configurable sampling to control capture volume in high-traffic scenarios:
- RANDOM: Simple percentage-based sampling
- CLUSTERING: Hash-based sampling to capture diverse input patterns
- ADAPTIVE: Increases sampling rate when errors occur
- HEAD: Captures first N requests per endpoint
"""

from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Dict, Optional, Set


class SamplingStrategy(str, Enum):
    """Available sampling strategies."""

    # Capture everything (no sampling)
    ALL = "all"

    # Random percentage sampling
    RANDOM = "random"

    # Hash inputs to capture diverse patterns
    CLUSTERING = "clustering"

    # Increase rate when errors spike
    ADAPTIVE = "adaptive"

    # First N requests per endpoint
    HEAD = "head"

    # Only capture errors and slow requests
    CONDITIONAL = "conditional"


@dataclass
class SamplingConfig:
    """
    Configuration for request sampling.

    Usage:
        from Chronicle.integrations import SamplingConfig, SamplingStrategy

        config = SamplingConfig(
            strategy=SamplingStrategy.CLUSTERING,
            base_rate=0.1,  # 10% baseline
            always_capture_errors=True,
            latency_threshold_ms=500,
        )
    """

    # Primary strategy
    strategy: SamplingStrategy = SamplingStrategy.RANDOM

    # Base sampling rate (0.0 to 1.0) for RANDOM/CLUSTERING
    base_rate: float = 0.1

    # Always capture these regardless of sampling
    always_capture_errors: bool = True
    always_capture_slow: bool = True
    latency_threshold_ms: float = 1000.0  # Slow request threshold

    # CLUSTERING specific: max unique patterns to track per endpoint
    max_patterns_per_endpoint: int = 100

    # HEAD specific: how many requests per endpoint to capture
    head_count: int = 100

    # ADAPTIVE specific: error rate window
    adaptive_window_seconds: int = 60
    adaptive_error_multiplier: float = 5.0  # 5x sampling on errors
    adaptive_max_rate: float = 1.0

    # Status codes considered errors
    error_status_codes: Set[int] = field(default_factory=lambda: {
        400, 401, 403, 404, 405, 408, 409, 410, 422, 429,
        500, 501, 502, 503, 504,
    })

    # Endpoints to always capture (exact match or prefix)
    always_capture_endpoints: Set[str] = field(default_factory=set)

    # Endpoints to never capture
    never_capture_endpoints: Set[str] = field(default_factory=lambda: {
        "/health",
        "/healthz",
        "/ready",
        "/readiness",
        "/metrics",
        "/favicon.ico",
    })


class Sampler:
    """
    Determines whether to capture a given request based on sampling strategy.

    Thread-safe implementation supporting multiple strategies.
    """

    def __init__(self, config: Optional[SamplingConfig] = None):
        self.config = config or SamplingConfig()
        self._lock = Lock()

        # CLUSTERING state: track seen input patterns per endpoint
        self._seen_patterns: Dict[str, Set[str]] = {}

        # HEAD state: count per endpoint
        self._head_counts: Dict[str, int] = {}

        # ADAPTIVE state: recent error tracking
        self._recent_requests: list = []  # [(timestamp, is_error), ...]
        self._adaptive_rate: float = self.config.base_rate

    def should_capture(
        self,
        endpoint: str,
        method: str,
        status_code: Optional[int] = None,
        duration_ms: Optional[float] = None,
        request_body: Optional[Any] = None,
        query_params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Determine if a request should be captured.

        Call this AFTER the request completes to include status code and duration.
        For pre-request decisions, omit status_code and duration_ms.

        Args:
            endpoint: The request path (e.g., "/api/users")
            method: HTTP method (e.g., "GET", "POST")
            status_code: Response status code (if available)
            duration_ms: Request duration in milliseconds (if available)
            request_body: Request body for clustering (if available)
            query_params: Query parameters for clustering (if available)

        Returns:
            True if the request should be captured
        """
        # Check never capture list
        if self._should_skip_endpoint(endpoint):
            return False

        # Check always capture list
        if self._should_always_capture_endpoint(endpoint):
            return True

        # Always capture errors if configured
        if self.config.always_capture_errors and status_code is not None:
            if status_code in self.config.error_status_codes:
                self._record_for_adaptive(is_error=True)
                return True

        # Always capture slow requests if configured
        if self.config.always_capture_slow and duration_ms is not None:
            if duration_ms >= self.config.latency_threshold_ms:
                return True

        # Apply strategy-specific logic
        strategy = self.config.strategy

        if strategy == SamplingStrategy.ALL:
            return True

        elif strategy == SamplingStrategy.RANDOM:
            return self._sample_random()

        elif strategy == SamplingStrategy.CLUSTERING:
            return self._sample_clustering(
                endpoint, method, request_body, query_params
            )

        elif strategy == SamplingStrategy.ADAPTIVE:
            self._record_for_adaptive(is_error=False)
            return self._sample_adaptive()

        elif strategy == SamplingStrategy.HEAD:
            return self._sample_head(endpoint, method)

        elif strategy == SamplingStrategy.CONDITIONAL:
            # Only capture errors and slow requests (already handled above)
            return False

        return False

    def _should_skip_endpoint(self, endpoint: str) -> bool:
        """Check if endpoint is in never-capture list."""
        endpoint_lower = endpoint.lower()
        for skip in self.config.never_capture_endpoints:
            if endpoint_lower == skip.lower() or endpoint_lower.startswith(skip.lower()):
                return True
        return False

    def _should_always_capture_endpoint(self, endpoint: str) -> bool:
        """Check if endpoint is in always-capture list."""
        endpoint_lower = endpoint.lower()
        for always in self.config.always_capture_endpoints:
            if endpoint_lower == always.lower() or endpoint_lower.startswith(always.lower()):
                return True
        return False

    def _sample_random(self) -> bool:
        """Simple random sampling."""
        return random.random() < self.config.base_rate

    def _sample_clustering(
        self,
        endpoint: str,
        method: str,
        request_body: Optional[Any],
        query_params: Optional[Dict[str, Any]],
    ) -> bool:
        """
        Clustering-based sampling to capture diverse input patterns.

        Hashes the input to create a pattern signature, then:
        - Always captures new patterns (up to max_patterns_per_endpoint)
        - Randomly samples seen patterns at base_rate
        """
        # Create pattern hash from inputs
        pattern_key = self._create_pattern_hash(endpoint, method, request_body, query_params)
        endpoint_key = f"{method}:{endpoint}"

        with self._lock:
            if endpoint_key not in self._seen_patterns:
                self._seen_patterns[endpoint_key] = set()

            seen = self._seen_patterns[endpoint_key]

            # New pattern - always capture (up to limit)
            if pattern_key not in seen:
                if len(seen) < self.config.max_patterns_per_endpoint:
                    seen.add(pattern_key)
                    return True
                # Over limit - fall back to random sampling
                return random.random() < self.config.base_rate

            # Seen pattern - sample randomly
            return random.random() < self.config.base_rate

    def _create_pattern_hash(
        self,
        endpoint: str,
        method: str,
        request_body: Optional[Any],
        query_params: Optional[Dict[str, Any]],
    ) -> str:
        """Create a hash representing the input pattern."""
        # Hash structure, not values, for clustering
        components = [method, endpoint]

        if request_body is not None:
            if isinstance(request_body, dict):
                # Hash the keys (structure) not values
                components.append(str(sorted(request_body.keys())))
            else:
                components.append(type(request_body).__name__)

        if query_params:
            components.append(str(sorted(query_params.keys())))

        pattern_str = "|".join(components)
        return hashlib.md5(pattern_str.encode()).hexdigest()[:16]

    def _sample_adaptive(self) -> bool:
        """
        Adaptive sampling that increases rate when errors occur.

        Maintains a sliding window of recent requests and adjusts
        sampling rate based on error rate.
        """
        return random.random() < self._adaptive_rate

    def _record_for_adaptive(self, is_error: bool) -> None:
        """Record a request for adaptive sampling calculations."""
        if self.config.strategy != SamplingStrategy.ADAPTIVE:
            return

        now = time.time()
        cutoff = now - self.config.adaptive_window_seconds

        with self._lock:
            # Add new record
            self._recent_requests.append((now, is_error))

            # Clean old records
            self._recent_requests = [
                (ts, err) for ts, err in self._recent_requests if ts > cutoff
            ]

            # Calculate error rate and adjust sampling
            if len(self._recent_requests) > 0:
                error_count = sum(1 for _, err in self._recent_requests if err)
                error_rate = error_count / len(self._recent_requests)

                # Increase sampling proportionally to error rate
                if error_rate > 0:
                    multiplier = 1 + (error_rate * self.config.adaptive_error_multiplier)
                    self._adaptive_rate = min(
                        self.config.base_rate * multiplier,
                        self.config.adaptive_max_rate,
                    )
                else:
                    self._adaptive_rate = self.config.base_rate

    def _sample_head(self, endpoint: str, method: str) -> bool:
        """Capture first N requests per endpoint."""
        endpoint_key = f"{method}:{endpoint}"

        with self._lock:
            count = self._head_counts.get(endpoint_key, 0)

            if count < self.config.head_count:
                self._head_counts[endpoint_key] = count + 1
                return True

            return False

    def get_stats(self) -> Dict[str, Any]:
        """Get sampling statistics."""
        with self._lock:
            return {
                "strategy": self.config.strategy.value,
                "base_rate": self.config.base_rate,
                "adaptive_rate": self._adaptive_rate if self.config.strategy == SamplingStrategy.ADAPTIVE else None,
                "patterns_tracked": {k: len(v) for k, v in self._seen_patterns.items()},
                "head_counts": dict(self._head_counts),
                "recent_requests_window": len(self._recent_requests),
            }

    def reset(self) -> None:
        """Reset sampling state."""
        with self._lock:
            self._seen_patterns.clear()
            self._head_counts.clear()
            self._recent_requests.clear()
            self._adaptive_rate = self.config.base_rate


# Global sampler instance
_sampler: Optional[Sampler] = None


def configure_sampling(config: SamplingConfig) -> Sampler:
    """
    Configure global sampling settings.

    Call this at application startup before adding middleware.

    Usage:
        from Chronicle.integrations import configure_sampling, SamplingConfig, SamplingStrategy

        configure_sampling(SamplingConfig(
            strategy=SamplingStrategy.CLUSTERING,
            base_rate=0.1,
        ))
    """
    global _sampler
    _sampler = Sampler(config)
    return _sampler


def get_sampler() -> Sampler:
    """Get the global sampler instance."""
    global _sampler
    if _sampler is None:
        _sampler = Sampler()
    return _sampler
