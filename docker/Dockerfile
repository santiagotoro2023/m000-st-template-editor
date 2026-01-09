# Multi-stage build for optimal image size
FROM python:3.11-slim AS base

# Install system dependencies for PDF conversion and document handling
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice \
    libreoffice-calc \
    libreoffice-impress \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt gunicorn uvicorn[standard]

# Copy application code
COPY app.py .

# Copy all static and template files
COPY static/ ./static/
COPY templates/ ./templates/

# Create required directories with proper permissions
RUN mkdir -p /app/data /app/temp /app/archive && \
    chmod 755 /app/data /app/temp /app/archive

# Non-root user for security
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/').read()" || exit 1

# Run with Gunicorn + Uvicorn workers for production
CMD ["gunicorn", \
     "--workers", "4", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
