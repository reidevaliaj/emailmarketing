# Single image used by the app, worker, beat, and migrate services.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# All wheels are prebuilt (asyncpg, psycopg[binary], argon2-cffi), so no
# system build toolchain is needed — keeps the image small.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Non-root runtime user; shared uploads dir (mounted as a volume in compose).
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /var/lib/emailmarketing/uploads \
    && chown -R appuser:appuser /app /var/lib/emailmarketing
USER appuser

EXPOSE 8000

# Default: serve the API behind gunicorn + uvicorn workers. Overridden by the
# worker/beat/migrate services in docker-compose.
CMD ["gunicorn", "app.main:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-b", "0.0.0.0:8000", "-w", "2", \
     "--access-logfile", "-", "--error-logfile", "-"]
