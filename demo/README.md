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

### Dashboard Pages

1. **Dashboard** - Real-time overview with task and capture statistics
2. **Task Queue** - Browse, filter, and manage tasks; create new tasks
3. **Chronicle Captures** - Explore captured function calls with full details
4. **Traffic Simulator** - Control the load generator, adjust weights
5. **Analysis** - Function statistics, error patterns, duration distributions

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
