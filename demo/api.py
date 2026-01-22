"""
FastAPI backend for the Chronicle Demo Task Queue System.

Provides REST API endpoints for task management with Chronicle capture
and OpenTelemetry instrumentation.

Now includes ChronicleMiddleware for automatic request/response capture
with smart sampling support.
"""

from __future__ import annotations

import random
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Add parent directory to path for Chronicle imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Support running from either the demo directory or parent directory
try:
    from .capture import capture, configure_storage, get_storage
    from .database import TaskDatabase
    from .models import TaskCreate, TaskPriority, TaskStatus, TaskUpdate
except ImportError:
    from capture import capture, configure_storage, get_storage
    from database import TaskDatabase
    from models import TaskCreate, TaskPriority, TaskStatus, TaskUpdate

# Import Chronicle middleware and sampling
try:
    from integrations import (
        ChronicleMiddleware,
        SamplingConfig,
        SamplingStrategy,
        configure_sampling,
        get_capture_stats,
        get_captured_requests,
        clear_captured_requests,
        # UI Dashboard
        mount_chronicle_dashboard,
        configure_type_limits,
        TypeLimitConfig,
        # Function Limits
        configure_function_limits,
        FunctionLimitConfig,
    )
    from integrations.fastapi import add_capture_callback
    CHRONICLE_MIDDLEWARE_AVAILABLE = True
except ImportError:
    CHRONICLE_MIDDLEWARE_AVAILABLE = False

# Configure OpenTelemetry
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    # Set up tracing
    resource = Resource.create({"service.name": "chronicle-demo-api"})
    provider = TracerProvider(resource=resource)

    # Console exporter for demo visibility
    console_exporter = ConsoleSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(console_exporter))

    # Optional OTLP exporter (e.g., Jaeger, Zipkin)
    try:
        otlp_exporter = OTLPSpanExporter(endpoint="localhost:4317", insecure=True)
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    except Exception:
        pass  # OTLP endpoint not available

    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer(__name__)
    OTEL_ENABLED = True
except ImportError:
    tracer = None
    OTEL_ENABLED = False


# Database instance
db: Optional[TaskDatabase] = None

# Error injection configuration
error_injection_rate: float = 0.0


def get_db() -> TaskDatabase:
    """Get the database instance."""
    global db
    if db is None:
        # TaskDatabase will read config from environment variables
        db = TaskDatabase()
    return db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    global db
    # TaskDatabase will read config from environment variables
    db = TaskDatabase()
    # CaptureStorage will also read config from environment variables
    configure_storage()
    yield
    # Shutdown (cleanup if needed)


# Create FastAPI app
app = FastAPI(
    title="Chronicle Demo API",
    description="Task Queue System demonstrating Chronicle's capture and analysis capabilities",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware for Streamlit frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add Chronicle middleware with smart sampling
if CHRONICLE_MIDDLEWARE_AVAILABLE:
    # Configure sampling strategy
    configure_sampling(SamplingConfig(
        strategy=SamplingStrategy.CLUSTERING,  # Capture diverse patterns
        base_rate=0.2,  # 20% baseline sampling
        always_capture_errors=True,  # 100% of errors
        always_capture_slow=True,
        latency_threshold_ms=500,  # Capture slow requests
        max_patterns_per_endpoint=50,  # Track up to 50 unique patterns
        never_capture_endpoints={"/health", "/metrics", "/docs", "/openapi.json", "/_chronicle"},
    ))

    # Add the middleware
    app.add_middleware(
        ChronicleMiddleware,
        capture_request_body=True,
        capture_response_body=True,
        max_body_size=65536,  # 64KB limit
    )

    # Configure type-based capture limits
    # This allows limiting captures per "type" field value (top-level in request body)
    configure_type_limits(TypeLimitConfig(
        field_path="type",         # Extract type from top-level "type" field
        limit_per_type=5000,       # Capture up to 5000 of each type
        alert_on_limit=True,       # Show alert when limit reached
        limit_action="stop",       # Stop recording that type when limit hit
    ))
    
    # Configure function-based capture limits
    # This limits captures per function name (prevents DB storage after limit)
    configure_function_limits(FunctionLimitConfig(
        limit_per_function=5000,   # Capture up to 5000 per function
        alert_on_limit=True,       # Show alert when limit reached
        limit_action="stop",       # Stop recording to DB when limit hit
    ))

    # Mount the Chronicle dashboard UI at /_chronicle
    # Access at http://localhost:8000/_chronicle
    mount_chronicle_dashboard(
        app,
        path="/_chronicle",
        enabled=True,  # Set to False or use env var to disable in production
    )

# Instrument with OpenTelemetry
if OTEL_ENABLED:
    # Exclude dashboard, health, and static docs from OTel tracing to reduce noise
    excluded_urls = "health,metrics,_chronicle,docs,openapi.json"
    FastAPIInstrumentor.instrument_app(app, excluded_urls=excluded_urls)


# Pydantic models for API
class TaskCreateRequest(BaseModel):
    """Request model for creating a task."""

    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    priority: str = Field(default="medium")
    payload: dict = Field(default_factory=dict)
    max_retries: int = Field(default=3, ge=0, le=10)


class TaskUpdateRequest(BaseModel):
    """Request model for updating a task."""

    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    priority: Optional[str] = None
    payload: Optional[dict] = None


class TaskResponse(BaseModel):
    """Response model for a task."""

    id: str
    title: str
    description: str
    status: str
    priority: str
    payload: dict
    result: Optional[dict]
    error_message: Optional[str]
    created_at: str
    updated_at: str
    claimed_at: Optional[str]
    completed_at: Optional[str]
    claimed_by: Optional[str]
    retry_count: int
    max_retries: int


class ProcessTaskRequest(BaseModel):
    """Request model for processing a task."""

    worker_id: str = Field(..., min_length=1)
    simulate_duration_ms: Optional[int] = Field(default=None, ge=0, le=30000)


class ErrorInjectionConfig(BaseModel):
    """Configuration for error injection."""

    rate: float = Field(..., ge=0.0, le=1.0)


class StatsResponse(BaseModel):
    """Response model for statistics."""

    tasks: dict
    captures: dict


def _should_inject_error() -> bool:
    """Check if we should inject an error based on current rate."""
    return random.random() < error_injection_rate


def _maybe_inject_error(operation: str) -> None:
    """Potentially inject an error for demonstration."""
    if _should_inject_error():
        error_types = [
            ("ValidationError", f"Simulated validation error in {operation}"),
            ("DatabaseError", f"Simulated database error in {operation}"),
            ("TimeoutError", f"Simulated timeout in {operation}"),
            ("ProcessingError", f"Simulated processing error in {operation}"),
        ]
        error_type, message = random.choice(error_types)
        raise HTTPException(status_code=500, detail={"error": error_type, "message": message})


# =============================================================================
# Task CRUD Endpoints
# =============================================================================


@app.post("/tasks", response_model=TaskResponse, tags=["Tasks"])
@capture
def create_task(request: TaskCreateRequest) -> TaskResponse:
    """Create a new task in the queue."""
    _maybe_inject_error("create_task")

    try:
        priority = TaskPriority(request.priority)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid priority: {request.priority}")

    task_create = TaskCreate(
        title=request.title,
        description=request.description,
        priority=priority,
        payload=request.payload,
        max_retries=request.max_retries,
    )

    task = get_db().create_task(task_create)
    return TaskResponse(**task.to_dict())


@app.get("/tasks", response_model=List[TaskResponse], tags=["Tasks"])
@capture
def list_tasks(
    status: Optional[str] = Query(None, description="Filter by status"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> List[TaskResponse]:
    """List tasks with optional filtering."""
    _maybe_inject_error("list_tasks")

    task_status = None
    task_priority = None

    if status:
        try:
            task_status = TaskStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    if priority:
        try:
            task_priority = TaskPriority(priority)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid priority: {priority}")

    tasks = get_db().list_tasks(
        status=task_status,
        priority=task_priority,
        limit=limit,
        offset=offset,
    )
    return [TaskResponse(**task.to_dict()) for task in tasks]


@app.get("/tasks/{task_id}", response_model=TaskResponse, tags=["Tasks"])
@capture
def get_task(task_id: str) -> TaskResponse:
    """Get a task by ID."""
    _maybe_inject_error("get_task")

    task = get_db().get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return TaskResponse(**task.to_dict())


@app.patch("/tasks/{task_id}", response_model=TaskResponse, tags=["Tasks"])
@capture
def update_task(task_id: str, request: TaskUpdateRequest) -> TaskResponse:
    """Update a task."""
    _maybe_inject_error("update_task")

    priority = None
    if request.priority:
        try:
            priority = TaskPriority(request.priority)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid priority: {request.priority}")

    update = TaskUpdate(
        title=request.title,
        description=request.description,
        priority=priority,
        payload=request.payload,
    )

    task = get_db().update_task(task_id, update)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return TaskResponse(**task.to_dict())


@app.delete("/tasks/{task_id}", tags=["Tasks"])
@capture
def delete_task(task_id: str) -> dict:
    """Delete a task."""
    _maybe_inject_error("delete_task")

    if not get_db().delete_task(task_id):
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return {"deleted": task_id}


# =============================================================================
# Task Workflow Endpoints
# =============================================================================


@app.post("/tasks/{task_id}/claim", response_model=TaskResponse, tags=["Workflow"])
@capture
def claim_task(task_id: str, request: ProcessTaskRequest) -> TaskResponse:
    """Claim a pending task for processing."""
    _maybe_inject_error("claim_task")

    task = get_db().claim_task(task_id, request.worker_id)
    if not task:
        raise HTTPException(
            status_code=409, detail=f"Task {task_id} is not available for claiming"
        )
    return TaskResponse(**task.to_dict())


@app.post("/tasks/claim-next", response_model=TaskResponse, tags=["Workflow"])
@capture
def claim_next_task(
    worker_id: str = Query(..., description="Worker identifier"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
) -> TaskResponse:
    """Claim the next available task from the queue."""
    _maybe_inject_error("claim_next_task")

    task_priority = None
    if priority:
        try:
            task_priority = TaskPriority(priority)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid priority: {priority}")

    task = get_db().claim_next_task(worker_id, task_priority)
    if not task:
        raise HTTPException(status_code=404, detail="No tasks available")
    return TaskResponse(**task.to_dict())


@app.post("/tasks/{task_id}/process", response_model=TaskResponse, tags=["Workflow"])
@capture
def process_task(task_id: str, request: ProcessTaskRequest) -> TaskResponse:
    """Start processing a claimed task."""
    _maybe_inject_error("process_task")

    task = get_db().start_processing(task_id)
    if not task:
        raise HTTPException(
            status_code=409, detail=f"Task {task_id} cannot be started (not claimed)"
        )

    # Simulate processing time if requested
    if request.simulate_duration_ms:
        time.sleep(request.simulate_duration_ms / 1000)

    return TaskResponse(**task.to_dict())


@app.post("/tasks/{task_id}/complete", response_model=TaskResponse, tags=["Workflow"])
@capture
def complete_task(task_id: str, result: Optional[dict] = None) -> TaskResponse:
    """Mark a task as completed."""
    _maybe_inject_error("complete_task")

    task = get_db().complete_task(task_id, result)
    if not task:
        raise HTTPException(
            status_code=409, detail=f"Task {task_id} cannot be completed (not processing)"
        )
    return TaskResponse(**task.to_dict())


@app.post("/tasks/{task_id}/fail", response_model=TaskResponse, tags=["Workflow"])
@capture
def fail_task(task_id: str, error_message: str = Query(...)) -> TaskResponse:
    """Mark a task as failed (may retry if attempts remain)."""
    task = get_db().fail_task(task_id, error_message)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return TaskResponse(**task.to_dict())


# =============================================================================
# Stats and Admin Endpoints
# =============================================================================


@app.get("/stats", response_model=StatsResponse, tags=["Admin"])
def get_stats() -> StatsResponse:
    """Get task queue and capture statistics."""
    return StatsResponse(
        tasks=get_db().get_stats(),
        captures=get_storage().get_stats(),
    )


@app.post("/admin/clear-tasks", tags=["Admin"])
def clear_tasks() -> dict:
    """Clear all tasks (admin only)."""
    count = get_db().clear_all()
    return {"deleted": count}


@app.post("/admin/clear-captures", tags=["Admin"])
def clear_captures() -> dict:
    """Clear all Chronicle captures (admin only)."""
    count = get_storage().clear()
    return {"deleted": count}


@app.get("/admin/error-injection", tags=["Admin"])
def get_error_injection() -> dict:
    """Get current error injection rate."""
    return {"rate": error_injection_rate}


@app.post("/admin/error-injection", tags=["Admin"])
def set_error_injection(config: ErrorInjectionConfig) -> dict:
    """Set error injection rate (0.0 to 1.0)."""
    global error_injection_rate
    error_injection_rate = config.rate
    return {"rate": error_injection_rate}


@app.get("/health", tags=["Admin"])
def health_check() -> dict:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "otel_enabled": OTEL_ENABLED,
        "chronicle_middleware": CHRONICLE_MIDDLEWARE_AVAILABLE,
        "chronicle_dashboard": "/_chronicle" if CHRONICLE_MIDDLEWARE_AVAILABLE else None,
        "timestamp": datetime.utcnow().isoformat(),
    }


# =============================================================================
# Chronicle Capture Endpoints
# =============================================================================


@app.get("/captures", tags=["Chronicle"])
def list_captures(
    function_name: Optional[str] = Query(None),
    has_error: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """List captured function calls."""
    calls = get_storage().get_calls(
        function_name=function_name,
        has_error=has_error,
        limit=limit,
        offset=offset,
    )
    return {
        "calls": [call.to_dict() for call in calls],
        "count": len(calls),
    }


@app.get("/captures/functions", tags=["Chronicle"])
def list_captured_functions() -> dict:
    """List all captured function names with counts."""
    stats = get_storage().get_stats()
    return {"functions": stats.get("by_function", {})}


# =============================================================================
# Chronicle Middleware Endpoints (Full Request/Response Capture)
# =============================================================================


@app.get("/middleware/requests", tags=["Middleware"])
def list_middleware_requests(
    method: Optional[str] = Query(None, description="Filter by HTTP method"),
    path_prefix: Optional[str] = Query(None, description="Filter by path prefix"),
    status_code: Optional[int] = Query(None, description="Filter by status code"),
    has_error: Optional[bool] = Query(None, description="Filter by error presence"),
    limit: int = Query(50, ge=1, le=500),
) -> dict:
    """
    List requests captured by Chronicle middleware.

    This shows full HTTP request/response data with smart sampling.
    """
    if not CHRONICLE_MIDDLEWARE_AVAILABLE:
        return {"error": "Chronicle middleware not available", "requests": []}

    requests = get_captured_requests(
        limit=limit,
        method=method,
        path_prefix=path_prefix,
        status_code=status_code,
        has_error=has_error,
    )

    return {
        "requests": [r.to_dict() for r in requests],
        "count": len(requests),
        "middleware_enabled": True,
    }


@app.get("/middleware/stats", tags=["Middleware"])
def get_middleware_stats() -> dict:
    """
    Get Chronicle middleware capture and sampling statistics.

    Shows capture counts, sampling strategy effectiveness, and patterns tracked.
    """
    if not CHRONICLE_MIDDLEWARE_AVAILABLE:
        return {"error": "Chronicle middleware not available"}

    return get_capture_stats()


@app.post("/middleware/clear", tags=["Middleware"])
def clear_middleware_requests() -> dict:
    """Clear all requests captured by Chronicle middleware."""
    if not CHRONICLE_MIDDLEWARE_AVAILABLE:
        return {"error": "Chronicle middleware not available", "deleted": 0}

    count = clear_captured_requests()
    return {"deleted": count}


@app.get("/middleware/request/{request_id}", tags=["Middleware"])
def get_middleware_request(request_id: str) -> dict:
    """Get a specific captured request by ID."""
    if not CHRONICLE_MIDDLEWARE_AVAILABLE:
        return {"error": "Chronicle middleware not available"}

    requests = get_captured_requests(limit=1000)
    for req in requests:
        if req.id == request_id:
            return req.to_dict()

    raise HTTPException(status_code=404, detail=f"Request not found: {request_id}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
