# --- Portfolio Watchdog Dockerfile ---
# Optimized for Google Cloud Run deployment

# Use slim Python image for smaller container size
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install dependencies first (leverages Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and the static dashboard.
# .dockerignore keeps .env, venv/ and the local database out of the image.
# frontend/ MUST be included - FastAPI mounts it at "/" to serve the dashboard.
COPY app/ ./app/
COPY frontend/ ./frontend/

# Cloud Run expects the container to listen on port 8080
ENV PORT=8080

# One service serves both the API and the dashboard:
#   /            -> frontend/index.html
#   /api, /docs, /watchlist, /portfolio, /history, /run-cycle, /reset, /scheduler
#
# Shell form so Cloud Run's injected $PORT is honoured, falling back to 8080.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
