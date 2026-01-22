"""Integration tests for complete workflows using captured patterns."""

import pytest
from fastapi import HTTPException

from demo.api import (
    ProcessTaskRequest,
    TaskCreateRequest,
    claim_next_task,
    complete_task,
    create_task,
    get_task,
    list_tasks,
    process_task,
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


class TestCompleteWorkflow:
    """Test complete task lifecycle workflows from captured patterns."""
    
    def test_complete_task_lifecycle(self):
        """Test complete workflow: create -> claim -> process -> complete."""
        # Step 1: Create task (pattern from captured create_task)
        create_request = TaskCreateRequest(
            title="Generate invoice #3914",
            description="Auto-generated task",
            priority="critical",
            payload={"generated": True, "value": 13},
            max_retries=3,
        )
        created = create_task(create_request)
        
        assert created.status == "pending"
        assert created.priority == "critical"
        
        # Step 2: Claim task (pattern from captured claim_next_task)
        claimed = claim_next_task(worker_id="simulator-worker-1", priority=None)
        
        assert claimed.status == "claimed"
        assert claimed.claimed_by == "simulator-worker-1"
        assert claimed.id == created.id
        
        # Step 3: Process task (pattern from captured process_task)
        process_request = ProcessTaskRequest(
            worker_id="simulator-worker-1",
            simulate_duration_ms=25,
        )
        processing = process_task(claimed.id, process_request)
        
        assert processing.status == "processing"
        
        # Step 4: Complete task (pattern from captured complete_task)
        result_data = {"output": "Completed by simulator-worker-1", "value": 40}
        completed = complete_task(processing.id, result=result_data)
        
        assert completed.status == "completed"
        assert completed.result == result_data
        assert completed.completed_at is not None
    
    def test_multiple_tasks_priority_ordering(self):
        """Test that tasks are claimed in priority order (critical > high > medium > low)."""
        # Create tasks with different priorities
        priorities = ["low", "medium", "high", "critical"]
        created_tasks = []
        
        for priority in priorities:
            request = TaskCreateRequest(
                title=f"{priority.title()} Priority Task",
                priority=priority,
            )
            task = create_task(request)
            created_tasks.append(task)
        
        # Claim tasks and verify order
        claimed_order = []
        for _ in range(4):
            claimed = claim_next_task(worker_id="worker-1", priority=None)
            claimed_order.append(claimed.priority)
        
        # Should be in priority order
        assert claimed_order == ["critical", "high", "medium", "low"]
    
    def test_list_tasks_filtering_workflow(self):
        """Test listing tasks with various filters - pattern from captured list_tasks."""
        # Create tasks with different statuses
        # Create pending tasks
        for i in range(3):
            request = TaskCreateRequest(
                title=f"Pending Task {i}",
                priority="medium",
            )
            create_task(request)
        
        # Create, claim, and process some
        request = TaskCreateRequest(title="Claimed Task", priority="high")
        task1 = create_task(request)
        claim_next_task(worker_id="worker-1", priority=None)
        
        request = TaskCreateRequest(title="Processing Task", priority="critical")
        task2 = create_task(request)
        claim_next_task(worker_id="worker-1", priority=None)
        process_request = ProcessTaskRequest(worker_id="worker-1")
        process_task(task2.id, process_request)
        
        # List pending tasks
        pending = list_tasks(status="pending")
        assert len(pending) == 3
        
        # List claimed tasks
        claimed = list_tasks(status="claimed")
        assert len(claimed) == 1
        
        # List processing tasks
        processing = list_tasks(status="processing")
        assert len(processing) == 1
        
        # List by priority
        high_priority = list_tasks(priority="high")
        assert len(high_priority) >= 1
    
    def test_task_retry_workflow(self):
        """Test task failure and retry workflow."""
        # Create task
        request = TaskCreateRequest(
            title="Task with Retries",
            priority="medium",
            max_retries=2,
        )
        task = create_task(request)
        
        # Claim and process
        claim_next_task(worker_id="worker-1", priority=None)
        process_request = ProcessTaskRequest(worker_id="worker-1")
        process_task(task.id, process_request)
        
        # Fail it (first retry)
        from demo.api import fail_task
        failed = fail_task(task.id, error_message="First failure")
        
        assert failed.status == "pending"  # Retried
        assert failed.retry_count == 1
        
        # Claim and process again
        claim_next_task(worker_id="worker-1", priority=None)
        process_task(task.id, process_request)
        
        # Fail again (second retry)
        failed = fail_task(task.id, error_message="Second failure")
        assert failed.retry_count == 2
        
        # Fail one more time (should be final failure)
        claim_next_task(worker_id="worker-1", priority=None)
        process_task(task.id, process_request)
        final = fail_task(task.id, error_message="Final failure")
        
        assert final.status == "failed"
        assert final.retry_count == 2  # Max retries reached


class TestErrorScenarios:
    """Test error scenarios from captured patterns."""
    
    def test_claim_next_task_no_tasks_available(self):
        """Test claiming when no tasks available - pattern from captured errors."""
        # Don't create any tasks
        with pytest.raises(HTTPException) as exc_info:
            claim_next_task(worker_id="worker-1", priority=None)
        
        assert exc_info.value.status_code == 404
        assert "No tasks available" in str(exc_info.value.detail)
    
    def test_get_task_not_found(self):
        """Test getting a non-existent task."""
        with pytest.raises(HTTPException) as exc_info:
            get_task("non-existent-task-id")
        
        assert exc_info.value.status_code == 404
    
    def test_process_task_not_claimed(self):
        """Test processing a task that hasn't been claimed."""
        # Create task but don't claim
        request = TaskCreateRequest(title="Task", priority="medium")
        task = create_task(request)
        
        # Try to process
        process_request = ProcessTaskRequest(worker_id="worker-1")
        with pytest.raises(HTTPException) as exc_info:
            process_task(task.id, process_request)
        
        assert exc_info.value.status_code == 409
    
    def test_complete_task_not_processing(self):
        """Test completing a task that's not in processing state."""
        # Create and claim but don't process
        request = TaskCreateRequest(title="Task", priority="medium")
        task = create_task(request)
        claim_next_task(worker_id="worker-1", priority=None)
        
        # Try to complete
        with pytest.raises(HTTPException) as exc_info:
            complete_task(task.id)
        
        assert exc_info.value.status_code == 409
    
    def test_invalid_priority(self):
        """Test creating task with invalid priority."""
        request = TaskCreateRequest(
            title="Task",
            priority="invalid_priority",
        )
        
        with pytest.raises(HTTPException) as exc_info:
            create_task(request)
        
        assert exc_info.value.status_code == 400
        assert "Invalid priority" in str(exc_info.value.detail)
    
    def test_invalid_status_filter(self):
        """Test listing tasks with invalid status."""
        with pytest.raises(HTTPException) as exc_info:
            list_tasks(status="invalid_status")
        
        assert exc_info.value.status_code == 400
        assert "Invalid status" in str(exc_info.value.detail)


class TestConcurrentOperations:
    """Test concurrent-like operations."""
    
    def test_multiple_workers_claiming(self):
        """Test multiple workers trying to claim tasks."""
        # Create multiple tasks
        for i in range(5):
            request = TaskCreateRequest(
                title=f"Task {i}",
                priority="medium",
            )
            create_task(request)
        
        # Multiple workers claim tasks
        worker1_tasks = []
        worker2_tasks = []
        
        for _ in range(3):
            try:
                task = claim_next_task(worker_id="worker-1", priority=None)
                worker1_tasks.append(task.id)
            except HTTPException:
                pass
        
        for _ in range(3):
            try:
                task = claim_next_task(worker_id="worker-2", priority=None)
                worker2_tasks.append(task.id)
            except HTTPException:
                pass
        
        # Verify no overlap
        assert len(set(worker1_tasks) & set(worker2_tasks)) == 0
        assert len(worker1_tasks) + len(worker2_tasks) <= 5
