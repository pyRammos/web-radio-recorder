#!/bin/bash

# Create this as start.sh in your project directory
PROJECT_DIR="$(dirname "$(readlink -f "$0")")"
cd "$PROJECT_DIR" || exit

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Load environment variables if .env exists
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Start the application
python app.py