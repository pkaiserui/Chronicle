"""Pytest configuration and shared fixtures for demo app tests."""

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List

import pytest

from demo.capture import CaptureStorage, configure_storage
from demo.database import TaskDatabase


@pytest.fixture(scope="session")
def captured_data_path():
    """Path to the captured JSON data file."""
    # Try to find the captured data file
    possible_paths = [
        Path(__file__).parent.parent / "Downloads" / "chronicle_captures_20260120_210049.json",
        Path(__file__).parent / "fixtures" / "chronicle_captures_20260120_210049.json",
        Path.home() / "Downloads" / "chronicle_captures_20260120_210049.json",
    ]
    
    for path in possible_paths:
        if path.exists():
            return str(path)
    
    # If not found, return None and tests will skip
    return None


@pytest.fixture(scope="session")
def captured_calls(captured_data_path) -> Dict[str, List[Dict]]:
    """Load and organize captured function calls by function name."""
    if not captured_data_path or not os.path.exists(captured_data_path):
        return {}
    
    with open(captured_data_path, "r") as f:
        data = json.load(f)
    
    # Organize calls by function name
    organized = {}
    for call in data.get("calls", []):
        func_name = call.get("function_name")
        if func_name:
            if func_name not in organized:
                organized[func_name] = []
            organized[func_name].append(call)
    
    return organized


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    db = TaskDatabase(db_path)
    yield db
    
    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def temp_capture_db():
    """Create a temporary capture database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    storage = configure_storage(db_path)
    yield storage
    
    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def sample_task_data():
    """Sample task data for testing."""
    return {
        "title": "Test Task",
        "description": "Test Description",
        "priority": "medium",
        "payload": {"test": True},
        "max_retries": 3,
    }


@pytest.fixture
def sample_task_create(sample_task_data):
    """Sample TaskCreate object."""
    from demo.models import TaskCreate, TaskPriority
    
    return TaskCreate(
        title=sample_task_data["title"],
        description=sample_task_data["description"],
        priority=TaskPriority(sample_task_data["priority"]),
        payload=sample_task_data["payload"],
        max_retries=sample_task_data["max_retries"],
    )
