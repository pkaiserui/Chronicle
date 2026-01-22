#!/bin/bash
# Setup script for local PostgreSQL database

set -e

echo "Setting up local PostgreSQL database for Chronicle..."

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed. Please install Docker first."
    echo "Visit: https://docs.docker.com/get-docker/"
    exit 1
fi

# Check if docker-compose is available
if command -v docker-compose &> /dev/null; then
    COMPOSE_CMD="docker-compose"
elif docker compose version &> /dev/null; then
    COMPOSE_CMD="docker compose"
else
    echo "Error: docker-compose is not available."
    exit 1
fi

# Start PostgreSQL container
echo "Starting PostgreSQL container..."
$COMPOSE_CMD up -d postgres

# Wait for PostgreSQL to be ready
echo "Waiting for PostgreSQL to be ready..."
max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if docker exec chronicle_postgres pg_isready -U chronicle_user -d chronicle_db &> /dev/null; then
        echo "PostgreSQL is ready!"
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done

if [ $attempt -eq $max_attempts ]; then
    echo "Error: PostgreSQL did not become ready in time."
    exit 1
fi

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file with PostgreSQL credentials..."
    cat > .env << EOF
# Database Configuration
DB_TYPE=postgres

# PostgreSQL Connection Settings
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=chronicle_user
POSTGRES_PASSWORD=chronicle_password
POSTGRES_DB=chronicle_db
EOF
    echo ".env file created with default PostgreSQL credentials."
else
    echo ".env file already exists. Please ensure it contains PostgreSQL settings."
fi

echo ""
echo "PostgreSQL setup complete!"
echo ""
echo "Connection details:"
echo "  Host: localhost"
echo "  Port: 5432"
echo "  User: chronicle_user"
echo "  Password: chronicle_password"
echo "  Database: chronicle_db"
echo ""
echo "To stop PostgreSQL: docker-compose down"
echo "To view logs: docker-compose logs -f postgres"
