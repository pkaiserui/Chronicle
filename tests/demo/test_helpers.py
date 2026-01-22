"""Helper functions for tests to handle FastAPI Query parameters."""

from typing import Any, Optional


def extract_query_value(value: Any) -> Any:
    """Extract actual value from FastAPI Query object if needed."""
    # If it's a Query object, try to get its default
    if hasattr(value, "default"):
        return value.default
    # If it's already a value, return it
    return value


def call_with_query_params(func, **kwargs):
    """Call a function, extracting values from Query objects."""
    cleaned_kwargs = {}
    for key, value in kwargs.items():
        cleaned_kwargs[key] = extract_query_value(value)
    return func(**cleaned_kwargs)
