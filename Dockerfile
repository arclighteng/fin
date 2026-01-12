FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml /app/
COPY src/ /app/src/
RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir -e .

# data + exports are mounted by docker-compose
CMD ["fin", "--help"]
