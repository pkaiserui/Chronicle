"""
Chronicle framework integrations package.

Provides middleware and auto-instrumentation for popular web frameworks:
- FastAPI
- Flask (coming soon)
- Django (coming soon)
"""

from .fastapi import ChronicleMiddleware
from .sampling import (
    SamplingStrategy,
    SamplingConfig,
    Sampler,
    configure_sampling,
)

__all__ = [
    "ChronicleMiddleware",
    "SamplingStrategy",
    "SamplingConfig",
    "Sampler",
    "configure_sampling",
]
