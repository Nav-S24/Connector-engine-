FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY mock_server ./mock_server
COPY connectors ./connectors
COPY benchmarks ./benchmarks
COPY tests ./tests

RUN pip install --upgrade pip && pip install -e ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "mock_server.app:app", "--host", "0.0.0.0", "--port", "8000"]
