FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# --- Daemon ---
FROM base AS daemon
CMD ["python", "src/main.py"]

# --- Web ---
FROM base AS web
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8080", "--app-dir", "src"]
