FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    patch \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/repo

RUN git clone https://github.com/pallets/click.git /workspace/repo \
    && git checkout cf19179e8e2fd54c601378cec9e0d21bb12b6fb1 \
    && pip install --upgrade pip \
    && pip install -e . pytest
