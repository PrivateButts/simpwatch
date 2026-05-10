FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install uv
RUN pip install --no-cache-dir uv

# Copy project config and install dependencies
COPY pyproject.toml uv.lock* /app/
RUN uv sync --frozen --no-dev

COPY . /app

EXPOSE 8000
