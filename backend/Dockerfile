# syntax=docker/dockerfile:1.7
# ObserveNet backend image: API, worker, scheduler and migrations share it.
FROM python:3.12-slim-bookworm AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
# Optional `extra_ca` build secret: a PEM bundle for building behind a TLS-inspecting proxy.
# Absent in normal builds; never copied into the image.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install "uv==0.11.32"
COPY pyproject.toml uv.lock ./
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --extra graph --no-install-project
COPY observenet ./observenet
COPY migrations ./migrations
COPY alembic.ini ./
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --extra graph

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
RUN groupadd --gid 10001 observenet && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin observenet
WORKDIR /app
COPY --from=builder --chown=root:root /app /app
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "observenet.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
