"""
FastAPI backend for the Chronicle Demo Task Queue System.

Provides REST API endpoints for task management with Chronicle capture
and OpenTelemetry instrumentation.
"""

from __future__ import annotations

import random
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .capture import capture, configure_storage, get_storage
from .database import TaskDatabase
from .models import TaskCreate, TaskPriority, TaskStatus, TaskUpdate

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
        db = TaskDatabase("demo_tasks.db")
    return db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    global db
    db = TaskDatabase("demo_tasks.db")
    configure_storage("chronicle_captures.db")
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

# Instrument with OpenTelemetry
if OTEL_ENABLED:
    FastAPIInstrumentor.instrument_app(app)


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

    # Extract actual values from Query objects if needed (for direct function calls in tests)
    if not isinstance(status, (str, type(None))):
        status = getattr(status, "default", None)
    if not isinstance(priority, (str, type(None))):
        priority = getattr(priority, "default", None)
    if not isinstance(limit, int):
        limit = getattr(limit, "default", 100)
    if not isinstance(offset, int):
        offset = getattr(offset, "default", 0)

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

    # Extract actual values from Query objects if needed (for direct function calls in tests)
    if not isinstance(worker_id, str):
        worker_id = getattr(worker_id, "default", worker_id)
    if not isinstance(priority, (str, type(None))):
        priority = getattr(priority, "default", None)

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
        "timestamp": datetime.now(timezone.utc).isoformat(),
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
    # Extract actual values from Query objects if needed (for direct function calls in tests)
    # Query objects passed as defaults need to be converted to their default values
    if not isinstance(function_name, (str, type(None))):
        function_name = getattr(function_name, "default", None)
    if not isinstance(has_error, (bool, type(None))):
        has_error = getattr(has_error, "default", None)
    if not isinstance(limit, int):
        limit = getattr(limit, "default", 50)
    if not isinstance(offset, int):
        offset = getattr(offset, "default", 0)
    
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
