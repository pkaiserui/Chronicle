"""
Traffic simulator for the Chronicle Demo.

Generates realistic traffic patterns against the Task Queue API
with configurable operation weights and frequencies.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import requests


class OperationType(str, Enum):
    """Types of operations the simulator can perform."""

    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    CLAIM = "claim"
    PROCESS = "process"
    COMPLETE = "complete"


@dataclass
class SimulatorConfig:
    """Configuration for the traffic simulator."""

    # API endpoint
    api_base_url: str = "http://localhost:8000"

    # Request frequency (requests per second)
    requests_per_second: float = 1.0

    # Operation weights (should sum to 1.0)
    weights: Dict[OperationType, float] = field(
        default_factory=lambda: {
            OperationType.READ: 0.50,  # 50% reads
            OperationType.CREATE: 0.20,  # 20% creates
            OperationType.CLAIM: 0.10,  # 10% claims
            OperationType.PROCESS: 0.08,  # 8% process
            OperationType.COMPLETE: 0.07,  # 7% complete
            OperationType.UPDATE: 0.03,  # 3% updates
            OperationType.DELETE: 0.02,  # 2% deletes
        }
    )

    # Worker configuration
    worker_id: str = "simulator-worker-1"

    # Task generation settings
    task_titles: List[str] = field(
        default_factory=lambda: [
            "Process customer order",
            "Generate monthly report",
            "Send notification batch",
            "Sync inventory data",
            "Calculate user metrics",
            "Archive old records",
            "Validate data integrity",
            "Process refund request",
            "Update search index",
            "Generate invoice",
        ]
    )

    priorities: List[str] = field(
        default_factory=lambda: ["low", "medium", "medium", "medium", "high", "critical"]
    )


@dataclass
class SimulatorStats:
    """Statistics from the simulator."""

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    requests_by_type: Dict[str, int] = field(default_factory=dict)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    start_time: Optional[datetime] = None
    last_request_time: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": (
                round(self.successful_requests / self.total_requests * 100, 2)
                if self.total_requests > 0
                else 0
            ),
            "requests_by_type": self.requests_by_type,
            "recent_errors": self.errors[-10:],  # Last 10 errors
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "last_request_time": (
                self.last_request_time.isoformat() if self.last_request_time else None
            ),
            "duration_seconds": (
                (self.last_request_time - self.start_time).total_seconds()
                if self.start_time and self.last_request_time
                else 0
            ),
        }


class TrafficSimulator:
    """
    Generates traffic against the Chronicle Demo API.

    Supports configurable operation weights and request frequencies.
    """

    def __init__(self, config: Optional[SimulatorConfig] = None):
        self.config = config or SimulatorConfig()
        self.stats = SimulatorStats()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._known_task_ids: List[str] = []
        self._callbacks: List[Callable[[str, bool, Optional[str]], None]] = []

    def add_callback(
        self, callback: Callable[[str, bool, Optional[str]], None]
    ) -> None:
        """Add a callback to be notified of each request."""
        self._callbacks.append(callback)

    def _notify_callbacks(
        self, operation: str, success: bool, error: Optional[str] = None
    ) -> None:
        """Notify all callbacks of a request."""
        for callback in self._callbacks:
            try:
                callback(operation, success, error)
            except Exception:
                pass

    def _choose_operation(self) -> OperationType:
        """Choose an operation based on configured weights."""
        operations = list(self.config.weights.keys())
        weights = list(self.config.weights.values())
        return random.choices(operations, weights=weights, k=1)[0]

    def _make_request(
        self, method: str, endpoint: str, **kwargs
    ) -> Optional[requests.Response]:
        """Make an HTTP request to the API."""
        url = f"{self.config.api_base_url}{endpoint}"
        try:
            response = requests.request(method, url, timeout=10, **kwargs)
            return response
        except requests.RequestException as e:
            self.stats.failed_requests += 1
            self.stats.errors.append(
                {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "endpoint": endpoint,
                    "error": str(e),
                }
            )
            return None

    def _do_create(self) -> bool:
        """Create a new task."""
        title = random.choice(self.config.task_titles)
        priority = random.choice(self.config.priorities)

        response = self._make_request(
            "POST",
            "/tasks",
            json={
                "title": f"{title} #{random.randint(1000, 9999)}",
                "description": f"Auto-generated task at {datetime.now(timezone.utc).isoformat()}",
                "priority": priority,
                "payload": {"generated": True, "value": random.randint(1, 100)},
            },
        )

        if response and response.status_code == 200:
            task_id = response.json().get("id")
            if task_id:
                self._known_task_ids.append(task_id)
                # Keep list manageable
                if len(self._known_task_ids) > 100:
                    self._known_task_ids = self._known_task_ids[-100:]
            return True
        return False

    def _do_read(self) -> bool:
        """Read tasks (list or get by ID)."""
        if random.random() < 0.7:
            # List tasks
            status = random.choice([None, "pending", "claimed", "processing", "completed"])
            params = {"limit": random.randint(10, 50)}
            if status:
                params["status"] = status
            response = self._make_request("GET", "/tasks", params=params)
        else:
            # Get specific task
            if not self._known_task_ids:
                return self._do_create()  # Create if no tasks exist
            task_id = random.choice(self._known_task_ids)
            response = self._make_request("GET", f"/tasks/{task_id}")

        return response is not None and response.status_code == 200

    def _do_update(self) -> bool:
        """Update a task."""
        if not self._known_task_ids:
            return self._do_create()

        task_id = random.choice(self._known_task_ids)
        response = self._make_request(
            "PATCH",
            f"/tasks/{task_id}",
            json={
                "description": f"Updated at {datetime.now(timezone.utc).isoformat()}",
                "priority": random.choice(self.config.priorities),
            },
        )
        return response is not None and response.status_code == 200

    def _do_delete(self) -> bool:
        """Delete a task."""
        if not self._known_task_ids:
            return True  # Nothing to delete

        task_id = random.choice(self._known_task_ids)
        response = self._make_request("DELETE", f"/tasks/{task_id}")

        if response and response.status_code == 200:
            if task_id in self._known_task_ids:
                self._known_task_ids.remove(task_id)
            return True
        return False

    def _do_claim(self) -> bool:
        """Claim a pending task."""
        response = self._make_request(
            "POST",
            "/tasks/claim-next",
            params={"worker_id": self.config.worker_id},
        )
        return response is not None and response.status_code == 200

    def _do_process(self) -> bool:
        """Start processing a claimed task."""
        # First, try to claim a task
        response = self._make_request(
            "POST",
            "/tasks/claim-next",
            params={"worker_id": self.config.worker_id},
        )

        if response and response.status_code == 200:
            task_id = response.json().get("id")
            if task_id:
                # Start processing
                response = self._make_request(
                    "POST",
                    f"/tasks/{task_id}/process",
                    json={
                        "worker_id": self.config.worker_id,
                        "simulate_duration_ms": random.randint(10, 100),
                    },
                )
                return response is not None and response.status_code == 200

        return False

    def _do_complete(self) -> bool:
        """Complete a task (claim, process, complete flow)."""
        # Claim
        response = self._make_request(
            "POST",
            "/tasks/claim-next",
            params={"worker_id": self.config.worker_id},
        )

        if not response or response.status_code != 200:
            return False

        task_id = response.json().get("id")
        if not task_id:
            return False

        # Process
        response = self._make_request(
            "POST",
            f"/tasks/{task_id}/process",
            json={"worker_id": self.config.worker_id},
        )

        if not response or response.status_code != 200:
            return False

        # Complete
        response = self._make_request(
            "POST",
            f"/tasks/{task_id}/complete",
            json={"output": f"Completed by {self.config.worker_id}", "value": random.randint(1, 100)},
        )

        return response is not None and response.status_code == 200

    def _execute_operation(self, operation: OperationType) -> bool:
        """Execute a single operation."""
        handlers = {
            OperationType.CREATE: self._do_create,
            OperationType.READ: self._do_read,
            OperationType.UPDATE: self._do_update,
            OperationType.DELETE: self._do_delete,
            OperationType.CLAIM: self._do_claim,
            OperationType.PROCESS: self._do_process,
            OperationType.COMPLETE: self._do_complete,
        }

        handler = handlers.get(operation)
        if handler:
            return handler()
        return False

    def run_single(self) -> bool:
        """Run a single operation."""
        operation = self._choose_operation()

        self.stats.total_requests += 1
        self.stats.last_request_time = datetime.now(timezone.utc)
        self.stats.requests_by_type[operation.value] = (
            self.stats.requests_by_type.get(operation.value, 0) + 1
        )

        success = self._execute_operation(operation)

        if success:
            self.stats.successful_requests += 1
        else:
            self.stats.failed_requests += 1

        self._notify_callbacks(operation.value, success, None)

        return success

    def _run_loop(self) -> None:
        """Main simulation loop."""
        self.stats.start_time = datetime.now(timezone.utc)

        while self._running:
            self.run_single()

            # Sleep based on configured rate
            if self.config.requests_per_second > 0:
                sleep_time = 1.0 / self.config.requests_per_second
                # Add some jitter
                sleep_time *= random.uniform(0.8, 1.2)
                time.sleep(sleep_time)

    def start(self) -> None:
        """Start the simulator in a background thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the simulator."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def is_running(self) -> bool:
        """Check if the simulator is running."""
        return self._running

    def get_stats(self) -> dict:
        """Get current statistics."""
        return self.stats.to_dict()

    def reset_stats(self) -> None:
        """Reset statistics."""
        self.stats = SimulatorStats()

    def update_config(
        self,
        requests_per_second: Optional[float] = None,
        weights: Optional[Dict[str, float]] = None,
    ) -> None:
        """Update simulator configuration."""
        if requests_per_second is not None:
            self.config.requests_per_second = requests_per_second

        if weights:
            for op_name, weight in weights.items():
                try:
                    op = OperationType(op_name)
                    self.config.weights[op] = weight
                except ValueError:
                    pass


# Global simulator instance
_simulator: Optional[TrafficSimulator] = None


def get_simulator() -> TrafficSimulator:
    """Get the global simulator instance."""
    global _simulator
    if _simulator is None:
        _simulator = TrafficSimulator()
    return _simulator


def reset_simulator(config: Optional[SimulatorConfig] = None) -> TrafficSimulator:
    """Reset the global simulator with optional new config."""
    global _simulator
    if _simulator and _simulator.is_running():
        _simulator.stop()
    _simulator = TrafficSimulator(config)
    return _simulator
