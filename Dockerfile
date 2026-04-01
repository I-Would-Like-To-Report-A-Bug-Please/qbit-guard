# Dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install git for version detection
RUN apt-get update && apt-get install -y git && apt-get clean
RUN pip install --no-cache-dir loguru

WORKDIR /app
COPY src/guard.py /app/guard.py
COPY src/logging_setup.py /app/logging_setup.py
COPY src/watcher.py /app/watcher.py

# Create a version file during build
ARG BUILD_VERSION
ENV APP_VERSION=${BUILD_VERSION:-0.0.0-dev}

# Create version.py dynamically during build
RUN echo "VERSION = '${APP_VERSION}'" > /app/version.py

ENTRYPOINT ["python", "/app/watcher.py"]
