# syntax=docker/dockerfile:1.7
# ObserveNet Linux collector. Runs unprivileged with a read-only root filesystem.
FROM python:3.12-slim-bookworm AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml ./
COPY observenet_agent ./observenet_agent
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    python -m venv /opt/agent && /opt/agent/bin/pip install .

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/opt/agent/bin:$PATH" \
    OBSERVENET_AGENT_STATE_DIR=/var/lib/observenet-agent
RUN groupadd --gid 10002 agent && useradd --uid 10002 --gid 10002 --no-create-home --shell /usr/sbin/nologin agent \
    && mkdir -p /var/lib/observenet-agent && chown 10002:10002 /var/lib/observenet-agent
COPY --from=builder /opt/agent /opt/agent
USER 10002:10002
VOLUME ["/var/lib/observenet-agent"]
CMD ["python", "-m", "observenet_agent"]
