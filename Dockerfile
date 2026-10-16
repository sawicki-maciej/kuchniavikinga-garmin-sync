FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY kuchniavikinga_garmin_sync/ ./kuchniavikinga_garmin_sync/

# Run as non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Run the sync
CMD ["python", "-m", "kuchniavikinga_garmin_sync.main"]
