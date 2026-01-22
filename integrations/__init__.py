"""
Chronicle framework integrations package.

Provides middleware and auto-instrumentation for popular web frameworks:
- FastAPI
- Flask (coming soon)
- Django (coming soon)
"""

from .fastapi import (
    ChronicleMiddleware,
    get_capture_stats,
    get_captured_requests,
    clear_captured_requests,
)
from .sampling import (
    SamplingStrategy,
    SamplingConfig,
    Sampler,
    configure_sampling,
)
from .ui import (
    mount_chronicle_dashboard,
    TypeLimitConfig,
    TypeLimiter,
    get_type_limiter,
    configure_type_limits,
    check_type_limit,
    FunctionLimitConfig,
    FunctionLimiter,
    get_function_limiter,
    configure_function_limits,
)

__all__ = [
    # Middleware
    "ChronicleMiddleware",
    "get_capture_stats",
    "get_captured_requests",
    "clear_captured_requests",
    # Sampling
    "SamplingStrategy",
    "SamplingConfig",
    "Sampler",
    "configure_sampling",
    # UI Dashboard
    "mount_chronicle_dashboard",
    "TypeLimitConfig",
    "TypeLimiter",
    "get_type_limiter",
    "configure_type_limits",
    "check_type_limit",
    # Function Limits
    "FunctionLimitConfig",
    "FunctionLimiter",
    "get_function_limiter",
    "configure_function_limits",
]
