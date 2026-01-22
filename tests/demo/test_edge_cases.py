"""Edge case and boundary condition tests."""

import pytest
from fastapi import HTTPException

from demo.api import (
    ProcessTaskRequest,
    TaskCreateRequest,
    TaskUpdateRequest,
    claim_next_task,
    complete_task,
    create_task,
    delete_task,
    get_task,
    list_tasks,
    process_task,
    update_task,
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


class TestBoundaryConditions:
    """Test boundary conditions and edge cases."""
    
    def test_create_task_minimal_fields(self):
        """Test creating task with minimal required fields."""
        request = TaskCreateRequest(title="Minimal Task")
        task = create_task(request)
        
        assert task.title == "Minimal Task"
        assert task.description == ""  # Default
        assert task.priority == "medium"  # Default
        assert task.payload == {}  # Default
        assert task.max_retries == 3  # Default
    
    def test_create_task_max_length_title(self):
        """Test creating task with maximum length title."""
        long_title = "A" * 200  # Max length from Field definition
        request = TaskCreateRequest(title=long_title)
        task = create_task(request)
        
        assert len(task.title) == 200
    
    def test_create_task_long_description(self):
        """Test creating task with long description."""
        long_desc = "A" * 2000  # Max length
        request = TaskCreateRequest(
            title="Task",
            description=long_desc,
        )
        task = create_task(request)
        
        assert len(task.description) == 2000
    
    def test_create_task_all_priorities(self):
        """Test creating tasks with all priority levels."""
        priorities = ["low", "medium", "high", "critical"]
        
        for priority in priorities:
            request = TaskCreateRequest(
                title=f"{priority.title()} Task",
                priority=priority,
            )
            task = create_task(request)
            assert task.priority == priority
    
    def test_create_task_max_retries(self):
        """Test creating task with maximum retries."""
        request = TaskCreateRequest(
            title="Task",
            max_retries=10,  # Max from Field definition
        )
        task = create_task(request)
        
        assert task.max_retries == 10
    
    def test_create_task_zero_retries(self):
        """Test creating task with zero retries."""
        request = TaskCreateRequest(
            title="Task",
            max_retries=0,
        )
        task = create_task(request)
        
        assert task.max_retries == 0
    
    def test_list_tasks_max_limit(self):
        """Test listing tasks with maximum limit."""
        # Create many tasks
        for i in range(150):
            request = TaskCreateRequest(title=f"Task {i}")
            create_task(request)
        
        # List with max limit
        tasks = list_tasks(status=None, priority=None, limit=1000, offset=0)  # Max limit
        
        assert len(tasks) <= 1000
    
    def test_list_tasks_large_offset(self):
        """Test listing tasks with large offset."""
        # Create some tasks
        for i in range(10):
            request = TaskCreateRequest(title=f"Task {i}")
            create_task(request)
        
        # List with offset beyond available
        tasks = list_tasks(status=None, priority=None, limit=10, offset=100)
        
        assert len(tasks) == 0
    
    def test_update_task_partial_fields(self):
        """Test updating task with only some fields."""
        # Create task
        request = TaskCreateRequest(
            title="Original Title",
            description="Original Description",
            priority="medium",
            payload={"key": "value"},
        )
        task = create_task(request)
        
        # Update only description
        update = TaskUpdateRequest(description="Updated Description")
        updated = update_task(task.id, update)
        
        assert updated.title == "Original Title"  # Unchanged
        assert updated.description == "Updated Description"
        assert updated.priority == "medium"  # Unchanged
        assert updated.payload == {"key": "value"}  # Unchanged
    
    def test_update_task_all_fields(self):
        """Test updating all fields of a task."""
        # Create task
        request = TaskCreateRequest(
            title="Original",
            description="Original",
            priority="low",
            payload={"old": True},
        )
        task = create_task(request)
        
        # Update all fields
        update = TaskUpdateRequest(
            title="Updated",
            description="Updated",
            priority="critical",
            payload={"new": True},
        )
        updated = update_task(task.id, update)
        
        assert updated.title == "Updated"
        assert updated.description == "Updated"
        assert updated.priority == "critical"
        assert updated.payload == {"new": True}
    
    def test_process_task_with_simulate_duration(self):
        """Test processing task with simulation duration."""
        # Create and claim task
        request = TaskCreateRequest(title="Task", priority="medium")
        task = create_task(request)
        claim_next_task(worker_id="worker-1", priority=None)
        
        # Process with duration
        process_request = ProcessTaskRequest(
            worker_id="worker-1",
            simulate_duration_ms=100,
        )
        import time
        start = time.time()
        result = process_task(task.id, process_request)
        elapsed = time.time() - start
        
        assert result.status == "processing"
        assert elapsed >= 0.1  # Should take at least 100ms


class TestPayloadVariations:
    """Test various payload structures."""
    
    def test_create_task_empty_payload(self):
        """Test creating task with empty payload."""
        request = TaskCreateRequest(
            title="Task",
            payload={},
        )
        task = create_task(request)
        
        assert task.payload == {}
    
    def test_create_task_nested_payload(self):
        """Test creating task with nested payload."""
        nested_payload = {
            "level1": {
                "level2": {
                    "level3": "value",
                    "array": [1, 2, 3],
                }
            },
            "list": ["a", "b", "c"],
        }
        request = TaskCreateRequest(
            title="Task",
            payload=nested_payload,
        )
        task = create_task(request)
        
        assert task.payload == nested_payload
    
    def test_create_task_payload_with_special_values(self):
        """Test creating task with payload containing special values."""
        special_payload = {
            "null_value": None,
            "boolean": True,
            "number": 42,
            "float": 3.14,
            "string": "test",
        }
        request = TaskCreateRequest(
            title="Task",
            payload=special_payload,
        )
        task = create_task(request)
        
        assert task.payload == special_payload


class TestErrorHandling:
    """Test error handling edge cases."""
    
    def test_create_task_invalid_priority(self):
        """Test creating task with invalid priority."""
        request = TaskCreateRequest(
            title="Task",
            priority="invalid",
        )
        
        with pytest.raises(HTTPException) as exc_info:
            create_task(request)
        
        assert exc_info.value.status_code == 400
    
    def test_create_task_empty_title(self):
        """Test creating task with empty title (should fail validation)."""
        # This should fail at Pydantic validation level
        with pytest.raises(Exception):  # Pydantic ValidationError
            TaskCreateRequest(title="")  # min_length=1
    
    def test_update_task_not_found(self):
        """Test updating non-existent task."""
        update = TaskUpdateRequest(title="New Title")
        
        with pytest.raises(HTTPException) as exc_info:
            update_task("non-existent", update)
        
        assert exc_info.value.status_code == 404
    
    def test_delete_task_not_found(self):
        """Test deleting non-existent task."""
        with pytest.raises(HTTPException) as exc_info:
            delete_task("non-existent")
        
        assert exc_info.value.status_code == 404


class TestStateTransitions:
    """Test state transition edge cases."""
    
    def test_claim_already_claimed_task(self):
        """Test claiming a task that's already claimed."""
        # Create and claim task
        request = TaskCreateRequest(title="Task", priority="medium")
        task = create_task(request)
        claim_next_task(worker_id="worker-1", priority=None)
        
        # Try to claim again
        from demo.api import claim_task
        process_request = ProcessTaskRequest(worker_id="worker-2")
        
        with pytest.raises(HTTPException) as exc_info:
            claim_task(task.id, process_request)
        
        assert exc_info.value.status_code == 409
    
    def test_complete_task_twice(self):
        """Test completing a task that's already completed."""
        # Create, claim, process, and complete
        request = TaskCreateRequest(title="Task", priority="medium")
        task = create_task(request)
        claim_next_task(worker_id="worker-1", priority=None)
        process_request = ProcessTaskRequest(worker_id="worker-1")
        process_task(task.id, process_request)
        complete_task(task.id, result={"done": True})
        
        # Try to complete again
        with pytest.raises(HTTPException) as exc_info:
            complete_task(task.id, result={"done": True})
        
        assert exc_info.value.status_code == 409
