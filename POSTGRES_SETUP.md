# PostgreSQL Setup Guide

This guide explains how to set up and use PostgreSQL with the Chronicle demo application.

## Quick Start

### Option 1: Using Docker (Recommended)

1. **Start PostgreSQL using Docker Compose:**
   ```bash
   ./setup_postgres.sh
   ```
   
   This script will:
   - Start a PostgreSQL container
   - Wait for it to be ready
   - Create a `.env` file with the connection credentials

2. **Install Python dependencies:**
   ```bash
   cd demo
   pip install -r requirements.txt
   ```

3. **Run the application:**
   ```bash
   python run.py
   ```

### Option 2: Manual Setup

1. **Start PostgreSQL container manually:**
   ```bash
   docker-compose up -d postgres
   ```

2. **Create a `.env` file in the project root:**
   ```env
   DB_TYPE=postgres
   POSTGRES_HOST=localhost
   POSTGRES_PORT=5432
   POSTGRES_USER=chronicle_user
   POSTGRES_PASSWORD=chronicle_password
   POSTGRES_DB=chronicle_db
   ```

3. **Install Python dependencies:**
   ```bash
   cd demo
   pip install -r requirements.txt
   ```

## Database Credentials

The default PostgreSQL credentials are:

- **Host:** localhost
- **Port:** 5432
- **User:** chronicle_user
- **Password:** chronicle_password
- **Database:** chronicle_db

These can be customized by editing the `.env` file or the `docker-compose.yml` file.

## Environment Variables

The application reads database configuration from environment variables. You can set these in a `.env` file:

```env
# Database type: 'postgres' or 'sqlite' (default: 'sqlite')
DB_TYPE=postgres

# PostgreSQL connection settings
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=chronicle_user
POSTGRES_PASSWORD=chronicle_password
POSTGRES_DB=chronicle_db

# Alternative: Use a full connection string
# POSTGRES_DSN=postgresql://chronicle_user:chronicle_password@localhost:5432/chronicle_db

# SQLite paths (used when DB_TYPE=sqlite)
SQLITE_TASKS_DB=demo_tasks.db
SQLITE_CAPTURES_DB=chronicle_captures.db
```

## Switching Between SQLite and PostgreSQL

To switch back to SQLite, simply set `DB_TYPE=sqlite` in your `.env` file or remove the `.env` file (SQLite is the default).

## Managing the PostgreSQL Container

- **Start:** `docker-compose up -d postgres`
- **Stop:** `docker-compose down`
- **View logs:** `docker-compose logs -f postgres`
- **Restart:** `docker-compose restart postgres`

## Database Schema

The application automatically creates the required tables on first run:
- `tasks` - Task queue data
- `captured_calls` - Chronicle capture data

## Troubleshooting

### Connection Errors

If you see connection errors:

1. **Check if PostgreSQL is running:**
   ```bash
   docker ps | grep chronicle_postgres
   ```

2. **Check PostgreSQL logs:**
   ```bash
   docker-compose logs postgres
   ```

3. **Verify connection credentials in `.env` file match `docker-compose.yml`**

### Port Already in Use

If port 5432 is already in use, you can change it in `docker-compose.yml`:

```yaml
ports:
  - "5433:5432"  # Use 5433 on host instead
```

Then update your `.env` file:
```env
POSTGRES_PORT=5433
```

### Missing psycopg2

If you get an error about `psycopg2-binary`:

```bash
pip install psycopg2-binary
```

Or install all requirements:
```bash
cd demo
pip install -r requirements.txt
```

## Data Persistence

The PostgreSQL data is stored in a Docker volume named `postgres_data`. This means your data will persist even if you stop and restart the container.

To completely remove the database (including all data):
```bash
docker-compose down -v
```
