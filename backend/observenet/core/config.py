"""Process configuration (environment-driven, immutable at runtime).

Runtime-tunable settings (retention, thresholds, scope) live in the database and
are handled by ``observenet.services.settings``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OBSERVENET_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "production"
    demo_mode: bool = False

    database_url: str = "postgresql+psycopg://observenet:observenet@postgres:5432/observenet"
    redis_url: str = "redis://redis:6379/0"

    # Auth / edge
    secret_key: str = Field(default="", description="Used to HMAC session + CSRF tokens at rest.")
    admin_username: str = "admin"
    admin_password: str = ""
    bootstrap_agent_token: str = ""
    public_url: str = "http://localhost:8080"
    allowed_hosts: str = "localhost,127.0.0.1"
    cookie_secure: bool | None = None  # None -> derived from public_url scheme
    session_ttl_hours: int = 12
    agent_token_ttl_days: int = 90
    login_rate_limit_per_minute: int = 8
    ingest_rate_limit_per_minute: int = 240

    # GraphQL hardening
    graphql_introspection: bool | None = None  # None -> enabled only outside production
    graphql_max_depth: int = 8

    # Observability
    metrics_port: int = 0  # 0 disables the internal Prometheus listener
    otel_exporter_otlp_endpoint: str = ""
    log_level: str = "INFO"
    log_json: bool = True

    # Optional integrations
    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    alert_webhook_url: str = ""
    alert_webhook_secret: str = ""
    oui_file: str = ""

    @field_validator("database_url")
    @classmethod
    def _force_psycopg(cls, v: str) -> str:
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+psycopg://", 1)
        return v

    @property
    def cookie_secure_effective(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.public_url.startswith("https://")

    @property
    def introspection_enabled(self) -> bool:
        if self.graphql_introspection is not None:
            return self.graphql_introspection
        return self.env != "production"

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def public_origin(self) -> str:
        return self.public_url.rstrip("/")


@lru_cache
def get_config() -> Config:
    return Config()
