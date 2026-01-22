# Test Suite for Chronicle Demo App

This test suite is generated from captured function call data, ensuring tests reflect real-world usage patterns.

## Structure

```
tests/
├── conftest.py                    # Shared fixtures and configuration
├── demo/
│   ├── test_api_from_captures.py  # Tests generated from captured data
│   ├── test_api_uncaptured.py     # Tests for functions not in captures
│   ├── test_database.py           # Database layer tests
│   ├── test_integration.py        # Integration and workflow tests
│   └── test_edge_cases.py         # Edge cases and boundary conditions
└── fixtures/
    └── chronicle_captures_20260120_210049.json  # Captured data
```

## Running Tests

### Quick Start (Recommended):
```bash
# From the tests directory
python run_tests.py

# With coverage report
python run_tests.py --coverage

# Run specific test file
python run_tests.py --file test_api_from_captures.py
```

### Alternative: Direct pytest commands
```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=demo --cov-report=html

# Run specific test file
pytest tests/demo/test_api_from_captures.py -v
```

## Test Categories

### 1. Tests from Captured Data (`test_api_from_captures.py`)

These tests use real captured function calls as test cases:
- `create_task`: 18 captured examples
- `list_tasks`: 24 captured examples  
- `claim_next_task`: 23 captured examples (including error cases)
- `process_task`: 11 captured examples
- `get_task`: 10 captured examples
- `update_task`: 4 captured examples
- `complete_task`: 3 captured examples
- `delete_task`: 1 captured example

Each test replays the exact captured call with its parameters and verifies the result matches the captured output.

### 2. Tests for Uncaptured Functions (`test_api_uncaptured.py`)

Tests for functions not present in captured data:
- `claim_task`
- `fail_task`
- `health_check`
- `get_stats`
- `clear_tasks`
- `clear_captures`
- `get_error_injection` / `set_error_injection`
- `list_captures` / `list_captured_functions`

### 3. Database Layer Tests (`test_database.py`)

Direct tests for database methods using patterns from captured API calls:
- CRUD operations
- Workflow operations (claim, process, complete, fail)
- Filtering and pagination
- Statistics

### 4. Integration Tests (`test_integration.py`)

Complete workflow tests:
- Full task lifecycle (create → claim → process → complete)
- Priority ordering
- Task retry workflows
- Error scenarios
- Concurrent operations

### 5. Edge Cases (`test_edge_cases.py`)

Boundary conditions and edge cases:
- Minimal/maximal field values
- Empty/null inputs
- Invalid inputs
- State transition edge cases
- Payload variations

## Captured Data

The test suite uses captured data from `chronicle_captures_20260120_210049.json` which contains:
- 94 function calls
- 8 different functions
- Success and error cases
- Real-world parameter combinations

## Fixtures

### `temp_db`
Creates a temporary SQLite database for each test, ensuring test isolation.

### `temp_capture_db`
Creates a temporary capture storage database.

### `captured_calls`
Loads and organizes captured function calls by function name.

### `sample_task_data`
Provides sample task data for testing.

## Writing New Tests

When adding new tests:

1. **Use captured data when available**: Check if the function has captured examples in `test_api_from_captures.py`

2. **Follow patterns**: Use existing tests as templates for similar functions

3. **Test both success and error cases**: Include both happy path and error scenarios

4. **Use fixtures**: Leverage `temp_db` and `temp_capture_db` for isolation

5. **Verify state**: Check not just return values but also database state

## Coverage

Current test coverage targets:
- ✅ Core API functions: 100% (from captures)
- ✅ Database layer: ~90%
- ✅ Admin functions: 100%
- ✅ Error handling: ~80%
- ✅ Edge cases: ~70%

## Notes

- Tests use temporary databases to ensure isolation
- Error injection is disabled in tests (`error_injection_rate = 0.0`)
- Some tests may skip if required setup data isn't available
- Integration tests may take longer due to full workflow execution
