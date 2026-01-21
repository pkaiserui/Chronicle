#!/usr/bin/env python3
"""
Run script for the Chronicle Demo.

Usage:
    python -m demo.run api      # Start the FastAPI server
    python -m demo.run ui       # Start the Streamlit dashboard
    python -m demo.run both     # Start both (API in background)
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path


def run_api(host: str = "0.0.0.0", port: int = 8000, reload: bool = True):
    """Run the FastAPI server."""
    cmd = [
        sys.executable, "-m", "uvicorn",
        "demo.api:app",
        "--host", host,
        "--port", str(port),
    ]
    if reload:
        cmd.append("--reload")

    print(f"Starting API server at http://{host}:{port}")
    print(f"API docs at http://{host}:{port}/docs")
    subprocess.run(cmd, cwd=Path(__file__).parent.parent)


def run_ui(port: int = 8501):
    """Run the Streamlit dashboard."""
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(Path(__file__).parent / "app.py"),
        "--server.port", str(port),
    ]

    print(f"Starting Streamlit dashboard at http://localhost:{port}")
    subprocess.run(cmd, cwd=Path(__file__).parent.parent)


def run_both(api_port: int = 8000, ui_port: int = 8501):
    """Run both API and UI."""
    import threading

    # Start API in background thread
    api_cmd = [
        sys.executable, "-m", "uvicorn",
        "demo.api:app",
        "--host", "0.0.0.0",
        "--port", str(api_port),
    ]

    print(f"Starting API server at http://localhost:{api_port}")
    api_process = subprocess.Popen(
        api_cmd,
        cwd=Path(__file__).parent.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Wait for API to start
    time.sleep(2)

    # Start UI (blocking)
    print(f"Starting Streamlit dashboard at http://localhost:{ui_port}")
    try:
        run_ui(ui_port)
    finally:
        api_process.terminate()


def main():
    parser = argparse.ArgumentParser(description="Run the Chronicle Demo")
    parser.add_argument(
        "component",
        choices=["api", "ui", "both"],
        help="Component to run: api, ui, or both",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=8000,
        help="Port for the API server (default: 8000)",
    )
    parser.add_argument(
        "--ui-port",
        type=int,
        default=8501,
        help="Port for the Streamlit UI (default: 8501)",
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable auto-reload for API server",
    )

    args = parser.parse_args()

    if args.component == "api":
        run_api(port=args.api_port, reload=not args.no_reload)
    elif args.component == "ui":
        run_ui(port=args.ui_port)
    elif args.component == "both":
        run_both(api_port=args.api_port, ui_port=args.ui_port)


if __name__ == "__main__":
    main()
