#!/usr/bin/env python3
"""
Test runner script for Chronicle Demo App tests.

Usage:
    python run_tests.py              # Run all tests
    python run_tests.py --coverage   # Run with coverage report
    python run_tests.py --file test_api_from_captures.py  # Run specific file
"""

import os
import sys
import subprocess
from pathlib import Path


def check_pytest():
    """Check if pytest is installed."""
    try:
        import pytest
        return True
    except ImportError:
        return False


def install_pytest():
    """Install pytest and dependencies."""
    print("📦 Installing pytest and dependencies...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pytest", "pytest-cov", "pytest-asyncio"], check=True)


def check_captured_data():
    """Check if captured data file exists."""
    possible_paths = [
        Path(__file__).parent / "fixtures" / "chronicle_captures_20260120_210049.json",
        Path(__file__).parent.parent / "Downloads" / "chronicle_captures_20260120_210049.json",
        Path.home() / "Downloads" / "chronicle_captures_20260120_210049.json",
    ]
    
    for path in possible_paths:
        if path.exists():
            return True
    
    return False


def run_tests(coverage=False, file=None, verbose=True):
    """Run pytest with appropriate arguments."""
    # Get the project root (parent of tests directory)
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    
    # Change to project root for pytest
    original_cwd = Path.cwd()
    
    cmd = [sys.executable, "-m", "pytest"]
    
    if verbose:
        cmd.append("-v")
    
    if coverage:
        cmd.extend(["--cov=demo", "--cov-report=html", "--cov-report=term"])
    
    if file:
        # If file doesn't have .py extension, add it
        if not file.endswith(".py"):
            file = file + ".py"
        cmd.append(f"tests/demo/{file}")
    else:
        cmd.append("tests/")
    
    cmd.append("--tb=short")
    
    print(f"🚀 Running: {' '.join(cmd)}")
    print(f"📁 Working directory: {project_root}")
    print()
    
    try:
        os.chdir(project_root)
        result = subprocess.run(cmd)
        return result.returncode == 0
    finally:
        os.chdir(original_cwd)


def main():
    """Main entry point."""
    print("🧪 Chronicle Demo App Test Suite")
    print("=" * 50)
    print()
    
    # Check pytest
    if not check_pytest():
        print("❌ pytest is not installed.")
        response = input("   Install pytest and dependencies? (y/n): ")
        if response.lower() == 'y':
            install_pytest()
        else:
            print("   Please install pytest: pip install pytest pytest-cov pytest-asyncio")
            sys.exit(1)
    
    # Check captured data
    if not check_captured_data():
        print("⚠️  Warning: Captured data file not found.")
        print("   Tests that require captured data may be skipped.")
        print("   Expected location: tests/fixtures/chronicle_captures_20260120_210049.json")
        print()
    
    # Parse arguments
    coverage = "--coverage" in sys.argv or "-c" in sys.argv
    file = None
    if "--file" in sys.argv:
        idx = sys.argv.index("--file")
        if idx + 1 < len(sys.argv):
            file = sys.argv[idx + 1]
    
    # Run tests
    success = run_tests(coverage=coverage, file=file)
    
    print()
    if success:
        print("✅ All tests passed!")
    else:
        print("❌ Some tests failed.")
        sys.exit(1)
    
    if coverage:
        print()
        print("📊 Coverage report generated in htmlcov/index.html")


if __name__ == "__main__":
    main()
