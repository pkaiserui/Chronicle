"""Tests for API functions that were not captured in the data."""

import pytest
from fastapi import HTTPException

from demo.api import (
    ProcessTaskRequest,
    TaskCreateRequest,
    claim_task,
    clear_captures,
    clear_tasks,
    fail_task,
    get_error_injection,
    get_stats,
    health_check,
    list_captured_functions,
    list_captures,
    set_error_injection,
)
from demo.capture import configure_storage
from demo.database import TaskDatabase


@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch, temp_db, temp_capture_db):
    """Set up test database before each test."""
    import demo.api
    monkeypatch.setattr(demo.api, "db", temp_db)
    monkeypatch.setattr(demo.api, "get_db", lambda: temp_db)
    configure_storage(temp_capture_db.db_path)
    monkeypatch.setattr(demo.api, "error_injection_rate", 0.0)


class TestClaimTask:
    """Test claim_task endpoint (not captured but similar to claim_next_task)."""
    
    def test_claim_task_success(self, temp_db):
        """Test claiming a specific task."""
        # Create a task
        from demo.models import TaskCreate, TaskPriority
        
        task_create = TaskCreate(
            title="Task to Claim",
            description="Test",
            priority=TaskPriority.MEDIUM,
        )
        task = temp_db.create_task(task_create)
        
        # Claim it
        request = ProcessTaskRequest(worker_id="worker-1")
        result = claim_task(task.id, request)
        
        assert result.status == "claimed"
        assert result.claimed_by == "worker-1"
        assert result.claimed_at is not None
    
    def test_claim_task_not_available(self, temp_db):
        """Test claiming a task that's not available."""
        # Create and claim a task
        from demo.models import TaskCreate, TaskPriority
        
        task_create = TaskCreate(title="Task", priority=TaskPriority.MEDIUM)
        task = temp_db.create_task(task_create)
        temp_db.claim_task(task.id, "worker-1")
        
        # Try to claim again
        request = ProcessTaskRequest(worker_id="worker-2")
        with pytest.raises(HTTPException) as exc_info:
            claim_task(task.id, request)
        
        assert exc_info.value.status_code == 409


class TestFailTask:
    """Test fail_task endpoint."""
    
    def test_fail_task_success(self, temp_db):
        """Test failing a task."""
        # Create, claim, and process a task
        from demo.models import TaskCreate, TaskPriority
        
        task_create = TaskCreate(title="Task", priority=TaskPriority.MEDIUM, max_retries=3)
        task = temp_db.create_task(task_create)
        temp_db.claim_task(task.id, "worker-1")
        temp_db.start_processing(task.id)
        
        # Fail it
        result = fail_task(task.id, error_message="Test error")
        
        assert result.status == "pending"  # Retried
        assert result.error_message == "Test error"
        assert result.retry_count == 1
    
    def test_fail_task_not_found(self, temp_db):
        """Test failing a non-existent task."""
        with pytest.raises(HTTPException) as exc_info:
            fail_task("non-existent", error_message="Error")
        
        assert exc_info.value.status_code == 404


class TestHealthCheck:
    """Test health_check endpoint."""
    
    def test_health_check(self):
        """Test health check returns healthy status."""
        result = health_check()
        
        assert result["status"] == "healthy"
        assert "timestamp" in result
        assert "otel_enabled" in result


class TestGetStats:
    """Test get_stats endpoint."""
    
    def test_get_stats(self, temp_db):
        """Test getting statistics."""
        # Create some tasks
        from demo.models import TaskCreate, TaskPriority
        
        for i in range(3):
            task_create = TaskCreate(
                title=f"Task {i}",
                priority=TaskPriority.MEDIUM,
            )
            temp_db.create_task(task_create)
        
        result = get_stats()
        
        # StatsResponse is a Pydantic model, access attributes directly
        assert result.tasks["total"] == 3
        assert result.captures is not None


class TestClearTasks:
    """Test clear_tasks endpoint."""
    
    def test_clear_tasks(self, temp_db):
        """Test clearing all tasks."""
        # Create some tasks
        from demo.models import TaskCreate, TaskPriority
        
        for i in range(5):
            task_create = TaskCreate(title=f"Task {i}", priority=TaskPriority.MEDIUM)
            temp_db.create_task(task_create)
        
        result = clear_tasks()
        
        assert result["deleted"] == 5
        assert len(temp_db.list_tasks()) == 0


class TestClearCaptures:
    """Test clear_captures endpoint."""
    
    def test_clear_captures(self, temp_capture_db):
        """Test clearing all captures."""
        from demo.capture import CapturedCall, get_storage
        
        # Add some captures
        storage = get_storage()
        call = CapturedCall(
            function_name="test_function",
            module="test_module",
        )
        storage.store(call)
        
        result = clear_captures()
        
        assert result["deleted"] == 1
        assert len(storage.get_calls()) == 0


class TestErrorInjection:
    """Test error injection endpoints."""
    
    def test_get_error_injection(self):
        """Test getting error injection rate."""
        result = get_error_injection()
        
        assert "rate" in result
        assert isinstance(result["rate"], float)
        assert 0.0 <= result["rate"] <= 1.0
    
    def test_set_error_injection(self, monkeypatch):
        """Test setting error injection rate."""
        import demo.api
        
        from demo.api import ErrorInjectionConfig
        
        config = ErrorInjectionConfig(rate=0.5)
        result = set_error_injection(config)
        
        assert result["rate"] == 0.5
        assert demo.api.error_injection_rate == 0.5


class TestListCaptures:
    """Test list_captures endpoint."""
    
    def test_list_captures(self, temp_capture_db):
        """Test listing captures."""
        from demo.capture import CapturedCall, get_storage
        
        # Add some captures
        storage = get_storage()
        for i in range(3):
            call = CapturedCall(
                function_name="test_function",
                module="test_module",
            )
            storage.store(call)
        
        # list_captures returns a dict, but we need to handle Query objects properly
        # Call it without parameters to avoid Query object issues
        result = list_captures()
        
        assert "calls" in result
        assert "count" in result
        assert result["count"] == 3
    
    def test_list_captures_filter_by_function(self, temp_capture_db):
        """Test filtering captures by function name."""
        from demo.capture import CapturedCall, get_storage
        
        storage = get_storage()
        call1 = CapturedCall(function_name="func1", module="test")
        call2 = CapturedCall(function_name="func2", module="test")
        storage.store(call1)
        storage.store(call2)
        
        result = list_captures(function_name="func1")
        
        assert result["count"] == 1
        assert result["calls"][0]["function_name"] == "func1"
    
    def test_list_captures_filter_by_error(self, temp_capture_db):
        """Test filtering captures by error status."""
        from demo.capture import CapturedCall, get_storage
        
        storage = get_storage()
        call1 = CapturedCall(function_name="func1", module="test")
        call2 = CapturedCall(
            function_name="func2",
            module="test",
            exception="Test error",
            exception_type="ValueError",
        )
        storage.store(call1)
        storage.store(call2)
        
        result = list_captures(has_error=True)
        
        assert result["count"] == 1
        assert result["calls"][0]["exception"] == "Test error"


class TestListCapturedFunctions:
    """Test list_captured_functions endpoint."""
    
    def test_list_captured_functions(self, temp_capture_db):
        """Test listing captured function names."""
        from demo.capture import CapturedCall, get_storage
        
        storage = get_storage()
        for func_name in ["func1", "func2", "func1"]:  # func1 appears twice
            call = CapturedCall(function_name=func_name, module="test")
            storage.store(call)
        
        result = list_captured_functions()
        
        assert "functions" in result
        assert result["functions"]["func1"] == 2
        assert result["functions"]["func2"] == 1
