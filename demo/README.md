# Chronicle Demo App

A demonstration application showcasing Chronicle's capture, replay, and analysis capabilities using a **Task Queue System**.

## Overview

This demo implements a complete task queue system with:
- **FastAPI Backend** - REST API for task CRUD operations and workflow management
- **Streamlit Dashboard** - UI for monitoring, configuration, and analysis
- **Traffic Simulator** - Configurable load generator with realistic patterns
- **Chronicle Integration** - Function capture with OpenTelemetry instrumentation

## Quick Start

### 1. Install Dependencies

```bash
cd demo
pip install -r requirements.txt
```

### 2. Start the API Server

```bash
# From the Chronicle root directory
uvicorn demo.api:app --reload --port 8000
```

The API will be available at `http://localhost:8000` with interactive docs at `http://localhost:8000/docs`.

**Chronicle Dashboard**: Access the built-in Chronicle dashboard at `http://localhost:8000/_chronicle` to view and configure capture settings in real-time.

### 3. Start the Streamlit Dashboard

```bash
# In a new terminal, from the Chronicle root directory
streamlit run demo/app.py
```

The dashboard will open at `http://localhost:8501`.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Streamlit Dashboard                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐│
│  │Dashboard │  │Task Queue│  │ Captures │  │Traffic Simulator ││
│  │ Overview │  │  Manager │  │ Explorer │  │    Controls      ││
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI Backend                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  Task CRUD   │  │   Workflow   │  │   Chronicle Capture  │  │
│  │  Endpoints   │  │  Endpoints   │  │      Decorator       │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│                              │                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              OpenTelemetry Instrumentation               │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┴──────────────────┐
           ▼                                      ▼
    ┌─────────────┐                      ┌─────────────────┐
    │  Tasks DB   │                      │  Captures DB    │
    │  (SQLite)   │                      │    (SQLite)     │
    └─────────────┘                      └─────────────────┘
```

## Features

### Task Queue System

The demo implements a job queue with full lifecycle management:

| Operation | Description |
|-----------|-------------|
| **Create** | Add new tasks to the queue |
| **Claim** | Worker claims a pending task |
| **Process** | Mark task as being processed |
| **Complete** | Mark task as successfully finished |
| **Fail** | Mark task as failed (with retry support) |

Task properties:
- **Status**: pending → claimed → processing → completed/failed
- **Priority**: low, medium, high, critical
- **Retries**: Configurable max retries with automatic requeue

### Traffic Simulator

Generates realistic traffic patterns with configurable:

- **Requests per second** (0.1 - 10 RPS)
- **Operation weights** (default: 50% reads, 20% creates, etc.)
- **Error injection rate** (0% - 50%)

Default weights (realistic CRUD ratio):
```
Read:     50%
Create:   20%
Claim:    10%
Process:   8%
Complete:  7%
Update:    3%
Delete:    2%
```

### Chronicle Captures

All API endpoint functions are decorated with `@capture`, recording:

- Function name and module
- Input arguments (args, kwargs)
- Return values or exceptions
- Execution duration
- OpenTelemetry trace/span IDs
- Dependency calls (DB, HTTP, etc.)

### Chronicle Dashboard UI

The demo includes Chronicle's built-in dashboard at `/_chronicle` with:

- **Real-time Statistics**: View capture counts, error rates, and sampling stats
- **Sampling Configuration**: Adjust sampling strategy, rates, and thresholds
- **Type-Based Limits**: Configure capture limits per payload type value
  - Set field path (e.g., `"type"` or `"payload.type"`)
  - Set limit per type (default: 5000)
  - Configure action when limit reached (stop or sample)
- **Function-Based Limits**: Configure capture limits per function name
  - Set limit per function (default: 5000)
  - Prevents database storage after limit reached
- **Endpoint Management**: View all endpoints and configure per-endpoint limits
- **Alerts**: See notifications when limits are reached
- **Recent Captures**: Browse captured requests with filtering

### Capture Limiters

The demo is pre-configured with two capture limiters:

1. **Type-Based Limiting**: Limits captures per payload `type` field value
   - Default: 5000 captures per unique type
   - Stops recording that type when limit reached
   - Configurable via dashboard

2. **Function-Based Limiting**: Limits captures per function name
   - Default: 5000 captures per function
   - Stops storing to database when limit reached
   - Prevents database bloat from high-traffic functions
   - Configurable via dashboard

Both limiters can be adjusted in real-time through the dashboard without restarting the server.

### Dashboard Pages

#### Streamlit Dashboard (Port 8501)

1. **Dashboard** - Real-time overview with task and capture statistics
2. **Task Queue** - Browse, filter, and manage tasks; create new tasks
3. **Chronicle Captures** - Explore captured function calls with full details
4. **Traffic Simulator** - Control the load generator, adjust weights
5. **Analysis** - Function statistics, error patterns, duration distributions

#### Chronicle Dashboard (Port 8000, `/_chronicle`)

A lightweight web dashboard for real-time capture management:

1. **Capture Stats** - Total captures, errors, duration, sampling stats
2. **Sampling Settings** - Adjust strategy, rates, and thresholds
3. **Type-Based Limits** - Configure limits per payload type value
4. **Function-Based Limits** - Configure limits per function name
5. **Endpoint Configuration** - View and configure per-endpoint limits
6. **Captures by Type** - Monitor type capture counts and limits
7. **Captures by Function** - Monitor function capture counts and limits
8. **Alerts** - Notifications when limits are reached
9. **Recent Captures** - Browse captured HTTP requests

## API Endpoints

### Tasks

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/tasks` | Create a new task |
| GET | `/tasks` | List tasks (with filters) |
| GET | `/tasks/{id}` | Get task by ID |
| PATCH | `/tasks/{id}` | Update a task |
| DELETE | `/tasks/{id}` | Delete a task |

### Workflow

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/tasks/{id}/claim` | Claim a specific task |
| POST | `/tasks/claim-next` | Claim next available task |
| POST | `/tasks/{id}/process` | Start processing a task |
| POST | `/tasks/{id}/complete` | Complete a task |
| POST | `/tasks/{id}/fail` | Fail a task |

### Chronicle

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/captures` | List captured calls |
| GET | `/captures/functions` | List captured function names |
| GET | `/_chronicle` | Chronicle Dashboard UI |
| GET | `/_chronicle/api/stats` | Get capture statistics |
| GET | `/_chronicle/api/type-limits` | Get type limit configuration |
| POST | `/_chronicle/api/type-limits` | Update type limit configuration |
| GET | `/_chronicle/api/function-limits` | Get function limit configuration |
| POST | `/_chronicle/api/function-limits` | Update function limit configuration |
| GET | `/_chronicle/api/endpoints` | List all captured endpoints |

### Admin

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/stats` | Get task and capture statistics |
| GET | `/health` | Health check |
| POST | `/admin/clear-tasks` | Clear all tasks |
| POST | `/admin/clear-captures` | Clear all captures |
| GET/POST | `/admin/error-injection` | Get/set error injection rate |

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | None | OTLP endpoint for traces |
| `ENVIRONMENT` | development | Deployment environment tag |

### Database Files

- `demo_tasks.db` - Task queue storage
- `chronicle_captures.db` - Function capture storage

Both are SQLite files created automatically in the working directory.

## Capture Limiters

### Type-Based Limits

Limit captures based on payload field values. Example:

```bash
# Request with type field
curl -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Welcome Email",
    "priority": "high",
    "type": "USER_SIGNUP",
    "payload": {"email": "user@example.com"}
  }'
```

Configure in code:
```python
from Chronicle.integrations import configure_type_limits, TypeLimitConfig

configure_type_limits(TypeLimitConfig(
    field_path="type",         # Extract from top-level "type" field
    limit_per_type=5000,       # Capture up to 5000 of each type
    alert_on_limit=True,
    limit_action="stop",
))
```

Or configure via dashboard at `/_chronicle`:
- Enable "Type-Based Limits"
- Set field path (e.g., `"type"` or `"payload.type"`)
- Set limit per type
- Choose action when limit reached

### Function-Based Limits

Limit captures per function name to prevent database bloat:

```python
from Chronicle.integrations import configure_function_limits, FunctionLimitConfig

configure_function_limits(FunctionLimitConfig(
    limit_per_function=5000,   # Capture up to 5000 per function
    alert_on_limit=True,
    limit_action="stop",       # Stop storing to DB when limit hit
))
```

Or configure via dashboard at `/_chronicle`:
- Enable "Function-Based Limits"
- Set limit per function
- Choose action when limit reached

When a function reaches its limit, it will:
- Continue executing normally
- Stop storing captures to the database
- Show an alert in the dashboard
- Allow you to reset the limit to resume capturing

## Example Usage

### Create a Task via API

```bash
curl -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Process order #12345",
    "priority": "high",
    "payload": {"order_id": "12345", "amount": 99.99}
  }'
```

### Process Tasks (Worker Pattern)

```bash
# Claim next task
TASK=$(curl -s "http://localhost:8000/tasks/claim-next?worker_id=worker-1")
TASK_ID=$(echo $TASK | jq -r '.id')

# Start processing
curl -X POST "http://localhost:8000/tasks/$TASK_ID/process" \
  -H "Content-Type: application/json" \
  -d '{"worker_id": "worker-1"}'

# Complete with result
curl -X POST "http://localhost:8000/tasks/$TASK_ID/complete" \
  -H "Content-Type: application/json" \
  -d '{"output": "processed", "result_code": 200}'
```

### View Captures

```bash
# List all captures
curl http://localhost:8000/captures

# Filter by function
curl "http://localhost:8000/captures?function_name=create_task"

# Filter errors only
curl "http://localhost:8000/captures?has_error=true"
```

## OpenTelemetry Integration

The demo includes full OpenTelemetry instrumentation:

- **Automatic tracing** of all FastAPI endpoints
- **Trace context propagation** to Chronicle captures
- **Console export** for demo visibility
- **OTLP export** support for production use

To view traces in Jaeger:

```bash
# Start Jaeger
docker run -d --name jaeger \
  -p 16686:16686 \
  -p 4317:4317 \
  jaegertracing/all-in-one:latest

# Set endpoint and restart API
export OTEL_EXPORTER_OTLP_ENDPOINT=localhost:4317
uvicorn demo.api:app --reload --port 8000

# View traces at http://localhost:16686
```

## Development

### Project Structure

```
demo/
├── __init__.py       # Package init
├── models.py         # Task data models
├── database.py       # SQLite database layer
├── capture.py        # Chronicle capture implementation
├── api.py            # FastAPI application
├── simulator.py      # Traffic generator
├── telemetry.py      # OpenTelemetry configuration
├── app.py            # Streamlit dashboard
├── requirements.txt  # Dependencies
└── README.md         # This file
```

### Running Tests

```bash
# From Chronicle root
pytest demo/tests/ -v
```

### Adding New Endpoints

1. Add the endpoint function in `api.py`
2. Decorate with `@capture` to enable Chronicle tracking
3. Add corresponding UI in the appropriate Streamlit page

## Troubleshooting

### API not responding

1. Check if uvicorn is running: `ps aux | grep uvicorn`
2. Check port 8000 is free: `lsof -i :8000`
3. View logs in terminal where uvicorn is running

### Streamlit connection errors

1. Ensure API is running first
2. Check `API_BASE_URL` in `app.py` matches your setup
3. Try refreshing the browser

### No captures appearing

1. Verify `@capture` decorator is on endpoint functions
2. Check `chronicle_captures.db` exists
3. View captures directly: `curl http://localhost:8000/captures`

## License

MIT License - See main Chronicle repository for details.
