"""
Chronicle Dashboard UI.

Provides an optional lightweight web dashboard for viewing and adjusting
Chronicle capture settings in real-time.

Usage:
    from fastapi import FastAPI
    from Chronicle.integrations import ChronicleMiddleware
    from Chronicle.integrations.ui import mount_chronicle_dashboard

    app = FastAPI()
    app.add_middleware(ChronicleMiddleware)

    # Mount the dashboard at /_chronicle
    mount_chronicle_dashboard(app)

    # Or with custom path and auth
    mount_chronicle_dashboard(
        app,
        path="/_admin/chronicle",
        enabled=os.getenv("CHRONICLE_UI_ENABLED", "false") == "true",
    )
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .fastapi import (
    CaptureConfig,
    _captured_requests,
    get_capture_stats,
    get_captured_requests,
    clear_captured_requests,
)
from .sampling import (
    SamplingConfig,
    SamplingStrategy,
    Sampler,
    get_sampler,
    configure_sampling,
)


# Function-based capture limits
@dataclass
class FunctionLimitConfig:
    """Configuration for function-based capture limiting."""
    
    # Maximum captures per function name
    limit_per_function: int = 5000
    
    # Whether to alert when limit is reached
    alert_on_limit: bool = True
    
    # Action when limit reached: "stop" or "sample"
    limit_action: str = "stop"
    
    # Sample rate when limit reached (if action is "sample")
    overflow_sample_rate: float = 0.01


@dataclass
class FunctionLimitState:
    """Tracks capture counts per function name."""
    
    counts: Dict[str, int] = field(default_factory=dict)
    alerts: List[Dict[str, Any]] = field(default_factory=list)
    stopped_functions: Set[str] = field(default_factory=set)


class FunctionLimiter:
    """
    Manages function-based capture limits.
    
    Tracks captures per function name and enforces limits.
    """
    
    def __init__(self, config: Optional[FunctionLimitConfig] = None):
        self.config = config or FunctionLimitConfig()
        self._state = FunctionLimitState()
        self._lock = Lock()
        self._enabled = False
        self._function_configs: Dict[str, FunctionLimitConfig] = {}  # Per-function configs
    
    def enable(self, function_name: Optional[str] = None, config: Optional[FunctionLimitConfig] = None) -> None:
        """Enable function limiting, optionally for specific function."""
        self._enabled = True
        if function_name and config:
            self._function_configs[function_name] = config
    
    def disable(self, function_name: Optional[str] = None) -> None:
        """Disable function limiting."""
        if function_name:
            self._function_configs.pop(function_name, None)
        else:
            self._enabled = False
    
    def get_config(self, function_name: Optional[str] = None) -> FunctionLimitConfig:
        """Get config for function or default."""
        if function_name and function_name in self._function_configs:
            return self._function_configs[function_name]
        return self.config
    
    def set_config(self, config: FunctionLimitConfig, function_name: Optional[str] = None) -> None:
        """Set config globally or per-function."""
        if function_name:
            self._function_configs[function_name] = config
        else:
            self.config = config
    
    def should_capture(self, function_name: str) -> bool:
        """
        Check if function should be captured based on function limits.
        
        Returns:
            True if the function should be captured
        """
        if not self._enabled:
            return True
        
        config = self.get_config(function_name)
        
        with self._lock:
            # Check if function is stopped
            if function_name in self._state.stopped_functions:
                if config.limit_action == "stop":
                    return False
                # Sample at overflow rate
                import random
                return random.random() < config.overflow_sample_rate
            
            # Get current count
            count = self._state.counts.get(function_name, 0)
            
            # Check limit BEFORE incrementing
            if count >= config.limit_per_function:
                # Ensure it's marked as stopped
                if function_name not in self._state.stopped_functions:
                    self._state.stopped_functions.add(function_name)
                    if config.alert_on_limit:
                        self._state.alerts.append({
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "function_name": function_name,
                            "count": count,
                            "message": f"Capture limit ({config.limit_per_function}) reached for function '{function_name}'",
                        })
                
                # Don't capture - limit reached
                if config.limit_action == "stop":
                    return False
                # Sample at very low rate if configured
                import random
                return random.random() < config.overflow_sample_rate
            
            # Only increment if we're below the limit and will capture
            if count < config.limit_per_function:
                self._state.counts[function_name] = count + 1
                # Check if we just hit the limit after incrementing
                if self._state.counts[function_name] >= config.limit_per_function:
                    # Mark as stopped and create alert
                    if function_name not in self._state.stopped_functions:
                        self._state.stopped_functions.add(function_name)
                        if config.alert_on_limit:
                            self._state.alerts.append({
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "function_name": function_name,
                                "count": self._state.counts[function_name],
                                "message": f"Capture limit ({config.limit_per_function}) reached for function '{function_name}'",
                            })
                return True
            
            # Should never reach here, but just in case
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get function limiting statistics."""
        with self._lock:
            return {
                "enabled": self._enabled,
                "config": {
                    "limit_per_function": self.config.limit_per_function,
                    "alert_on_limit": self.config.alert_on_limit,
                    "limit_action": self.config.limit_action,
                    "overflow_sample_rate": self.config.overflow_sample_rate,
                },
                "counts": dict(self._state.counts),
                "stopped_functions": list(self._state.stopped_functions),
                "alerts": list(self._state.alerts),
                "function_configs": {
                    fn: {
                        "limit_per_function": c.limit_per_function,
                    }
                    for fn, c in self._function_configs.items()
                },
            }
    
    def get_alerts(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent alerts."""
        with self._lock:
            return list(reversed(self._state.alerts[-limit:]))
    
    def clear_alerts(self) -> int:
        """Clear all alerts."""
        with self._lock:
            count = len(self._state.alerts)
            self._state.alerts.clear()
            return count
    
    def reset_function(self, function_name: str) -> bool:
        """Reset count for a specific function, allowing captures again."""
        with self._lock:
            if function_name in self._state.counts:
                del self._state.counts[function_name]
            self._state.stopped_functions.discard(function_name)
            return True
    
    def reset_all(self) -> None:
        """Reset all function limiting state."""
        with self._lock:
            self._state.counts.clear()
            self._state.stopped_functions.clear()
            self._state.alerts.clear()


# Global function limiter instance
_function_limiter: Optional[FunctionLimiter] = None


def get_function_limiter() -> FunctionLimiter:
    """Get the global function limiter instance."""
    global _function_limiter
    if _function_limiter is None:
        _function_limiter = FunctionLimiter()
    return _function_limiter


def configure_function_limits(config: FunctionLimitConfig) -> FunctionLimiter:
    """Configure global function limiting."""
    global _function_limiter
    _function_limiter = FunctionLimiter(config)
    _function_limiter.enable()
    return _function_limiter


# Type-based capture limits
@dataclass
class TypeLimitConfig:
    """Configuration for type-based capture limiting."""
    
    # Field path to extract type from (e.g., "type", "event_type", "data.type")
    field_path: str = "type"
    
    # Maximum captures per type value
    limit_per_type: int = 5000
    
    # Whether to alert when limit is reached
    alert_on_limit: bool = True
    
    # Action when limit reached: "stop" or "sample"
    limit_action: str = "stop"
    
    # Sample rate when limit reached (if action is "sample")
    overflow_sample_rate: float = 0.01


@dataclass
class TypeLimitState:
    """Tracks capture counts per type value."""
    
    counts: Dict[str, int] = field(default_factory=dict)
    alerts: List[Dict[str, Any]] = field(default_factory=list)
    stopped_types: Set[str] = field(default_factory=set)


class TypeLimiter:
    """
    Manages type-based capture limits.
    
    Tracks captures per payload type value and enforces limits.
    """
    
    def __init__(self, config: Optional[TypeLimitConfig] = None):
        self.config = config or TypeLimitConfig()
        self._state = TypeLimitState()
        self._lock = Lock()
        self._enabled = False
        self._endpoints: Dict[str, TypeLimitConfig] = {}  # Per-endpoint configs
    
    def enable(self, endpoint: Optional[str] = None, config: Optional[TypeLimitConfig] = None) -> None:
        """Enable type limiting, optionally for specific endpoint."""
        self._enabled = True
        if endpoint and config:
            self._endpoints[endpoint] = config
    
    def disable(self, endpoint: Optional[str] = None) -> None:
        """Disable type limiting."""
        if endpoint:
            self._endpoints.pop(endpoint, None)
        else:
            self._enabled = False
    
    def get_config(self, endpoint: Optional[str] = None) -> TypeLimitConfig:
        """Get config for endpoint or default."""
        if endpoint and endpoint in self._endpoints:
            return self._endpoints[endpoint]
        return self.config
    
    def set_config(self, config: TypeLimitConfig, endpoint: Optional[str] = None) -> None:
        """Set config globally or per-endpoint."""
        if endpoint:
            self._endpoints[endpoint] = config
        else:
            self.config = config
    
    def _extract_type_value(self, body: Any, field_path: str) -> Optional[str]:
        """Extract type value from body using field path."""
        if body is None:
            return None
        
        if not isinstance(body, dict):
            return None
        
        parts = field_path.split(".")
        current = body
        
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        
        return str(current) if current is not None else None
    
    def should_capture(
        self,
        endpoint: str,
        request_body: Any,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if request should be captured based on type limits.
        
        Returns:
            (should_capture, type_value) tuple
        """
        if not self._enabled:
            return True, None
        
        config = self.get_config(endpoint)
        type_value = self._extract_type_value(request_body, config.field_path)
        
        if type_value is None:
            return True, None  # No type to limit on
        
        with self._lock:
            # Check if type is stopped
            if type_value in self._state.stopped_types:
                if config.limit_action == "stop":
                    return False, type_value
                # Sample at overflow rate
                import random
                return random.random() < config.overflow_sample_rate, type_value
            
            # Get current count
            count = self._state.counts.get(type_value, 0)
            
            # Check limit BEFORE incrementing - if already at or over limit, don't capture
            if count >= config.limit_per_type:
                # Ensure it's marked as stopped
                if type_value not in self._state.stopped_types:
                    self._state.stopped_types.add(type_value)
                    if config.alert_on_limit:
                        self._state.alerts.append({
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "type_value": type_value,
                            "count": count,
                            "endpoint": endpoint,
                            "message": f"Capture limit ({config.limit_per_type}) reached for type '{type_value}'",
                        })
                
                # Don't capture - limit reached
                if config.limit_action == "stop":
                    return False, type_value
                # Sample at very low rate if configured
                import random
                return random.random() < config.overflow_sample_rate, type_value
            
            # Only increment if we're below the limit and will capture
            # This ensures we never exceed the limit
            if count < config.limit_per_type:
                self._state.counts[type_value] = count + 1
                # Check if we just hit the limit after incrementing
                if self._state.counts[type_value] >= config.limit_per_type:
                    # Mark as stopped and create alert
                    if type_value not in self._state.stopped_types:
                        self._state.stopped_types.add(type_value)
                        if config.alert_on_limit:
                            self._state.alerts.append({
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "type_value": type_value,
                                "count": self._state.counts[type_value],
                                "endpoint": endpoint,
                                "message": f"Capture limit ({config.limit_per_type}) reached for type '{type_value}'",
                            })
                return True, type_value
            
            # Should never reach here, but just in case
            return False, type_value
    
    def get_stats(self) -> Dict[str, Any]:
        """Get type limiting statistics."""
        with self._lock:
            return {
                "enabled": self._enabled,
                "config": {
                    "field_path": self.config.field_path,
                    "limit_per_type": self.config.limit_per_type,
                    "alert_on_limit": self.config.alert_on_limit,
                    "limit_action": self.config.limit_action,
                    "overflow_sample_rate": self.config.overflow_sample_rate,
                },
                "counts": dict(self._state.counts),
                "stopped_types": list(self._state.stopped_types),
                "alerts": list(self._state.alerts),
                "endpoint_configs": {
                    ep: {
                        "field_path": c.field_path,
                        "limit_per_type": c.limit_per_type,
                    }
                    for ep, c in self._endpoints.items()
                },
            }
    
    def get_alerts(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent alerts."""
        with self._lock:
            return list(reversed(self._state.alerts[-limit:]))
    
    def clear_alerts(self) -> int:
        """Clear all alerts."""
        with self._lock:
            count = len(self._state.alerts)
            self._state.alerts.clear()
            return count
    
    def reset_type(self, type_value: str) -> bool:
        """Reset count for a specific type, allowing captures again."""
        with self._lock:
            if type_value in self._state.counts:
                del self._state.counts[type_value]
            self._state.stopped_types.discard(type_value)
            return True
    
    def reset_all(self) -> None:
        """Reset all type limiting state."""
        with self._lock:
            self._state.counts.clear()
            self._state.stopped_types.clear()
            self._state.alerts.clear()


# Global type limiter instance
_type_limiter: Optional[TypeLimiter] = None


def get_type_limiter() -> TypeLimiter:
    """Get the global type limiter instance."""
    global _type_limiter
    if _type_limiter is None:
        _type_limiter = TypeLimiter()
    return _type_limiter


def configure_type_limits(config: TypeLimitConfig) -> TypeLimiter:
    """Configure global type limiting."""
    global _type_limiter
    _type_limiter = TypeLimiter(config)
    _type_limiter.enable()
    return _type_limiter


# Dashboard HTML
DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chronicle Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #0a0e14;
            --bg-secondary: #11151c;
            --bg-tertiary: #1a1f28;
            --bg-card: #151a23;
            --border-color: #2d3640;
            --text-primary: #e6e9ed;
            --text-secondary: #8b95a5;
            --text-muted: #5c6675;
            --accent-cyan: #39bae6;
            --accent-green: #7fd962;
            --accent-orange: #ff9940;
            --accent-red: #f07178;
            --accent-purple: #c792ea;
            --accent-yellow: #ffb454;
            --shadow: 0 4px 24px rgba(0,0,0,0.4);
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Space Grotesk', sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            background-image: 
                radial-gradient(ellipse at 20% 0%, rgba(57, 186, 230, 0.08) 0%, transparent 50%),
                radial-gradient(ellipse at 80% 100%, rgba(199, 146, 234, 0.06) 0%, transparent 50%);
        }
        
        .header {
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border-color);
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .logo {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }
        
        .logo-icon {
            width: 32px;
            height: 32px;
            background: linear-gradient(135deg, var(--accent-cyan), var(--accent-purple));
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 18px;
        }
        
        .logo h1 {
            font-size: 1.25rem;
            font-weight: 600;
            background: linear-gradient(135deg, var(--accent-cyan), var(--accent-purple));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .header-actions {
            display: flex;
            gap: 0.75rem;
        }
        
        .btn {
            font-family: 'Space Grotesk', sans-serif;
            padding: 0.5rem 1rem;
            border-radius: 6px;
            border: 1px solid var(--border-color);
            background: var(--bg-tertiary);
            color: var(--text-primary);
            cursor: pointer;
            font-size: 0.875rem;
            font-weight: 500;
            transition: all 0.2s ease;
        }
        
        .btn:hover {
            background: var(--bg-card);
            border-color: var(--accent-cyan);
        }
        
        .btn-primary {
            background: linear-gradient(135deg, var(--accent-cyan), var(--accent-purple));
            border: none;
            color: var(--bg-primary);
        }
        
        .btn-primary:hover {
            opacity: 0.9;
        }
        
        .btn-danger {
            border-color: var(--accent-red);
            color: var(--accent-red);
        }
        
        .btn-danger:hover {
            background: rgba(240, 113, 120, 0.15);
        }
        
        .main {
            padding: 2rem;
            max-width: 1600px;
            margin: 0 auto;
        }
        
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }
        
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            box-shadow: var(--shadow);
        }
        
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
        }
        
        .card-title {
            font-size: 0.875rem;
            font-weight: 600;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        
        .card-badge {
            font-size: 0.75rem;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            font-weight: 500;
        }
        
        .badge-active {
            background: rgba(127, 217, 98, 0.15);
            color: var(--accent-green);
        }
        
        .badge-warning {
            background: rgba(255, 153, 64, 0.15);
            color: var(--accent-orange);
        }
        
        .badge-error {
            background: rgba(240, 113, 120, 0.15);
            color: var(--accent-red);
        }
        
        .stat-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 2.5rem;
            font-weight: 600;
            color: var(--accent-cyan);
            line-height: 1;
        }
        
        .stat-label {
            font-size: 0.875rem;
            color: var(--text-muted);
            margin-top: 0.5rem;
        }
        
        .stat-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 1rem;
            margin-top: 1rem;
        }
        
        .stat-item {
            padding: 0.75rem;
            background: var(--bg-tertiary);
            border-radius: 8px;
        }
        
        .stat-item-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 1.25rem;
            font-weight: 600;
            color: var(--text-primary);
        }
        
        .stat-item-label {
            font-size: 0.75rem;
            color: var(--text-muted);
        }
        
        .form-group {
            margin-bottom: 1rem;
        }
        
        .form-label {
            display: block;
            font-size: 0.8125rem;
            font-weight: 500;
            color: var(--text-secondary);
            margin-bottom: 0.5rem;
        }
        
        .form-input, .form-select {
            width: 100%;
            padding: 0.625rem 0.875rem;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.875rem;
            background: var(--bg-tertiary);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            color: var(--text-primary);
            transition: border-color 0.2s ease;
        }
        
        .form-input:focus, .form-select:focus {
            outline: none;
            border-color: var(--accent-cyan);
        }
        
        .form-select {
            cursor: pointer;
        }
        
        .form-row {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 1rem;
        }
        
        .toggle {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            cursor: pointer;
        }
        
        .toggle-switch {
            position: relative;
            width: 44px;
            height: 24px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            transition: all 0.2s ease;
        }
        
        .toggle-switch::after {
            content: '';
            position: absolute;
            top: 3px;
            left: 3px;
            width: 16px;
            height: 16px;
            background: var(--text-muted);
            border-radius: 50%;
            transition: all 0.2s ease;
        }
        
        .toggle.active .toggle-switch {
            background: var(--accent-cyan);
            border-color: var(--accent-cyan);
        }
        
        .toggle.active .toggle-switch::after {
            left: 23px;
            background: var(--bg-primary);
        }
        
        .toggle-label {
            font-size: 0.875rem;
            color: var(--text-secondary);
        }
        
        .alert-list {
            max-height: 300px;
            overflow-y: auto;
        }
        
        .alert-item {
            display: flex;
            align-items: flex-start;
            gap: 0.75rem;
            padding: 0.875rem;
            background: var(--bg-tertiary);
            border-radius: 8px;
            margin-bottom: 0.5rem;
            border-left: 3px solid var(--accent-orange);
        }
        
        .alert-icon {
            font-size: 1.25rem;
        }
        
        .alert-content {
            flex: 1;
        }
        
        .alert-message {
            font-size: 0.875rem;
            color: var(--text-primary);
            margin-bottom: 0.25rem;
        }
        
        .alert-time {
            font-size: 0.75rem;
            color: var(--text-muted);
            font-family: 'JetBrains Mono', monospace;
        }
        
        .alert-action {
            padding: 0.25rem 0.5rem;
            font-size: 0.75rem;
        }
        
        .type-list {
            max-height: 350px;
            overflow-y: auto;
        }
        
        .type-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0.75rem;
            background: var(--bg-tertiary);
            border-radius: 8px;
            margin-bottom: 0.5rem;
        }
        
        .type-info {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }
        
        .type-name {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.875rem;
            color: var(--text-primary);
        }
        
        .type-count {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.875rem;
            color: var(--accent-cyan);
        }
        
        .type-status {
            font-size: 0.75rem;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
        }
        
        .type-status.active {
            background: rgba(127, 217, 98, 0.15);
            color: var(--accent-green);
        }
        
        .type-status.stopped {
            background: rgba(240, 113, 120, 0.15);
            color: var(--accent-red);
        }
        
        .progress-bar {
            height: 6px;
            background: var(--bg-tertiary);
            border-radius: 3px;
            overflow: hidden;
            margin-top: 0.5rem;
        }
        
        .progress-fill {
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s ease;
        }
        
        .progress-fill.low { background: var(--accent-green); }
        .progress-fill.medium { background: var(--accent-yellow); }
        .progress-fill.high { background: var(--accent-orange); }
        .progress-fill.full { background: var(--accent-red); }
        
        .empty-state {
            text-align: center;
            padding: 2rem;
            color: var(--text-muted);
        }
        
        .empty-state-icon {
            font-size: 2rem;
            margin-bottom: 0.5rem;
            opacity: 0.5;
        }
        
        .requests-table {
            width: 100%;
            border-collapse: collapse;
        }
        
        .requests-table th,
        .requests-table td {
            padding: 0.75rem;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }
        
        .requests-table th {
            font-size: 0.75rem;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
        }
        
        .requests-table td {
            font-size: 0.875rem;
            font-family: 'JetBrains Mono', monospace;
        }
        
        .method {
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        
        .method-get { background: rgba(127, 217, 98, 0.15); color: var(--accent-green); }
        .method-post { background: rgba(57, 186, 230, 0.15); color: var(--accent-cyan); }
        .method-put { background: rgba(255, 180, 84, 0.15); color: var(--accent-yellow); }
        .method-delete { background: rgba(240, 113, 120, 0.15); color: var(--accent-red); }
        
        .status-2xx { color: var(--accent-green); }
        .status-4xx { color: var(--accent-orange); }
        .status-5xx { color: var(--accent-red); }
        
        .refresh-indicator {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.75rem;
            color: var(--text-muted);
        }
        
        .refresh-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--accent-green);
            animation: pulse 2s infinite;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
        }
        
        .tabs {
            display: flex;
            gap: 0.25rem;
            background: var(--bg-tertiary);
            padding: 0.25rem;
            border-radius: 8px;
            margin-bottom: 1rem;
        }
        
        .tab {
            flex: 1;
            padding: 0.625rem 1rem;
            background: transparent;
            border: none;
            border-radius: 6px;
            font-family: 'Space Grotesk', sans-serif;
            font-size: 0.875rem;
            font-weight: 500;
            color: var(--text-muted);
            cursor: pointer;
            transition: all 0.2s ease;
        }
        
        .tab:hover {
            color: var(--text-secondary);
        }
        
        .tab.active {
            background: var(--bg-card);
            color: var(--text-primary);
        }
        
        .endpoint-config {
            padding: 1rem;
            background: var(--bg-tertiary);
            border-radius: 8px;
            margin-bottom: 1rem;
        }
        
        .endpoint-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.75rem;
        }
        
        .endpoint-path {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.875rem;
            color: var(--accent-cyan);
        }
        
        ::-webkit-scrollbar {
            width: 6px;
            height: 6px;
        }
        
        ::-webkit-scrollbar-track {
            background: var(--bg-tertiary);
            border-radius: 3px;
        }
        
        ::-webkit-scrollbar-thumb {
            background: var(--border-color);
            border-radius: 3px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: var(--text-muted);
        }
    </style>
</head>
<body>
    <header class="header">
        <div class="logo">
            <div class="logo-icon">C</div>
            <h1>Chronicle Dashboard</h1>
        </div>
        <div class="header-actions">
            <div class="refresh-indicator">
                <span class="refresh-dot"></span>
                <span>Auto-refresh: <span id="refresh-interval">5s</span></span>
            </div>
            <button class="btn" onclick="refreshAll()">↻ Refresh</button>
            <button class="btn btn-danger" onclick="clearCaptures()">Clear All</button>
        </div>
    </header>
    
    <main class="main">
        <div class="grid">
            <!-- Capture Stats Card -->
            <div class="card">
                <div class="card-header">
                    <span class="card-title">Capture Stats</span>
                    <span class="card-badge badge-active" id="capture-status">Active</span>
                </div>
                <div class="stat-value" id="total-captured">0</div>
                <div class="stat-label">Total Captured Requests</div>
                <div class="stat-grid">
                    <div class="stat-item">
                        <div class="stat-item-value" id="stat-errors">0</div>
                        <div class="stat-item-label">Errors</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-item-value" id="stat-avg-duration">0ms</div>
                        <div class="stat-item-label">Avg Duration</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-item-value" id="stat-error-rate">0%</div>
                        <div class="stat-item-label">Error Rate</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-item-value" id="stat-strategy">random</div>
                        <div class="stat-item-label">Strategy</div>
                    </div>
                </div>
            </div>
            
            <!-- Sampling Settings Card -->
            <div class="card">
                <div class="card-header">
                    <span class="card-title">Sampling Settings</span>
                </div>
                <div class="form-group">
                    <label class="form-label" title="Algorithm used to decide which requests to capture">Strategy</label>
                    <select class="form-select" id="sampling-strategy" onchange="updateSamplingSettings()">
                        <option value="all" title="Capture 100% of all traffic">All (No Sampling)</option>
                        <option value="random" title="Capture a random percentage of traffic">Random</option>
                        <option value="clustering" title="Capture diverse patterns by hashing input structure">Clustering</option>
                        <option value="adaptive" title="Increase sampling rate automatically when error rates spike">Adaptive</option>
                        <option value="head" title="Capture the first N requests for each unique endpoint">Head (First N)</option>
                        <option value="conditional" title="Only capture errors or slow requests">Conditional Only</option>
                    </select>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label class="form-label" title="Baseline capture rate (e.g. 0.1 = 10% of traffic)">Base Rate</label>
                        <input type="number" class="form-input" id="base-rate" 
                               min="0" max="1" step="0.01" value="0.1"
                               onchange="updateSamplingSettings()">
                    </div>
                    <div class="form-group">
                        <label class="form-label" title="Requests slower than this are considered 'slow'">Latency Threshold (ms)</label>
                        <input type="number" class="form-input" id="latency-threshold" 
                               min="0" step="100" value="1000"
                               onchange="updateSamplingSettings()">
                    </div>
                </div>
                <div class="toggle" id="toggle-errors" onclick="toggleErrors()" title="Always capture 4xx/5xx responses regardless of sampling">
                    <div class="toggle-switch"></div>
                    <span class="toggle-label">Always capture errors</span>
                </div>
                <div class="toggle" id="toggle-slow" onclick="toggleSlow()" style="margin-top: 0.75rem;" title="Always capture requests exceeding latency threshold regardless of sampling">
                    <div class="toggle-switch"></div>
                    <span class="toggle-label">Always capture slow requests</span>
                </div>
            </div>
            
            <!-- Function Limiting Card -->
            <div class="card">
                <div class="card-header">
                    <span class="card-title">Function-Based Limits</span>
                    <span class="card-badge" id="function-limit-status">Disabled</span>
                </div>
                <div class="toggle" id="toggle-function-limits" onclick="toggleFunctionLimits()" style="margin-bottom: 1rem;" title="Enable capture quotas per function name">
                    <div class="toggle-switch"></div>
                    <span class="toggle-label">Enable function-based limiting</span>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label class="form-label" title="Maximum number of captures to keep per function">Limit Per Function</label>
                        <input type="number" class="form-input" id="function-limit-per-function" 
                               min="1" step="100" value="5000"
                               onchange="updateFunctionLimitSettings()">
                    </div>
                    <div class="form-group">
                        <label class="form-label" title="What to do once the limit for a function is reached">On Limit</label>
                        <select class="form-select" id="function-limit-action" onchange="updateFunctionLimitSettings()">
                            <option value="stop" title="Stop recording this function until reset">Stop Recording</option>
                            <option value="sample" title="Continue recording this function but at a very low sampling rate">Sample at Low Rate</option>
                        </select>
                    </div>
                </div>
            </div>
            
            <!-- Type Limiting Card -->
            <div class="card">
                <div class="card-header">
                    <span class="card-title">Type-Based Limits</span>
                    <span class="card-badge" id="type-limit-status">Disabled</span>
                </div>
                <div class="toggle" id="toggle-type-limits" onclick="toggleTypeLimits()" style="margin-bottom: 1rem;" title="Enable granular capture quotas based on payload field values">
                    <div class="toggle-switch"></div>
                    <span class="toggle-label">Enable type-based limiting</span>
                </div>
                <div class="form-group">
                    <label class="form-label" title="JSON path to extract the category value (e.g. 'type' or 'payload.event_type')">Field Path (e.g., "type", "event.type")</label>
                    <input type="text" class="form-input" id="type-field-path" 
                           value="type" onchange="updateTypeLimitSettings()">
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label class="form-label" title="Maximum number of captures to keep for each unique type value">Limit Per Type</label>
                        <input type="number" class="form-input" id="limit-per-type" 
                               min="1" step="100" value="5000"
                               onchange="updateTypeLimitSettings()">
                    </div>
                    <div class="form-group">
                        <label class="form-label" title="What to do once the limit for a specific type is reached">On Limit</label>
                        <select class="form-select" id="limit-action" onchange="updateTypeLimitSettings()">
                            <option value="stop" title="Stop recording this specific type until reset">Stop Recording</option>
                            <option value="sample" title="Continue recording this type but at a very low sampling rate">Sample at Low Rate</option>
                        </select>
                    </div>
                </div>
                
                <!-- Code Example Preview -->
                <div style="margin-top: 1.5rem; padding: 1rem; background: var(--bg-tertiary); border-radius: 8px; border: 1px solid var(--border-color);">
                    <div style="font-size: 0.75rem; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 0.75rem; letter-spacing: 0.05em;">Code Configuration Example</div>
                    <pre style="margin: 0; font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: var(--text-primary); overflow-x: auto; line-height: 1.6;"><code id="type-limit-code-example">from Chronicle.integrations import configure_type_limits, TypeLimitConfig

configure_type_limits(TypeLimitConfig(
    field_path="type",
    limit_per_type=5000,
    alert_on_limit=True,
    limit_action="stop",
))</code></pre>
                    <div style="margin-top: 0.5rem; font-size: 0.7rem; color: var(--text-muted);">
                        Copy this code to your application startup to configure type-based limits programmatically.
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Endpoint Management -->
        <div class="card">
            <div class="card-header">
                <span class="card-title">Endpoint Configuration</span>
                <button class="btn" onclick="refreshEndpoints()">↻ Refresh</button>
            </div>
            <div style="margin-bottom: 1rem;">
                <div class="form-group">
                    <label class="form-label">Filter Endpoints</label>
                    <input type="text" class="form-input" id="endpoint-filter" 
                           placeholder="Search endpoints..." oninput="filterEndpoints()">
                </div>
            </div>
            <div class="type-list" id="endpoint-list" style="max-height: 400px;">
                <div class="empty-state">
                    <div class="empty-state-icon">🔍</div>
                    <div>Loading endpoints...</div>
                </div>
            </div>
        </div>
        
        <!-- Function & Type Counts Row -->
        <div class="grid">
            <!-- Function Counts -->
            <div class="card">
                <div class="card-header">
                    <span class="card-title">Captures by Function</span>
                    <button class="btn" onclick="resetAllFunctions()">Reset All</button>
                </div>
                <div class="type-list" id="function-list">
                    <div class="empty-state">
                        <div class="empty-state-icon">📊</div>
                        <div>No function data yet</div>
                    </div>
                </div>
            </div>
            
            <!-- Type Counts -->
            <div class="card">
                <div class="card-header">
                    <span class="card-title">Captures by Type</span>
                    <button class="btn" onclick="resetAllTypes()">Reset All</button>
                </div>
                <div class="type-list" id="type-list">
                    <div class="empty-state">
                        <div class="empty-state-icon">📊</div>
                        <div>No type data yet</div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Alerts -->
        <div class="card">
            <div class="card-header">
                <span class="card-title">Alerts</span>
                <button class="btn" onclick="clearAlerts()">Clear</button>
            </div>
            <div class="alert-list" id="alert-list">
                <div class="empty-state">
                    <div class="empty-state-icon">🔔</div>
                    <div>No alerts</div>
                </div>
            </div>
        </div>
        
        <!-- Recent Requests Table -->
        <div class="card">
            <div class="card-header">
                <span class="card-title">Recent Captures</span>
                <select class="form-select" style="width: auto;" id="requests-limit" onchange="refreshRequests()">
                    <option value="25">Last 25</option>
                    <option value="50">Last 50</option>
                    <option value="100">Last 100</option>
                </select>
            </div>
            <div style="overflow-x: auto;">
                <table class="requests-table">
                    <thead>
                        <tr>
                            <th>Time</th>
                            <th>Method</th>
                            <th>Path</th>
                            <th>Status</th>
                            <th>Duration</th>
                            <th>Type</th>
                        </tr>
                    </thead>
                    <tbody id="requests-table-body">
                        <tr>
                            <td colspan="6" style="text-align: center; color: var(--text-muted);">No captures yet</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </main>
    
    <script>
        const API_BASE = window.location.pathname.replace(/\\/$/, '');
        let refreshInterval;
        
        // State
        let state = {
            captureErrors: true,
            captureSlow: true,
            typeLimitsEnabled: false,
            functionLimitsEnabled: false,
        };
        
        // API calls
        async function api(path, options = {}) {
            const response = await fetch(API_BASE + '/api' + path, {
                headers: { 'Content-Type': 'application/json' },
                ...options,
            });
            return response.json();
        }
        
        // Refresh all data
        async function refreshAll() {
            await Promise.all([
                refreshStats(),
                refreshTypeLimits(),
                refreshFunctionLimits(),
                refreshAlerts(),
                refreshRequests(),
                refreshEndpoints(),
            ]);
        }
        
        async function refreshStats() {
            const data = await api('/stats');
            
            document.getElementById('total-captured').textContent = data.total_captured.toLocaleString();
            document.getElementById('stat-errors').textContent = data.error_count.toLocaleString();
            document.getElementById('stat-avg-duration').textContent = data.avg_duration_ms.toFixed(0) + 'ms';
            document.getElementById('stat-error-rate').textContent = data.error_rate + '%';
            document.getElementById('stat-strategy').textContent = data.sampling_stats?.strategy || 'random';
            
            // Update sampling form
            if (data.sampling_stats) {
                document.getElementById('sampling-strategy').value = data.sampling_stats.strategy;
                document.getElementById('base-rate').value = data.sampling_stats.base_rate;
            }
        }
        
        async function refreshTypeLimits() {
            const data = await api('/type-limits');
            
            state.typeLimitsEnabled = data.enabled;
            updateToggle('toggle-type-limits', data.enabled);
            
            document.getElementById('type-limit-status').textContent = data.enabled ? 'Enabled' : 'Disabled';
            document.getElementById('type-limit-status').className = 'card-badge ' + (data.enabled ? 'badge-active' : '');
            
            if (data.config) {
                document.getElementById('type-field-path').value = data.config.field_path;
                document.getElementById('limit-per-type').value = data.config.limit_per_type;
                document.getElementById('limit-action').value = data.config.limit_action;
            }
            
            // Update code example
            updateCodeExample();
            
            // Render type counts
            const typeList = document.getElementById('type-list');
            const counts = data.counts || {};
            const stoppedTypes = new Set(data.stopped_types || []);
            const limit = data.config?.limit_per_type || 5000;
            
            if (Object.keys(counts).length === 0) {
                typeList.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📊</div><div>No type data yet</div></div>';
            } else {
                typeList.innerHTML = Object.entries(counts)
                    .sort((a, b) => b[1] - a[1])
                    .map(([type, count]) => {
                        const percentage = Math.min((count / limit) * 100, 100);
                        const isStopped = stoppedTypes.has(type);
                        const progressClass = percentage >= 100 ? 'full' : percentage >= 75 ? 'high' : percentage >= 50 ? 'medium' : 'low';
                        
                        return `
                            <div class="type-item">
                                <div class="type-info">
                                    <span class="type-name">${escapeHtml(type)}</span>
                                    <span class="type-count">${count.toLocaleString()} / ${limit.toLocaleString()}</span>
                                </div>
                                <div style="display: flex; align-items: center; gap: 0.5rem;">
                                    <span class="type-status ${isStopped ? 'stopped' : 'active'}">${isStopped ? 'Stopped' : 'Active'}</span>
                                    <button class="btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" 
                                            onclick="resetType('${escapeHtml(type)}')">Reset</button>
                                </div>
                            </div>
                            <div class="progress-bar" style="margin-top: -0.25rem; margin-bottom: 0.5rem;">
                                <div class="progress-fill ${progressClass}" style="width: ${percentage}%"></div>
                            </div>
                        `;
                    }).join('');
            }
        }
        
        async function refreshFunctionLimits() {
            const data = await api('/function-limits');
            
            state.functionLimitsEnabled = data.enabled;
            updateToggle('toggle-function-limits', data.enabled);
            
            document.getElementById('function-limit-status').textContent = data.enabled ? 'Enabled' : 'Disabled';
            document.getElementById('function-limit-status').className = 'card-badge ' + (data.enabled ? 'badge-active' : '');
            
            if (data.config) {
                document.getElementById('function-limit-per-function').value = data.config.limit_per_function;
                document.getElementById('function-limit-action').value = data.config.limit_action;
            }
            
            // Render function counts
            const functionList = document.getElementById('function-list');
            const counts = data.counts || {};
            const stoppedFunctions = new Set(data.stopped_functions || []);
            const limit = data.config?.limit_per_function || 5000;
            
            if (Object.keys(counts).length === 0) {
                functionList.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📊</div><div>No function data yet</div></div>';
            } else {
                functionList.innerHTML = Object.entries(counts)
                    .sort((a, b) => b[1] - a[1])
                    .map(([func, count]) => {
                        const percentage = Math.min((count / limit) * 100, 100);
                        const isStopped = stoppedFunctions.has(func);
                        const progressClass = percentage >= 100 ? 'full' : percentage >= 75 ? 'high' : percentage >= 50 ? 'medium' : 'low';
                        
                        return `
                            <div class="type-item">
                                <div class="type-info">
                                    <span class="type-name">${escapeHtml(func)}</span>
                                    <span class="type-count">${count.toLocaleString()} / ${limit.toLocaleString()}</span>
                                </div>
                                <div style="display: flex; align-items: center; gap: 0.5rem;">
                                    <span class="type-status ${isStopped ? 'stopped' : 'active'}">${isStopped ? 'Stopped' : 'Active'}</span>
                                    <button class="btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" 
                                            onclick="resetFunction('${escapeHtml(func)}')">Reset</button>
                                </div>
                            </div>
                            <div class="progress-bar" style="margin-top: -0.25rem; margin-bottom: 0.5rem;">
                                <div class="progress-fill ${progressClass}" style="width: ${percentage}%"></div>
                            </div>
                        `;
                    }).join('');
            }
        }
        
        async function refreshAlerts() {
            const data = await api('/alerts');
            const alertList = document.getElementById('alert-list');
            
            if (!data.alerts || data.alerts.length === 0) {
                alertList.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🔔</div><div>No alerts</div></div>';
            } else {
                alertList.innerHTML = data.alerts.map(alert => {
                    const isFunctionAlert = alert.function_name !== undefined;
                    const resetAction = isFunctionAlert ? 
                        `<button class="btn alert-action" onclick="resetFunction('${escapeHtml(alert.function_name)}')">Resume</button>` :
                        `<button class="btn alert-action" onclick="resetType('${escapeHtml(alert.type_value)}')">Resume</button>`;
                    
                    return `
                        <div class="alert-item">
                            <div class="alert-icon">⚠️</div>
                            <div class="alert-content">
                                <div class="alert-message">${escapeHtml(alert.message)}</div>
                                <div class="alert-time">${new Date(alert.timestamp).toLocaleString()}</div>
                            </div>
                            ${resetAction}
                        </div>
                    `;
                }).join('');
            }
        }
        
        async function refreshRequests() {
            const limit = document.getElementById('requests-limit').value;
            const data = await api('/requests?limit=' + limit);
            const tbody = document.getElementById('requests-table-body');
            
            if (!data.requests || data.requests.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">No captures yet</td></tr>';
            } else {
                tbody.innerHTML = data.requests.map(req => {
                    const methodClass = 'method-' + req.method.toLowerCase();
                    const statusClass = req.status_code >= 500 ? 'status-5xx' : req.status_code >= 400 ? 'status-4xx' : 'status-2xx';
                    const typeValue = extractType(req.request_body);
                    
                    return `
                        <tr>
                            <td>${new Date(req.timestamp).toLocaleTimeString()}</td>
                            <td><span class="method ${methodClass}">${req.method}</span></td>
                            <td style="max-width: 300px; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(req.path)}</td>
                            <td class="${statusClass}">${req.status_code || '-'}</td>
                            <td>${req.duration_ms?.toFixed(0) || 0}ms</td>
                            <td style="color: var(--accent-purple);">${typeValue ? escapeHtml(typeValue) : '-'}</td>
                        </tr>
                    `;
                }).join('');
            }
        }
        
        function extractType(body) {
            if (!body || typeof body !== 'object') return null;
            const fieldPath = document.getElementById('type-field-path').value;
            const parts = fieldPath.split('.');
            let current = body;
            for (const part of parts) {
                if (current && typeof current === 'object' && part in current) {
                    current = current[part];
                } else {
                    return null;
                }
            }
            return current;
        }
        
        // Settings updates
        async function updateSamplingSettings() {
            await api('/sampling', {
                method: 'POST',
                body: JSON.stringify({
                    strategy: document.getElementById('sampling-strategy').value,
                    base_rate: parseFloat(document.getElementById('base-rate').value),
                    latency_threshold_ms: parseFloat(document.getElementById('latency-threshold').value),
                    always_capture_errors: state.captureErrors,
                    always_capture_slow: state.captureSlow,
                }),
            });
            await refreshStats();
        }
        
        function updateCodeExample() {
            const fieldPath = document.getElementById('type-field-path').value;
            const limitPerType = parseInt(document.getElementById('limit-per-type').value);
            const limitAction = document.getElementById('limit-action').value;
            const enabled = state.typeLimitsEnabled;
            
            const code = `from Chronicle.integrations import configure_type_limits, TypeLimitConfig

configure_type_limits(TypeLimitConfig(
    field_path="${fieldPath}",
    limit_per_type=${limitPerType},
    alert_on_limit=True,
    limit_action="${limitAction}",
))`;
            
            document.getElementById('type-limit-code-example').textContent = code;
        }
        
        async function updateTypeLimitSettings() {
            await api('/type-limits', {
                method: 'POST',
                body: JSON.stringify({
                    enabled: state.typeLimitsEnabled,
                    field_path: document.getElementById('type-field-path').value,
                    limit_per_type: parseInt(document.getElementById('limit-per-type').value),
                    limit_action: document.getElementById('limit-action').value,
                }),
            });
            updateCodeExample();
            await refreshTypeLimits();
        }
        
        // Toggle functions
        function updateToggle(id, active) {
            const toggle = document.getElementById(id);
            if (active) {
                toggle.classList.add('active');
            } else {
                toggle.classList.remove('active');
            }
        }
        
        async function toggleErrors() {
            state.captureErrors = !state.captureErrors;
            updateToggle('toggle-errors', state.captureErrors);
            await updateSamplingSettings();
        }
        
        async function toggleSlow() {
            state.captureSlow = !state.captureSlow;
            updateToggle('toggle-slow', state.captureSlow);
            await updateSamplingSettings();
        }
        
        async function toggleTypeLimits() {
            state.typeLimitsEnabled = !state.typeLimitsEnabled;
            updateToggle('toggle-type-limits', state.typeLimitsEnabled);
            await updateTypeLimitSettings();
        }
        
        async function toggleFunctionLimits() {
            state.functionLimitsEnabled = !state.functionLimitsEnabled;
            updateToggle('toggle-function-limits', state.functionLimitsEnabled);
            await updateFunctionLimitSettings();
        }
        
        async function updateFunctionLimitSettings() {
            await api('/function-limits', {
                method: 'POST',
                body: JSON.stringify({
                    enabled: state.functionLimitsEnabled,
                    limit_per_function: parseInt(document.getElementById('function-limit-per-function').value),
                    limit_action: document.getElementById('function-limit-action').value,
                }),
            });
            await refreshFunctionLimits();
        }
        
        async function resetFunction(functionName) {
            await api('/function-limits/reset/' + encodeURIComponent(functionName), { method: 'POST' });
            await Promise.all([refreshFunctionLimits(), refreshAlerts()]);
        }
        
        async function resetAllFunctions() {
            if (confirm('Reset all function counts?')) {
                await api('/function-limits/reset-all', { method: 'POST' });
                await Promise.all([refreshFunctionLimits(), refreshAlerts()]);
            }
        }
        
        // Actions
        async function clearCaptures() {
            if (confirm('Clear all captured requests?')) {
                await api('/clear', { method: 'POST' });
                await refreshAll();
            }
        }
        
        async function clearAlerts() {
            await api('/alerts/clear', { method: 'POST' });
            await refreshAlerts();
        }
        
        async function resetType(typeValue) {
            await api('/type-limits/reset/' + encodeURIComponent(typeValue), { method: 'POST' });
            await Promise.all([refreshTypeLimits(), refreshAlerts()]);
        }
        
        async function resetAllTypes() {
            if (confirm('Reset all type counts?')) {
                await api('/type-limits/reset-all', { method: 'POST' });
                await Promise.all([refreshTypeLimits(), refreshAlerts()]);
            }
        }
        
        // Endpoint management
        let allEndpoints = [];
        
        async function refreshEndpoints() {
            const data = await api('/endpoints');
            allEndpoints = data.endpoints || [];
            renderEndpoints();
        }
        
        function filterEndpoints() {
            renderEndpoints();
        }
        
        function renderEndpoints() {
            const filter = document.getElementById('endpoint-filter').value.toLowerCase();
            const filtered = allEndpoints.filter(ep => 
                ep.path.toLowerCase().includes(filter) || 
                ep.method.toLowerCase().includes(filter)
            );
            
            const endpointList = document.getElementById('endpoint-list');
            
            if (filtered.length === 0) {
                endpointList.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🔍</div><div>No endpoints found</div></div>';
                return;
            }
            
            endpointList.innerHTML = filtered.map(ep => {
                const methodClass = 'method-' + ep.method.toLowerCase();
                const configBadge = ep.has_custom_config ? 
                    '<span class="type-status active" style="margin-left: 0.5rem;">Custom</span>' : 
                    '<span class="type-status" style="margin-left: 0.5rem; background: rgba(139, 149, 165, 0.15); color: var(--text-muted);">Global</span>';
                
                const config = ep.config;
                const configDisplay = config ? `
                    <div style="margin-top: 0.5rem; font-size: 0.75rem; color: var(--text-muted);">
                        Field: <code style="color: var(--accent-cyan);">${escapeHtml(config.field_path)}</code> | 
                        Limit: <code style="color: var(--accent-cyan);">${config.limit_per_type}</code> | 
                        Action: <code style="color: var(--accent-cyan);">${config.limit_action}</code>
                    </div>
                ` : '<div style="margin-top: 0.5rem; font-size: 0.75rem; color: var(--text-muted);">Type limits disabled</div>';
                
                return `
                    <div class="endpoint-config" style="margin-bottom: 1rem;">
                        <div class="endpoint-header">
                            <div style="display: flex; align-items: center; gap: 0.75rem;">
                                <span class="method ${methodClass}">${ep.method}</span>
                                <span class="endpoint-path">${escapeHtml(ep.path)}</span>
                                ${configBadge}
                            </div>
                            <div style="display: flex; align-items: center; gap: 0.5rem;">
                                <span style="font-size: 0.75rem; color: var(--text-muted);">${ep.capture_count} captures</span>
                                ${ep.has_custom_config ? 
                                    `<button class="btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="removeEndpointConfig('${escapeHtml(ep.path)}')">Remove Custom</button>` :
                                    `<button class="btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="showEndpointConfig('${escapeHtml(ep.path)}', '${ep.method}')">Set Custom</button>`
                                }
                            </div>
                        </div>
                        ${configDisplay}
                    </div>
                `;
            }).join('');
        }
        
        async function showEndpointConfig(path, method) {
            // Get current global config
            const typeLimitsData = await api('/type-limits');
            const globalConfig = typeLimitsData.config || {
                field_path: 'type',
                limit_per_type: 5000,
                limit_action: 'stop',
            };
            
            const fieldPath = prompt('Field Path (e.g., "type", "payload.type"):', globalConfig.field_path);
            if (fieldPath === null) return;
            
            const limitPerType = prompt('Limit Per Type:', globalConfig.limit_per_type);
            if (limitPerType === null) return;
            
            const limitAction = confirm('Stop recording when limit reached? (OK = Stop, Cancel = Sample at low rate)') ? 'stop' : 'sample';
            
            await setEndpointConfig(path, {
                field_path: fieldPath,
                limit_per_type: parseInt(limitPerType),
                limit_action: limitAction,
            });
        }
        
        async function setEndpointConfig(path, config) {
            await api('/endpoints/' + encodeURIComponent(path) + '/config', {
                method: 'POST',
                body: JSON.stringify(config),
            });
            await Promise.all([refreshEndpoints(), refreshTypeLimits()]);
        }
        
        async function removeEndpointConfig(path) {
            if (confirm(`Remove custom configuration for ${path}?`)) {
                await api('/endpoints/' + encodeURIComponent(path) + '/config', { method: 'DELETE' });
                await Promise.all([refreshEndpoints(), refreshTypeLimits()]);
            }
        }
        
        // Utility
        function escapeHtml(str) {
            if (str === null || str === undefined) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        }
        
        // Initialize
        updateToggle('toggle-errors', state.captureErrors);
        updateToggle('toggle-slow', state.captureSlow);
        
        // Add event listeners to update code example in real-time
        document.getElementById('type-field-path').addEventListener('input', updateCodeExample);
        document.getElementById('limit-per-type').addEventListener('input', updateCodeExample);
        document.getElementById('limit-action').addEventListener('change', updateCodeExample);
        
        refreshAll();
        refreshInterval = setInterval(refreshAll, 5000);
    </script>
</body>
</html>
'''


def create_chronicle_router() -> APIRouter:
    """Create the API router for the Chronicle dashboard."""
    router = APIRouter()
    
    @router.get("/", response_class=HTMLResponse)
    async def dashboard():
        """Serve the dashboard HTML."""
        return HTMLResponse(content=DASHBOARD_HTML)
    
    @router.get("/api/stats")
    async def get_stats():
        """Get capture statistics."""
        return get_capture_stats()
    
    @router.get("/api/requests")
    async def get_requests(limit: int = 25):
        """Get recent captured requests."""
        requests = get_captured_requests(limit=limit)
        return {
            "requests": [r.to_dict() for r in requests],
            "total": len(_captured_requests),
        }
    
    @router.post("/api/clear")
    async def clear_all():
        """Clear all captured requests."""
        count = clear_captured_requests()
        return {"cleared": count}
    
    @router.get("/api/sampling")
    async def get_sampling():
        """Get current sampling configuration."""
        sampler = get_sampler()
        return {
            "config": {
                "strategy": sampler.config.strategy.value,
                "base_rate": sampler.config.base_rate,
                "always_capture_errors": sampler.config.always_capture_errors,
                "always_capture_slow": sampler.config.always_capture_slow,
                "latency_threshold_ms": sampler.config.latency_threshold_ms,
                "head_count": sampler.config.head_count,
                "max_patterns_per_endpoint": sampler.config.max_patterns_per_endpoint,
            },
            "stats": sampler.get_stats(),
        }
    
    @router.post("/api/sampling")
    async def update_sampling(request: Request):
        """Update sampling configuration."""
        data = await request.json()
        
        # Get current sampler config as base
        current = get_sampler().config
        
        # Build new config
        strategy_str = data.get("strategy", current.strategy.value)
        strategy = SamplingStrategy(strategy_str)
        
        new_config = SamplingConfig(
            strategy=strategy,
            base_rate=data.get("base_rate", current.base_rate),
            always_capture_errors=data.get("always_capture_errors", current.always_capture_errors),
            always_capture_slow=data.get("always_capture_slow", current.always_capture_slow),
            latency_threshold_ms=data.get("latency_threshold_ms", current.latency_threshold_ms),
            head_count=data.get("head_count", current.head_count),
            max_patterns_per_endpoint=data.get("max_patterns_per_endpoint", current.max_patterns_per_endpoint),
        )
        
        configure_sampling(new_config)
        return {"success": True, "config": new_config.__dict__}
    
    @router.get("/api/type-limits")
    async def get_type_limits():
        """Get type limiting configuration and state."""
        limiter = get_type_limiter()
        return limiter.get_stats()
    
    @router.post("/api/type-limits")
    async def update_type_limits(request: Request):
        """Update type limiting configuration."""
        data = await request.json()
        limiter = get_type_limiter()
        
        if "enabled" in data:
            if data["enabled"]:
                limiter.enable()
            else:
                limiter.disable()
        
        # Update config
        config = TypeLimitConfig(
            field_path=data.get("field_path", limiter.config.field_path),
            limit_per_type=data.get("limit_per_type", limiter.config.limit_per_type),
            alert_on_limit=data.get("alert_on_limit", limiter.config.alert_on_limit),
            limit_action=data.get("limit_action", limiter.config.limit_action),
            overflow_sample_rate=data.get("overflow_sample_rate", limiter.config.overflow_sample_rate),
        )
        limiter.set_config(config)
        
        return {"success": True}
    
    @router.post("/api/type-limits/reset/{type_value:path}")
    async def reset_type(type_value: str):
        """Reset count for a specific type."""
        limiter = get_type_limiter()
        limiter.reset_type(type_value)
        return {"success": True, "reset": type_value}
    
    @router.post("/api/type-limits/reset-all")
    async def reset_all_types():
        """Reset all type counts."""
        limiter = get_type_limiter()
        limiter.reset_all()
        return {"success": True}
    
    @router.get("/api/function-limits")
    async def get_function_limits():
        """Get function limiting configuration and state."""
        limiter = get_function_limiter()
        return limiter.get_stats()
    
    @router.post("/api/function-limits")
    async def update_function_limits(request: Request):
        """Update function limiting configuration."""
        data = await request.json()
        limiter = get_function_limiter()
        
        if "enabled" in data:
            if data["enabled"]:
                limiter.enable()
            else:
                limiter.disable()
        
        # Update config
        config = FunctionLimitConfig(
            limit_per_function=data.get("limit_per_function", limiter.config.limit_per_function),
            alert_on_limit=data.get("alert_on_limit", limiter.config.alert_on_limit),
            limit_action=data.get("limit_action", limiter.config.limit_action),
            overflow_sample_rate=data.get("overflow_sample_rate", limiter.config.overflow_sample_rate),
        )
        limiter.set_config(config)
        
        return {"success": True}
    
    @router.post("/api/function-limits/reset/{function_name:path}")
    async def reset_function(function_name: str):
        """Reset count for a specific function."""
        limiter = get_function_limiter()
        limiter.reset_function(function_name)
        return {"success": True, "reset": function_name}
    
    @router.post("/api/function-limits/reset-all")
    async def reset_all_functions():
        """Reset all function counts."""
        limiter = get_function_limiter()
        limiter.reset_all()
        return {"success": True}
    
    @router.get("/api/alerts")
    async def get_alerts(limit: int = 100):
        """Get recent alerts from both type and function limiters."""
        type_limiter = get_type_limiter()
        function_limiter = get_function_limiter()
        
        type_alerts = type_limiter.get_alerts(limit)
        function_alerts = function_limiter.get_alerts(limit)
        
        # Combine and sort by timestamp
        all_alerts = type_alerts + function_alerts
        all_alerts.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        
        return {"alerts": all_alerts[:limit]}
    
    @router.post("/api/alerts/clear")
    async def clear_alerts():
        """Clear all alerts from both limiters."""
        type_limiter = get_type_limiter()
        function_limiter = get_function_limiter()
        count = type_limiter.clear_alerts() + function_limiter.clear_alerts()
        return {"cleared": count}
    
    @router.get("/api/endpoints")
    async def get_endpoints():
        """Get all captured endpoints with their configuration."""
        from .fastapi import _captured_requests
        
        # Get unique endpoints from captured requests
        endpoints = {}
        for req in _captured_requests:
            endpoint_key = f"{req.method} {req.path}"
            if endpoint_key not in endpoints:
                endpoints[endpoint_key] = {
                    "method": req.method,
                    "path": req.path,
                    "count": 0,
                }
            endpoints[endpoint_key]["count"] += 1
        
        # Get type limiter config for each endpoint
        limiter = get_type_limiter()
        endpoint_list = []
        for key, info in sorted(endpoints.items()):
            endpoint_path = info["path"]
            # Check if this endpoint has custom config
            has_custom = endpoint_path in limiter._endpoints
            custom_config = limiter._endpoints.get(endpoint_path)
            
            endpoint_list.append({
                "method": info["method"],
                "path": endpoint_path,
                "key": key,
                "capture_count": info["count"],
                "has_custom_config": has_custom,
                "config": {
                    "field_path": custom_config.field_path if custom_config else limiter.config.field_path,
                    "limit_per_type": custom_config.limit_per_type if custom_config else limiter.config.limit_per_type,
                    "limit_action": custom_config.limit_action if custom_config else limiter.config.limit_action,
                } if limiter._enabled else None,
            })
        
        return {"endpoints": endpoint_list}
    
    @router.post("/api/endpoints/{endpoint_path:path}/config")
    async def set_endpoint_config(endpoint_path: str, request: Request):
        """Set custom type limit configuration for a specific endpoint."""
        data = await request.json()
        limiter = get_type_limiter()
        
        # Create custom config for this endpoint
        config = TypeLimitConfig(
            field_path=data.get("field_path", limiter.config.field_path),
            limit_per_type=data.get("limit_per_type", limiter.config.limit_per_type),
            alert_on_limit=data.get("alert_on_limit", limiter.config.alert_on_limit),
            limit_action=data.get("limit_action", limiter.config.limit_action),
            overflow_sample_rate=data.get("overflow_sample_rate", limiter.config.overflow_sample_rate),
        )
        
        limiter.set_config(config, endpoint=endpoint_path)
        limiter.enable(endpoint=endpoint_path, config=config)
        
        return {"success": True, "endpoint": endpoint_path, "config": config.__dict__}
    
    @router.delete("/api/endpoints/{endpoint_path:path}/config")
    async def remove_endpoint_config(endpoint_path: str):
        """Remove custom configuration for an endpoint (revert to global)."""
        limiter = get_type_limiter()
        limiter.disable(endpoint=endpoint_path)
        return {"success": True, "endpoint": endpoint_path}
    
    return router


def mount_chronicle_dashboard(
    app: FastAPI,
    path: str = "/_chronicle",
    enabled: bool = True,
    auth_callback: Optional[Callable[[Request], bool]] = None,
) -> Optional[APIRouter]:
    """
    Mount the Chronicle dashboard on a FastAPI application.
    
    Args:
        app: FastAPI application instance
        path: URL path to mount the dashboard at (default: "/_chronicle")
        enabled: Whether the dashboard is enabled (default: True)
        auth_callback: Optional callback to check if request is authorized.
                       Receives Request, returns True if authorized.
    
    Usage:
        from fastapi import FastAPI
        from Chronicle.integrations.ui import mount_chronicle_dashboard
        
        app = FastAPI()
        mount_chronicle_dashboard(app)
        
        # With auth
        def check_auth(request):
            return request.headers.get("X-Admin-Key") == os.getenv("ADMIN_KEY")
        
        mount_chronicle_dashboard(app, auth_callback=check_auth)
    
    Returns:
        The mounted router, or None if not enabled.
    """
    if not enabled:
        return None
    
    router = create_chronicle_router()
    
    # Wrap with auth if provided
    if auth_callback:
        original_routes = router.routes.copy()
        router = APIRouter()
        
        @router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
        async def auth_wrapper(request: Request, path: str = ""):
            if not auth_callback(request):
                raise HTTPException(status_code=401, detail="Unauthorized")
            # Continue to actual route
            for route in original_routes:
                if hasattr(route, "path") and route.matches(request.scope)[0]:
                    return await route.endpoint(request)
            raise HTTPException(status_code=404, detail="Not found")
        
        # Simpler approach: add middleware to router
        router = create_chronicle_router()
    
    # Ensure path doesn't have trailing slash for prefix
    prefix = path.rstrip("/")
    
    # Automatically exclude dashboard paths from capture
    sampler = get_sampler()
    if prefix:
        # Add the prefix to exclusion list (will match prefix and all sub-paths)
        # The middleware checks for exact match or startswith, so this covers:
        # /_chronicle, /_chronicle/, /_chronicle/api/stats, etc.
        sampler.config.never_capture_endpoints.add(prefix)
        # Also add with trailing slash for explicit matching
        if not prefix.endswith("/"):
            sampler.config.never_capture_endpoints.add(f"{prefix}/")
    
    # Mount the router (routes will be at prefix + router path, e.g., /chronicle/ and /chronicle/api/stats)
    app.include_router(router, prefix=prefix, tags=["chronicle"])
    
    # Also handle the path without trailing slash by serving the dashboard directly
    # This ensures /_chronicle works in addition to /_chronicle/
    if prefix:
        @app.get(prefix, response_class=HTMLResponse, include_in_schema=False)
        async def dashboard_no_slash():
            """Serve dashboard at the prefix (without trailing slash)."""
            return HTMLResponse(content=DASHBOARD_HTML)
        
        # If prefix starts with /_, also add a redirect from the version without _
        if prefix.startswith("/_"):
            no_underscore = "/" + prefix[2:]
            @app.get(no_underscore, include_in_schema=False)
            async def redirect_to_underscore():
                """Redirect /chronicle to /_chronicle"""
                return RedirectResponse(url=prefix, status_code=307)
    
    return router


# Export the type limiter check function for use in middleware
def check_type_limit(endpoint: str, request_body: Any) -> tuple[bool, Optional[str]]:
    """
    Check if request should be captured based on type limits.
    
    Use this in your capture logic:
        should_capture, type_value = check_type_limit(endpoint, body)
        if not should_capture:
            return  # Skip capture
    
    Returns:
        (should_capture, type_value) tuple
    """
    return get_type_limiter().should_capture(endpoint, request_body)
