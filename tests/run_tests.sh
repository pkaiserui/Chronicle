#!/bin/bash
# Test runner script for Chronicle Demo App tests

set -e

echo "🧪 Running Chronicle Demo App Test Suite"
echo "========================================"
echo ""

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    echo "❌ pytest is not installed. Installing..."
    pip install pytest pytest-cov pytest-asyncio
fi

# Check if captured data exists
CAPTURED_DATA="fixtures/chronicle_captures_20260120_210049.json"
if [ ! -f "$CAPTURED_DATA" ]; then
    echo "⚠️  Warning: Captured data file not found at $CAPTURED_DATA"
    echo "   Tests that require captured data may be skipped."
    echo ""
fi

# Run tests
echo "📋 Running all tests..."
pytest tests/ -v --tb=short

echo ""
echo "✅ Test run complete!"
echo ""
echo "To run with coverage:"
echo "  pytest tests/ --cov=demo --cov-report=html"
echo ""
echo "To run specific test file:"
echo "  pytest tests/demo/test_api_from_captures.py -v"
