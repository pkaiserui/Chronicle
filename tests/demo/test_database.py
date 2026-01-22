"""Tests for database layer using patterns from captured data."""

import pytest
from datetime import datetime

from demo.database import TaskDatabase
from demo.models import TaskCreate, TaskPriority, TaskStatus, TaskUpdate


class TestTaskDatabase:
    """Test TaskDatabase using patterns from captured API calls."""
    
    def test_create_task(self, temp_db):
        """Test creating a task - pattern from captured create_task calls."""
        # Example from captured data
        task_create = TaskCreate(
            title="Send notification batch #7834",
            description="Auto-generated task at 2026-01-21T05:00:14.455222",
            priority=TaskPriority.MEDIUM,
            payload={"generated": True, "value": 26},
            max_retries=3,
        )
        
        task = temp_db.create_task(task_create)
        
        assert task.id is not None
        assert task.title == task_create.title
        assert task.description == task_create.description
        assert task.priority == task_create.priority
        assert task.status == TaskStatus.PENDING
        assert task.payload == task_create.payload
        assert task.max_retries == task_create.max_retries
        assert task.retry_count == 0
        assert task.created_at is not None
        assert task.updated_at is not None
    
    def test_get_task(self, temp_db):
        """Test getting a task by ID."""
        # Create a task first
        task_create = TaskCreate(
            title="Test Task",
            description="Test Description",
            priority=TaskPriority.HIGH,
            payload={"test": True},
        )
        created_task = temp_db.create_task(task_create)
        
        # Get it back
        retrieved = temp_db.get_task(created_task.id)
        
        assert retrieved is not None
        assert retrieved.id == created_task.id
        assert retrieved.title == created_task.title
    
    def test_get_task_not_found(self, temp_db):
        """Test getting a non-existent task."""
        result = temp_db.get_task("non-existent-id")
        assert result is None
    
    def test_list_tasks_all(self, temp_db):
        """Test listing all tasks - pattern from captured list_tasks calls."""
        # Create multiple tasks with different priorities
        priorities = [TaskPriority.LOW, TaskPriority.MEDIUM, TaskPriority.HIGH, TaskPriority.CRITICAL]
        
        for i, priority in enumerate(priorities):
            task_create = TaskCreate(
                title=f"Task {i}",
                description=f"Description {i}",
                priority=priority,
                payload={"index": i},
            )
            temp_db.create_task(task_create)
        
        # List all
        tasks = temp_db.list_tasks()
        
        assert len(tasks) == 4
        # Should be ordered by created_at DESC
        assert tasks[0].created_at >= tasks[1].created_at
    
    def test_list_tasks_filter_by_status(self, temp_db):
        """Test filtering tasks by status - pattern from captured calls."""
        # Create tasks
        task_create = TaskCreate(title="Pending Task", priority=TaskPriority.MEDIUM)
        task1 = temp_db.create_task(task_create)
        
        # Claim one
        task2 = temp_db.claim_task(task1.id, "worker-1")
        
        # List pending
        pending = temp_db.list_tasks(status=TaskStatus.PENDING)
        assert len(pending) == 0  # task1 was claimed
        
        # List claimed
        claimed = temp_db.list_tasks(status=TaskStatus.CLAIMED)
        assert len(claimed) == 1
        assert claimed[0].id == task1.id
    
    def test_list_tasks_filter_by_priority(self, temp_db):
        """Test filtering tasks by priority."""
        # Create tasks with different priorities
        for priority in [TaskPriority.LOW, TaskPriority.MEDIUM, TaskPriority.HIGH, TaskPriority.CRITICAL]:
            task_create = TaskCreate(
                title=f"{priority.value} task",
                priority=priority,
            )
            temp_db.create_task(task_create)
        
        # Filter by high priority
        high_tasks = temp_db.list_tasks(priority=TaskPriority.HIGH)
        assert len(high_tasks) == 1
        assert high_tasks[0].priority == TaskPriority.HIGH
    
    def test_list_tasks_with_limit_and_offset(self, temp_db):
        """Test pagination - pattern from captured list_tasks calls."""
        # Create multiple tasks
        for i in range(10):
            task_create = TaskCreate(
                title=f"Task {i}",
                priority=TaskPriority.MEDIUM,
            )
            temp_db.create_task(task_create)
        
        # Get first 5
        first_page = temp_db.list_tasks(limit=5, offset=0)
        assert len(first_page) == 5
        
        # Get next 5
        second_page = temp_db.list_tasks(limit=5, offset=5)
        assert len(second_page) == 5
        
        # Verify different tasks
        assert first_page[0].id != second_page[0].id
    
    def test_update_task(self, temp_db):
        """Test updating a task - pattern from captured update_task calls."""
        # Create task
        task_create = TaskCreate(
            title="Original Title",
            description="Original Description",
            priority=TaskPriority.MEDIUM,
            payload={"original": True},
        )
        task = temp_db.create_task(task_create)
        
        # Update it
        update = TaskUpdate(
            description="Updated description",
            priority=TaskPriority.HIGH,
        )
        updated = temp_db.update_task(task.id, update)
        
        assert updated is not None
        assert updated.title == "Original Title"  # Not updated
        assert updated.description == "Updated description"
        assert updated.priority == TaskPriority.HIGH
        assert updated.payload == {"original": True}  # Not updated
    
    def test_update_task_not_found(self, temp_db):
        """Test updating a non-existent task."""
        update = TaskUpdate(title="New Title")
        result = temp_db.update_task("non-existent", update)
        assert result is None
    
    def test_delete_task(self, temp_db):
        """Test deleting a task - pattern from captured delete_task calls."""
        # Create task
        task_create = TaskCreate(title="Task to Delete", priority=TaskPriority.MEDIUM)
        task = temp_db.create_task(task_create)
        
        # Delete it
        deleted = temp_db.delete_task(task.id)
        assert deleted is True
        
        # Verify gone
        assert temp_db.get_task(task.id) is None
    
    def test_delete_task_not_found(self, temp_db):
        """Test deleting a non-existent task."""
        result = temp_db.delete_task("non-existent")
        assert result is False
    
    def test_claim_task(self, temp_db):
        """Test claiming a task - pattern from workflow."""
        # Create pending task
        task_create = TaskCreate(title="Task to Claim", priority=TaskPriority.MEDIUM)
        task = temp_db.create_task(task_create)
        
        # Claim it
        claimed = temp_db.claim_task(task.id, "worker-1")
        
        assert claimed is not None
        assert claimed.status == TaskStatus.CLAIMED
        assert claimed.claimed_by == "worker-1"
        assert claimed.claimed_at is not None
    
    def test_claim_task_already_claimed(self, temp_db):
        """Test claiming an already claimed task."""
        # Create and claim task
        task_create = TaskCreate(title="Task", priority=TaskPriority.MEDIUM)
        task = temp_db.create_task(task_create)
        temp_db.claim_task(task.id, "worker-1")
        
        # Try to claim again
        result = temp_db.claim_task(task.id, "worker-2")
        assert result is None  # Should fail
    
    def test_claim_next_task(self, temp_db):
        """Test claiming next available task - pattern from captured claim_next_task calls."""
        # Create tasks with different priorities
        priorities = [
            (TaskPriority.LOW, "Low Priority"),
            (TaskPriority.MEDIUM, "Medium Priority"),
            (TaskPriority.HIGH, "High Priority"),
            (TaskPriority.CRITICAL, "Critical Priority"),
        ]
        
        for priority, title in priorities:
            task_create = TaskCreate(title=title, priority=priority)
            temp_db.create_task(task_create)
        
        # Claim next (should get critical first)
        claimed = temp_db.claim_next_task("worker-1")
        
        assert claimed is not None
        assert claimed.priority == TaskPriority.CRITICAL
        assert claimed.status == TaskStatus.CLAIMED
        assert claimed.claimed_by == "worker-1"
    
    def test_claim_next_task_with_priority_filter(self, temp_db):
        """Test claiming next task with priority filter."""
        # Create tasks with different priorities
        for priority in [TaskPriority.LOW, TaskPriority.MEDIUM, TaskPriority.HIGH]:
            task_create = TaskCreate(title=f"{priority.value} task", priority=priority)
            temp_db.create_task(task_create)
        
        # Claim next with medium priority filter
        claimed = temp_db.claim_next_task("worker-1", priority=TaskPriority.MEDIUM)
        
        assert claimed is not None
        assert claimed.priority == TaskPriority.MEDIUM
    
    def test_claim_next_task_no_available(self, temp_db):
        """Test claiming when no tasks available - pattern from captured errors."""
        # Don't create any tasks
        result = temp_db.claim_next_task("worker-1")
        assert result is None
    
    def test_start_processing(self, temp_db):
        """Test starting to process a task - pattern from process_task calls."""
        # Create and claim task
        task_create = TaskCreate(title="Task", priority=TaskPriority.MEDIUM)
        task = temp_db.create_task(task_create)
        temp_db.claim_task(task.id, "worker-1")
        
        # Start processing
        processing = temp_db.start_processing(task.id)
        
        assert processing is not None
        assert processing.status == TaskStatus.PROCESSING
    
    def test_start_processing_not_claimed(self, temp_db):
        """Test starting processing on unclaimed task."""
        # Create but don't claim
        task_create = TaskCreate(title="Task", priority=TaskPriority.MEDIUM)
        task = temp_db.create_task(task_create)
        
        # Try to start processing
        result = temp_db.start_processing(task.id)
        assert result is None
    
    def test_complete_task(self, temp_db):
        """Test completing a task - pattern from captured complete_task calls."""
        # Create, claim, and process task
        task_create = TaskCreate(title="Task", priority=TaskPriority.MEDIUM)
        task = temp_db.create_task(task_create)
        temp_db.claim_task(task.id, "worker-1")
        temp_db.start_processing(task.id)
        
        # Complete it
        result_data = {"output": "Completed by worker-1", "value": 40}
        completed = temp_db.complete_task(task.id, result=result_data)
        
        assert completed is not None
        assert completed.status == TaskStatus.COMPLETED
        assert completed.result == result_data
        assert completed.completed_at is not None
    
    def test_complete_task_not_processing(self, temp_db):
        """Test completing a task that's not in processing state."""
        # Create and claim but don't process
        task_create = TaskCreate(title="Task", priority=TaskPriority.MEDIUM)
        task = temp_db.create_task(task_create)
        temp_db.claim_task(task.id, "worker-1")
        
        # Try to complete
        result = temp_db.complete_task(task.id)
        assert result is None
    
    def test_fail_task_with_retry(self, temp_db):
        """Test failing a task that can be retried."""
        # Create task
        task_create = TaskCreate(title="Task", priority=TaskPriority.MEDIUM, max_retries=3)
        task = temp_db.create_task(task_create)
        temp_db.claim_task(task.id, "worker-1")
        temp_db.start_processing(task.id)
        
        # Fail it (should retry)
        failed = temp_db.fail_task(task.id, "Test error")
        
        assert failed is not None
        assert failed.status == TaskStatus.PENDING  # Retried
        assert failed.retry_count == 1
        assert failed.error_message == "Test error"
        assert failed.claimed_at is None  # Reset
    
    def test_fail_task_max_retries(self, temp_db):
        """Test failing a task that has exceeded max retries."""
        # Create task
        task_create = TaskCreate(title="Task", priority=TaskPriority.MEDIUM, max_retries=2)
        task = temp_db.create_task(task_create)
        
        # Fail it multiple times (max_retries=2 means we can retry twice, then fail)
        temp_db.claim_task(task.id, "worker-1")
        temp_db.start_processing(task.id)
        temp_db.fail_task(task.id, "Error 1")  # retry_count = 1
        
        temp_db.claim_task(task.id, "worker-1")
        temp_db.start_processing(task.id)
        temp_db.fail_task(task.id, "Error 2")  # retry_count = 2
        
        temp_db.claim_task(task.id, "worker-1")
        temp_db.start_processing(task.id)
        final = temp_db.fail_task(task.id, "Error 3")  # retry_count = 2, but status = FAILED
        
        assert final is not None
        assert final.status == TaskStatus.FAILED  # No more retries
        assert final.retry_count == 2  # Max retries reached, so retry_count stays at 2
    
    def test_get_stats(self, temp_db):
        """Test getting task statistics."""
        # Create tasks with different statuses and priorities
        task_create = TaskCreate(title="Task 1", priority=TaskPriority.CRITICAL)
        task1 = temp_db.create_task(task_create)
        
        task_create = TaskCreate(title="Task 2", priority=TaskPriority.HIGH)
        task2 = temp_db.create_task(task_create)
        temp_db.claim_task(task2.id, "worker-1")
        
        task_create = TaskCreate(title="Task 3", priority=TaskPriority.MEDIUM)
        task3 = temp_db.create_task(task_create)
        temp_db.claim_task(task3.id, "worker-1")
        temp_db.start_processing(task3.id)
        temp_db.complete_task(task3.id)
        
        # Get stats
        stats = temp_db.get_stats()
        
        assert stats["total"] == 3
        assert stats["by_status"]["pending"] == 1
        assert stats["by_status"]["claimed"] == 1
        assert stats["by_status"]["completed"] == 1
        assert stats["by_priority"]["critical"] == 1
        assert stats["by_priority"]["high"] == 1
        assert stats["by_priority"]["medium"] == 1
    
    def test_clear_all(self, temp_db):
        """Test clearing all tasks."""
        # Create some tasks
        for i in range(5):
            task_create = TaskCreate(title=f"Task {i}", priority=TaskPriority.MEDIUM)
            temp_db.create_task(task_create)
        
        # Clear all
        count = temp_db.clear_all()
        
        assert count == 5
        assert len(temp_db.list_tasks()) == 0
