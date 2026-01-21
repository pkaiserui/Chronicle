"""
Streamlit UI for the Chronicle Demo.

Provides a dashboard for:
- Configuring traffic simulation parameters
- Viewing task queue status
- Exploring Chronicle captures
- Running analysis and tests
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st

# Add parent directory to path for Chronicle imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from demo.simulator import SimulatorConfig, TrafficSimulator, get_simulator

# Page configuration
st.set_page_config(
    page_title="Chronicle Demo",
    page_icon="📜",
    layout="wide",
    initial_sidebar_state="expanded",
)

# API configuration
API_BASE_URL = "http://localhost:8000"


def api_request(method: str, endpoint: str, **kwargs) -> dict | None:
    """Make an API request and handle errors."""
    try:
        response = requests.request(
            method, f"{API_BASE_URL}{endpoint}", timeout=10, **kwargs
        )
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"API Error: {response.status_code} - {response.text}")
            return None
    except requests.RequestException as e:
        st.error(f"Connection Error: {e}")
        return None


def render_sidebar():
    """Render the sidebar with navigation and controls."""
    st.sidebar.title("📜 Chronicle Demo")
    st.sidebar.markdown("---")

    # Navigation
    page = st.sidebar.radio(
        "Navigation",
        ["Dashboard", "Task Queue", "Chronicle Captures", "Traffic Simulator", "Analysis"],
        index=0,
    )

    st.sidebar.markdown("---")

    # Quick stats
    st.sidebar.subheader("Quick Stats")
    stats = api_request("GET", "/stats")
    if stats:
        task_stats = stats.get("tasks", {})
        capture_stats = stats.get("captures", {})

        col1, col2 = st.sidebar.columns(2)
        col1.metric("Tasks", task_stats.get("total", 0))
        col2.metric("Captures", capture_stats.get("total_calls", 0))

        if task_stats.get("failed_rate"):
            st.sidebar.metric("Task Fail Rate", f"{task_stats['failed_rate']}%")

    st.sidebar.markdown("---")

    # API health
    health = api_request("GET", "/health")
    if health:
        status = "🟢 Online" if health.get("status") == "healthy" else "🔴 Offline"
        st.sidebar.text(f"API Status: {status}")
        if health.get("otel_enabled"):
            st.sidebar.text("OpenTelemetry: Enabled")
    else:
        st.sidebar.text("API Status: 🔴 Offline")

    return page


def render_dashboard():
    """Render the main dashboard page."""
    st.title("Chronicle Demo Dashboard")
    st.markdown("Real-time overview of the Task Queue System and Chronicle captures.")

    # Fetch data
    stats = api_request("GET", "/stats")
    if not stats:
        st.warning("Unable to fetch statistics. Is the API running?")
        st.code("uvicorn demo.api:app --reload --port 8000", language="bash")
        return

    task_stats = stats.get("tasks", {})
    capture_stats = stats.get("captures", {})

    # Task overview
    st.header("Task Queue Overview")
    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Total Tasks", task_stats.get("total", 0))
    col2.metric("Pending", task_stats.get("by_status", {}).get("pending", 0))
    col3.metric("Processing", task_stats.get("by_status", {}).get("processing", 0))
    col4.metric("Completed", task_stats.get("by_status", {}).get("completed", 0))

    # Status breakdown
    if task_stats.get("by_status"):
        st.subheader("Tasks by Status")
        status_data = task_stats["by_status"]
        st.bar_chart(status_data)

    # Priority breakdown
    if task_stats.get("by_priority"):
        st.subheader("Tasks by Priority")
        priority_data = task_stats["by_priority"]
        st.bar_chart(priority_data)

    st.markdown("---")

    # Chronicle captures overview
    st.header("Chronicle Captures")
    col1, col2, col3 = st.columns(3)

    col1.metric("Total Captures", capture_stats.get("total_calls", 0))
    col2.metric("Error Rate", f"{capture_stats.get('error_rate', 0)}%")
    col3.metric("Avg Duration", f"{capture_stats.get('avg_duration_ms', 0):.2f}ms")

    # Function breakdown
    if capture_stats.get("by_function"):
        st.subheader("Captures by Function")
        func_data = capture_stats["by_function"]
        st.bar_chart(func_data)

    # Auto-refresh
    if st.checkbox("Auto-refresh (5s)", value=False):
        time.sleep(5)
        st.rerun()


def render_task_queue():
    """Render the task queue management page."""
    st.title("Task Queue Management")

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        status_filter = st.selectbox(
            "Filter by Status",
            ["All", "pending", "claimed", "processing", "completed", "failed"],
        )
    with col2:
        priority_filter = st.selectbox(
            "Filter by Priority", ["All", "low", "medium", "high", "critical"]
        )
    with col3:
        limit = st.slider("Limit", 10, 100, 50)

    # Fetch tasks
    params = {"limit": limit}
    if status_filter != "All":
        params["status"] = status_filter
    if priority_filter != "All":
        params["priority"] = priority_filter

    tasks = api_request("GET", "/tasks", params=params)

    if tasks:
        st.subheader(f"Tasks ({len(tasks)} shown)")

        for task in tasks:
            with st.expander(f"**{task['title']}** - {task['status'].upper()}", expanded=False):
                col1, col2 = st.columns(2)
                with col1:
                    st.text(f"ID: {task['id']}")
                    st.text(f"Priority: {task['priority']}")
                    st.text(f"Status: {task['status']}")
                    st.text(f"Created: {task['created_at']}")
                with col2:
                    st.text(f"Retries: {task['retry_count']}/{task['max_retries']}")
                    if task.get("claimed_by"):
                        st.text(f"Claimed by: {task['claimed_by']}")
                    if task.get("error_message"):
                        st.error(f"Error: {task['error_message']}")

                if task.get("payload"):
                    st.json(task["payload"])

                if task.get("result"):
                    st.success("Result:")
                    st.json(task["result"])
    else:
        st.info("No tasks found.")

    st.markdown("---")

    # Create new task
    st.subheader("Create New Task")
    with st.form("create_task"):
        title = st.text_input("Title", value="Manual test task")
        description = st.text_area("Description", value="Created from UI")
        priority = st.selectbox("Priority", ["low", "medium", "high", "critical"], index=1)
        payload = st.text_area("Payload (JSON)", value='{"test": true}')

        if st.form_submit_button("Create Task"):
            try:
                payload_dict = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                st.error("Invalid JSON in payload")
                payload_dict = None

            if payload_dict is not None:
                result = api_request(
                    "POST",
                    "/tasks",
                    json={
                        "title": title,
                        "description": description,
                        "priority": priority,
                        "payload": payload_dict,
                    },
                )
                if result:
                    st.success(f"Task created: {result['id']}")
                    st.rerun()

    st.markdown("---")

    # Admin actions
    st.subheader("Admin Actions")
    col1, col2 = st.columns(2)

    with col1:
        if st.button("Clear All Tasks", type="secondary"):
            result = api_request("POST", "/admin/clear-tasks")
            if result:
                st.success(f"Deleted {result['deleted']} tasks")
                st.rerun()

    with col2:
        if st.button("Clear All Captures", type="secondary"):
            result = api_request("POST", "/admin/clear-captures")
            if result:
                st.success(f"Deleted {result['deleted']} captures")
                st.rerun()


def render_captures():
    """Render the Chronicle captures exploration page."""
    st.title("Chronicle Captures")
    st.markdown("Explore captured function calls and their inputs/outputs.")

    # Filters
    col1, col2, col3 = st.columns(3)

    # Get available functions
    functions = api_request("GET", "/captures/functions")
    func_list = ["All"] + list(functions.get("functions", {}).keys()) if functions else ["All"]

    with col1:
        func_filter = st.selectbox("Function", func_list)
    with col2:
        error_filter = st.selectbox("Error Status", ["All", "Errors Only", "Success Only"])
    with col3:
        limit = st.slider("Limit", 10, 200, 50, key="capture_limit")

    # Build params
    params = {"limit": limit}
    if func_filter != "All":
        params["function_name"] = func_filter
    if error_filter == "Errors Only":
        params["has_error"] = True
    elif error_filter == "Success Only":
        params["has_error"] = False

    # Fetch captures
    captures = api_request("GET", "/captures", params=params)

    if captures and captures.get("calls"):
        st.subheader(f"Captured Calls ({captures['count']} shown)")

        for call in captures["calls"]:
            status_icon = "🔴" if call.get("exception") else "🟢"
            duration = call.get("duration_ms", 0)

            with st.expander(
                f"{status_icon} **{call['function_name']}** - {duration:.2f}ms",
                expanded=False,
            ):
                col1, col2 = st.columns(2)

                with col1:
                    st.text(f"ID: {call['id']}")
                    st.text(f"Module: {call.get('module', 'N/A')}")
                    st.text(f"Duration: {duration:.2f}ms")
                    st.text(f"Time: {call['start_time']}")

                with col2:
                    if call.get("trace_id"):
                        st.text(f"Trace ID: {call['trace_id']}")
                    if call.get("span_id"):
                        st.text(f"Span ID: {call['span_id']}")

                # Arguments
                if call.get("args") or call.get("kwargs"):
                    st.subheader("Arguments")
                    if call.get("args"):
                        st.json({"args": call["args"]})
                    if call.get("kwargs"):
                        st.json({"kwargs": call["kwargs"]})

                # Result or Exception
                if call.get("exception"):
                    st.error(f"**{call.get('exception_type', 'Error')}**: {call['exception']}")
                elif call.get("result"):
                    st.success("Result:")
                    st.json(call["result"])

                # Dependencies
                if call.get("dependencies"):
                    st.subheader("Dependencies")
                    for dep in call["dependencies"]:
                        st.json(dep)
    else:
        st.info("No captures found.")

    # Auto-refresh
    if st.checkbox("Auto-refresh captures (3s)", value=False):
        time.sleep(3)
        st.rerun()


def render_simulator():
    """Render the traffic simulator control page."""
    st.title("Traffic Simulator")
    st.markdown("Configure and control the traffic generator.")

    # Get simulator instance
    simulator = get_simulator()

    # Status
    is_running = simulator.is_running()
    status = "🟢 Running" if is_running else "🔴 Stopped"
    st.subheader(f"Status: {status}")

    # Controls
    col1, col2, col3 = st.columns(3)

    with col1:
        if is_running:
            if st.button("Stop Simulator", type="primary"):
                simulator.stop()
                st.rerun()
        else:
            if st.button("Start Simulator", type="primary"):
                simulator.start()
                st.rerun()

    with col2:
        if st.button("Reset Stats"):
            simulator.reset_stats()
            st.rerun()

    with col3:
        pass  # Placeholder for future controls

    st.markdown("---")

    # Configuration
    st.subheader("Configuration")

    col1, col2 = st.columns(2)

    with col1:
        rps = st.slider(
            "Requests per Second",
            min_value=0.1,
            max_value=10.0,
            value=simulator.config.requests_per_second,
            step=0.1,
        )
        if rps != simulator.config.requests_per_second:
            simulator.update_config(requests_per_second=rps)

    with col2:
        # Error injection
        error_config = api_request("GET", "/admin/error-injection")
        current_rate = error_config.get("rate", 0) if error_config else 0

        error_rate = st.slider(
            "Error Injection Rate",
            min_value=0.0,
            max_value=0.5,
            value=current_rate,
            step=0.01,
            help="Probability of injecting an error into API calls",
        )
        if error_rate != current_rate:
            api_request("POST", "/admin/error-injection", json={"rate": error_rate})

    # Operation weights
    st.subheader("Operation Weights")
    st.markdown("Adjust the probability of each operation type.")

    weights = {}
    cols = st.columns(4)

    operations = [
        ("read", "Read (list/get)", 0),
        ("create", "Create", 1),
        ("claim", "Claim", 2),
        ("process", "Process", 3),
        ("complete", "Complete", 0),
        ("update", "Update", 1),
        ("delete", "Delete", 2),
    ]

    for op_name, label, col_idx in operations:
        current = simulator.config.weights.get(
            getattr(__import__("demo.simulator", fromlist=["OperationType"]).OperationType, op_name.upper()),
            0.1,
        )
        with cols[col_idx % 4]:
            weights[op_name] = st.slider(
                label,
                min_value=0.0,
                max_value=1.0,
                value=current,
                step=0.01,
                key=f"weight_{op_name}",
            )

    if st.button("Update Weights"):
        simulator.update_config(weights=weights)
        st.success("Weights updated!")

    st.markdown("---")

    # Statistics
    st.subheader("Simulator Statistics")
    stats = simulator.get_stats()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Requests", stats["total_requests"])
    col2.metric("Successful", stats["successful_requests"])
    col3.metric("Failed", stats["failed_requests"])
    col4.metric("Success Rate", f"{stats['success_rate']}%")

    if stats.get("requests_by_type"):
        st.subheader("Requests by Type")
        st.bar_chart(stats["requests_by_type"])

    if stats.get("recent_errors"):
        st.subheader("Recent Errors")
        for error in stats["recent_errors"]:
            st.error(f"{error['time']}: {error['endpoint']} - {error['error']}")

    # Auto-refresh when running
    if is_running:
        if st.checkbox("Auto-refresh stats (2s)", value=True):
            time.sleep(2)
            st.rerun()


def render_analysis():
    """Render the analysis and testing page."""
    st.title("Analysis & Testing")
    st.markdown("Use Chronicle's BehaviorAgent to analyze captured behavior.")

    # Check if we have captures
    stats = api_request("GET", "/stats")
    if not stats or stats.get("captures", {}).get("total_calls", 0) == 0:
        st.warning("No captures available. Start the simulator to generate some data first.")
        return

    capture_stats = stats.get("captures", {})
    functions = capture_stats.get("by_function", {})

    if not functions:
        st.warning("No function captures available.")
        return

    st.subheader("Function Analysis")

    # Select function
    selected_func = st.selectbox("Select Function", list(functions.keys()))

    if selected_func:
        # Get captures for this function
        captures = api_request(
            "GET", "/captures", params={"function_name": selected_func, "limit": 100}
        )

        if captures and captures.get("calls"):
            calls = captures["calls"]

            # Basic stats
            col1, col2, col3, col4 = st.columns(4)

            total = len(calls)
            errors = sum(1 for c in calls if c.get("exception"))
            avg_duration = sum(c.get("duration_ms", 0) for c in calls) / total if total else 0

            col1.metric("Total Calls", total)
            col2.metric("Errors", errors)
            col3.metric("Error Rate", f"{errors/total*100:.1f}%" if total else "0%")
            col4.metric("Avg Duration", f"{avg_duration:.2f}ms")

            # Duration distribution
            st.subheader("Duration Distribution")
            durations = [c.get("duration_ms", 0) for c in calls]
            if durations:
                import pandas as pd
                df = pd.DataFrame({"duration_ms": durations})
                st.line_chart(df)

            # Error patterns
            if errors > 0:
                st.subheader("Error Patterns")
                error_calls = [c for c in calls if c.get("exception")]
                error_types = {}
                for c in error_calls:
                    et = c.get("exception_type", "Unknown")
                    error_types[et] = error_types.get(et, 0) + 1

                st.bar_chart(error_types)

                st.markdown("**Recent Errors:**")
                for c in error_calls[:5]:
                    st.error(f"{c.get('exception_type')}: {c.get('exception')}")

    st.markdown("---")

    # Test generation placeholder
    st.subheader("Test Generation")
    st.info(
        "Test generation from captured calls will be available when the full Chronicle "
        "BehaviorAgent is integrated. For now, captures are stored and can be exported."
    )

    if st.button("Export Captures (JSON)"):
        captures = api_request("GET", "/captures", params={"limit": 500})
        if captures:
            st.download_button(
                "Download Captures",
                data=json.dumps(captures, indent=2),
                file_name=f"chronicle_captures_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
            )

    st.markdown("---")

    # Query interface placeholder
    st.subheader("Natural Language Queries")
    st.info(
        "The BehaviorAgent query interface will allow natural language questions like:\n"
        "- 'What inputs cause errors in create_task?'\n"
        "- 'Show me the slowest calls'\n"
        "- 'How has behavior changed today?'"
    )

    query = st.text_input("Query (coming soon)", placeholder="What causes errors in create_task?")
    if query:
        st.warning("Query interface not yet connected to BehaviorAgent.")


def main():
    """Main application entry point."""
    page = render_sidebar()

    if page == "Dashboard":
        render_dashboard()
    elif page == "Task Queue":
        render_task_queue()
    elif page == "Chronicle Captures":
        render_captures()
    elif page == "Traffic Simulator":
        render_simulator()
    elif page == "Analysis":
        render_analysis()


if __name__ == "__main__":
    main()
