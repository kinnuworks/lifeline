# Live engine for judges: the API that runs a full analysis on any area and date.
# Runs on Hugging Face Spaces (Docker, port 7860) or anywhere Docker runs:
#   docker build -t lifeline . && docker run -p 7860:7860 --env-file .env lifeline
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    GDAL_HTTP_MULTIPLEX=YES \
    GDAL_HTTP_VERSION=2

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

RUN useradd -m -u 1000 user
WORKDIR /app
COPY --chown=user pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY --chown=user src ./src
COPY --chown=user scripts ./scripts
COPY --chown=user docs ./docs
COPY --chown=user runs ./runs
RUN uv sync --frozen --no-dev && mkdir -p data && chown -R user /app
USER user
EXPOSE 7860
CMD ["uv", "run", "--no-sync", "uvicorn", "lifeline.api.server:app", "--host", "0.0.0.0", "--port", "7860"]
