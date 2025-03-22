FROM python:3.12-slim

# Install FFmpeg and other dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Add timezone support and set default timezone
RUN apt-get update && apt-get install -y tzdata
ENV TZ=UTC

# Create app directory
WORKDIR /app

# Copy requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directories for recordings, logs, and instance
RUN mkdir -p /data/recordings /data/logs /data/instance

# Set environment variables
ENV FLASK_APP=app.py
ENV RECORDINGS_FOLDER=/data/recordings
ENV INSTANCE_PATH=/data/instance
ENV LOGS_PATH=/data/logs
ENV DATABASE_URL=sqlite:////data/instance/radio_recorder.db
ENV PORT=5000

# Expose port
EXPOSE 5000

# Create entrypoint script
RUN echo '#!/bin/bash\n\
mkdir -p $RECORDINGS_FOLDER\n\
mkdir -p $LOGS_PATH\n\
mkdir -p $INSTANCE_PATH\n\
exec python app.py\n' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Set entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]