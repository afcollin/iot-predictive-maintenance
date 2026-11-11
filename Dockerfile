FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Containeren kører som en almindelig bruger, ikke root (least privilege).
# appuser må kun skrive i /app/data, ikke i koden.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir /app/data && chown appuser /app/data

# Standard: SQLite i /app/data, så imaget kan køres alene med `docker run`.
# Docker Compose og Azure sætter DATABASE_URL til PostgreSQL.
ENV DATABASE_URL=sqlite:////app/data/iot.db

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).status == 200 else 1)"

# Request-logs skrives af appen selv som JSON, derfor --no-access-log
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
