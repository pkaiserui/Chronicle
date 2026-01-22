# Chronicle

**Capture, replay, and intelligently refactor backend services.**

Chronicle is a Python package that records function inputs, outputs, and data flows in your backend system, then provides that data in an agentic way so services can be recreated or intelligently refactored without affecting their external behavior.

## Features

- 🎯 **Capture** - Record function I/O with OpenTelemetry integration
- 🔄 **Replay** - Validate refactored code against captured behavior  
- 🧪 **Test Generation** - Auto-generate pytest tests from real traffic
- 🤖 **Agentic Interface** - Natural language queries over captured behavior
- 🔒 **PII Redaction** - Built-in filtering for sensitive data
- 📊 **Smart Sampling** - Clustering-based dedup for similar requests

## Installation

```bash
pip install Chronicle

# With PostgreSQL support
pip install Chronicle[postgres]

# Full installation with all optional dependencies
pip install Chronicle[full]
```

## Quick Start

### 1. Configure and Capture

```python
from Chronicle import capture, configure, CaptureConfig

# Configure (call once at startup)
configure(CaptureConfig(
    storage_backend="sqlite",
    storage_dsn="Chronicle.db",
    sampling_rate=0.1,  # Capture 10% of calls
    sampling_strategy="clustering",  # Smart dedup for similar requests
    retention_days=30,
    redaction_enabled=True,
))

# Decorate functions to capture
@capture
def process_order(order_id: str, items: list, user_id: str) -> dict:
    # Your existing logic
    total = sum(item["price"] * item["qty"] for item in items)
    return {"order_id": order_id, "total": total, "status": "confirmed"}

@capture
async def validate_user(user_id: str) -> bool:
    # Async functions work too
    return await check_user_exists(user_id)
```

### 2. Replay for Safe Refactoring

```python
from Chronicle import ReplayEngine, SQLiteStorage

storage = SQLiteStorage("Chronicle.db")
engine = ReplayEngine(storage)

# Test a refactored implementation
def process_order_v2(order_id: str, items: list, user_id: str) -> dict:
    # New, optimized implementation
    total = sum(i["price"] * i["qty"] for i in items)
    return {"order_id": order_id, "total": total, "status": "confirmed"}

# Replay captured calls through new implementation
report = engine.replay(
    function_name="mymodule.process_order",
    new_implementation=process_order_v2,
    limit=1000,
)

print(report.summary())
# Replay Report: mymodule.process_order
# ==================================================
# Total calls:  1000
# Passed:       998 (99.8%)
# Failed:       2
# Errors:       0
# Duration:     12.34s
# Avg speedup:  1.45x

# Investigate failures
for result in report.results:
    if result.status.value == "failed":
        print(f"Failed: {result.difference}")
```

### 3. Generate Tests from Real Traffic

```python
from Chronicle import BehaviorAgent, SQLiteStorage

storage = SQLiteStorage("Chronicle.db")
agent = BehaviorAgent(storage)

# Generate pytest test file
agent.generate_test_file(
    function_name="mymodule.process_order",
    output_path="tests/test_process_order_regression.py",
    count=20,
)
```

Generated tests look like:

```python
def test_process_order_1():
    """
    Auto-generated regression test from captured behavior.
    Call ID: abc123
    Captured: 2024-01-15T10:30:00Z
    """
    from mymodule import process_order
    
    inputs = {
        "order_id": "ORD-12345",
        "items": [{"name": "Widget", "price": 9.99, "qty": 2}],
        "user_id": "USR-789"
    }
    
    result = process_order(**inputs)
    
    expected = {
        "order_id": "ORD-12345",
        "total": 19.98,
        "status": "confirmed"
    }
    
    assert result == expected
```

### 4. Query Behavior with Natural Language

```python
from Chronicle import BehaviorAgent, SQLiteStorage

storage = SQLiteStorage("Chronicle.db")
agent = BehaviorAgent(storage)

# Ask questions about your captured behavior
result = agent.query("What inputs cause errors in process_order?")
print(result)
# {
#   "type": "error_analysis",
#   "total_errors": 42,
#   "error_types": {
#     "ValueError": {
#       "count": 35,
#       "example_inputs": [...],
#       "example_messages": ["Invalid quantity: -1", ...]
#     },
#     "KeyError": {
#       "count": 7,
#       "example_inputs": [...],
#     }
#   }
# }

# Performance analysis
result = agent.query("Show me the slowest calls to validate_user")

# Behavioral drift detection
result = agent.query("How has process_order behavior changed this week?")
```

## Configuration Options

```python
from Chronicle import CaptureConfig

config = CaptureConfig(
    # Storage
    storage_backend="sqlite",  # or "postgres"
    storage_dsn="Chronicle.db",  # or postgres connection string
    
    # Sampling (important for your 10k requests/day)
    sampling_rate=0.1,  # Base rate
    sampling_strategy="clustering",  # "random", "clustering", or "adaptive"
    cluster_similarity_threshold=0.85,  # For clustering sampler
    
    # Retention
    retention_days=30,
    max_records_per_function=100_000,
    
    # PII Redaction
    redaction_enabled=True,
    redaction_patterns=[
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # Email
        r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
    ],
    redaction_fields=["password", "secret", "token", "api_key"],
    
    # OpenTelemetry
    otel_enabled=True,
    otel_service_name="my-service",
    
    # Capture settings
    capture_exceptions=True,
    capture_timing=True,
    capture_call_stack=True,
    max_serialized_size=1_000_000,  # 1MB per value
    
    # Dependency tracking
    track_db_calls=True,
    track_http_calls=True,
    track_file_io=True,
)
```

## Dependency Tracking

Automatically capture database queries, HTTP calls, and file I/O:

```python
from Chronicle.dependencies import install_all_hooks

# Call once at startup
install_all_hooks()

# Now all DB/HTTP/file calls within @capture functions are recorded
@capture
def fetch_user_data(user_id: str) -> dict:
    # This DB query will be captured as a dependency
    user = db.execute("SELECT * FROM users WHERE id = ?", user_id)
    
    # This HTTP call will be captured too
    enrichment = requests.get(f"https://api.example.com/users/{user_id}")
    
    return {**user, **enrichment.json()}
```

## Sampling Strategies

### Random Sampling
Simple probabilistic sampling - good for uniform traffic.

```python
configure(CaptureConfig(
    sampling_strategy="random",
    sampling_rate=0.1,  # Capture 10%
))
```

### Clustering Sampling (Recommended)
Smart deduplication for when "most requests are the same with slight changes":

```python
configure(CaptureConfig(
    sampling_strategy="clustering",
    sampling_rate=0.1,  # Base rate for similar inputs
    cluster_similarity_threshold=0.85,  # How similar inputs need to be to cluster
))
```

This ensures diverse inputs are captured while avoiding redundant similar calls.

### Adaptive Sampling
Automatically adjusts rate based on errors and novelty:

```python
from Chronicle.sampling import AdaptiveSampler

sampler = AdaptiveSampler(
    min_rate=0.01,
    max_rate=1.0,
    error_boost_factor=5.0,  # Increase sampling when errors occur
    novelty_boost_factor=3.0,  # Increase for new input patterns
)
```

## Integration with FastAPI

### Basic Integration

```python
from fastapi import FastAPI
from Chronicle import capture, configure, CaptureConfig
from Chronicle.dependencies import install_all_hooks

app = FastAPI()

# Configure on startup
@app.on_event("startup")
def setup_Chronicle():
    configure(CaptureConfig(
        storage_backend="postgres",
        storage_dsn="postgresql://user:pass@localhost/Chronicle",
        sampling_strategy="clustering",
        sampling_rate=0.1,
    ))
    install_all_hooks()

# Your service functions
@capture
def process_payment(payment_id: str, amount: float) -> dict:
    # ... implementation ...
    return {"status": "success"}

@app.post("/payments")
async def create_payment(payment_id: str, amount: float):
    return process_payment(payment_id, amount)
```

### Chronicle Dashboard UI

Chronicle includes an optional lightweight web dashboard for viewing and adjusting capture settings in real-time:

```python
from fastapi import FastAPI
from Chronicle.integrations import (
    ChronicleMiddleware,
    mount_chronicle_dashboard,
    configure_type_limits,
    configure_function_limits,
    TypeLimitConfig,
    FunctionLimitConfig,
    SamplingConfig,
    SamplingStrategy,
    configure_sampling,
)

app = FastAPI()

# Add Chronicle middleware for automatic HTTP request/response capture
app.add_middleware(ChronicleMiddleware)

# Configure sampling strategy
configure_sampling(SamplingConfig(
    strategy=SamplingStrategy.CLUSTERING,
    base_rate=0.2,  # 20% baseline sampling
    always_capture_errors=True,
    always_capture_slow=True,
    latency_threshold_ms=500,
))

# Configure type-based capture limits
# Limits captures per payload "type" field value
configure_type_limits(TypeLimitConfig(
    field_path="type",         # Extract from request body.type
    limit_per_type=5000,       # Capture up to 5000 of each type
    alert_on_limit=True,       # Show alert when limit reached
    limit_action="stop",       # Stop recording that type when limit hit
))

# Configure function-based capture limits
# Limits captures per function name (prevents DB storage after limit)
configure_function_limits(FunctionLimitConfig(
    limit_per_function=5000,   # Capture up to 5000 per function
    alert_on_limit=True,       # Show alert when limit reached
    limit_action="stop",       # Stop recording to DB when limit hit
))

# Mount the dashboard at /_chronicle
mount_chronicle_dashboard(app, path="/_chronicle", enabled=True)
```

Access the dashboard at `http://localhost:8000/_chronicle` to:
- View real-time capture statistics
- Adjust sampling strategy and rates
- Configure type-based limits per endpoint
- Configure function-based limits
- Monitor capture counts and alerts
- Reset limits to resume capturing

### Capture Limiters

Chronicle provides two types of capture limiters to control storage volume:

#### Type-Based Limits

Limit captures based on payload field values (e.g., `type`, `event_type`):

```python
from Chronicle.integrations import configure_type_limits, TypeLimitConfig

configure_type_limits(TypeLimitConfig(
    field_path="type",              # JSON path to extract type (e.g., "type", "payload.type")
    limit_per_type=5000,            # Maximum captures per unique type value
    alert_on_limit=True,            # Create alert when limit reached
    limit_action="stop",             # "stop" or "sample" (sample at low rate)
    overflow_sample_rate=0.01,      # Sample rate if action is "sample"
))
```

Example: If your request body has `{"type": "USER_SIGNUP", ...}`, Chronicle will capture up to 5000 requests with `type="USER_SIGNUP"`, then stop recording that specific type.

#### Function-Based Limits

Limit captures per function name to prevent database bloat:

```python
from Chronicle.integrations import configure_function_limits, FunctionLimitConfig

configure_function_limits(FunctionLimitConfig(
    limit_per_function=5000,         # Maximum captures per function
    alert_on_limit=True,            # Create alert when limit reached
    limit_action="stop",             # "stop" or "sample"
    overflow_sample_rate=0.01,      # Sample rate if action is "sample"
))
```

Example: The `create_task` function will be captured up to 5000 times, then stop storing to the database. The function still executes normally, but captures are no longer persisted.

## CLI Usage

```bash
# View captured functions
Chronicle functions

# Query specific function
Chronicle query "mymodule.process_order" --errors

# Generate tests
Chronicle generate-tests "mymodule.process_order" -o tests/

# Cleanup old records
Chronicle cleanup --days 30

# Export captured data
Chronicle export "mymodule.process_order" -o captures.json
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Your Python Backend                         │
│   @capture decorated functions                                   │
│         │                                                        │
│   ┌─────┴─────────────────────────────────────────┐             │
│   │         Chronicle Middleware               │             │
│   │  • Captures I/O                               │             │
│   │  • Integrates with OpenTelemetry              │             │
│   │  • Tracks dependencies (DB/HTTP/File)         │             │
│   └─────────────────┬─────────────────────────────┘             │
└─────────────────────┼───────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Capture Pipeline                              │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐     │
│  │ Sampling │ → │ Redaction│ → │ Serialize│ → │ Storage  │     │
│  │ Strategy │   │ (PII)    │   │          │   │ Backend  │     │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘     │
└─────────────────────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Analysis & Replay Layer                        │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │ Replay Engine   │  │ Test Generator  │  │ Agent Interface │ │
│  │                 │  │                 │  │                 │ │
│  │ Validate new    │  │ pytest from     │  │ NL queries      │ │
│  │ implementations │  │ real traffic    │  │ over behavior   │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## License

MIT License - see LICENSE file for details.
