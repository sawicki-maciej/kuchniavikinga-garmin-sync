# Use official uv image
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# Copy dependency files
COPY pyproject.toml ./
COPY README.md ./

# Install dependencies using uv
# Use --frozen to ensure reproducible builds
RUN uv pip install --system --no-cache .

# Copy application code
COPY kuchniavikinga_garmin_sync/ ./kuchniavikinga_garmin_sync/

# Run as non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Run the sync
CMD ["python", "-m", "kuchniavikinga_garmin_sync.main"]
