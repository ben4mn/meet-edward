#!/bin/bash
# First-time Edward setup for macOS
set -e

echo "=== Edward First-Time Setup ==="

# Check for Homebrew
if ! command -v brew &>/dev/null; then
  echo "Error: Homebrew not found. Install from https://brew.sh"
  exit 1
fi

# Install PostgreSQL 16 + pgvector
echo "Installing PostgreSQL and pgvector..."
brew install postgresql@16 pgvector
brew services start postgresql@16

# Wait for Postgres
for i in {1..10}; do pg_isready -q && break; sleep 1; done

# Create database and user
echo "Setting up database..."
createuser edward 2>/dev/null || true
createdb -O edward edward 2>/dev/null || true
psql -d edward -c "ALTER USER edward WITH PASSWORD 'edward';" 2>/dev/null || true
psql -d edward -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || true

# Backend Python setup
echo "Setting up backend..."
cd "$(dirname "$0")/backend"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Frontend setup
echo "Setting up frontend..."
cd ../frontend
npm install

# Create .env from template if needed
cd ..
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from template. Edit it to add your ANTHROPIC_API_KEY."
fi

echo ""
echo "=== Setup Complete ==="
echo "1. Add your ANTHROPIC_API_KEY to .env"
echo "2. Run: ./restart.sh"
