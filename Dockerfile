# Production Dockerfile for AE-03 Directive V2 FastAPI Backend
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies required for PostgreSQL and builds
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ ./backend/
COPY additionals/ ./additionals/

EXPOSE 8000

ENV PORT=8000
ENV ENVIRONMENT=production

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
