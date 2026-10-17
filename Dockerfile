# Use official uv image
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# Copy requirements file
COPY requirements.txt ./

# Install dependencies using uv
RUN uv pip install --system --no-cache -r requirements.txt

# Copy application code
COPY kuchniavikinga_garmin_sync/ ./kuchniavikinga_garmin_sync/

# Run as non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Run the sync
CMD ["python", "-m", "kuchniavikinga_garmin_sync.main"]
