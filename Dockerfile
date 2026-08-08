# ── Stage 1: Build Next.js Frontend Static Export ──
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python FastAPI Application Server ──
FROM python:3.11-slim
WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend codebase and static frontend assets
COPY . .
COPY --from=frontend-builder /app/frontend/out ./frontend/out

# Hugging Face Spaces uses port 7860
EXPOSE 7860

# Launch unified server
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "7860"]
