FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install --no-cache-dir uv

COPY . /app

# Install locked runtime dependencies and expose the project venv on PATH.
RUN rm -rf /app/.venv && uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000
