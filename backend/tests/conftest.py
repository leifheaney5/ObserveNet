"""Shared test fixtures.

* A REAL PostgreSQL database (``observenet_test``) migrated with the real Alembic revision,
  so the migration itself is exercised on every run.
* fakeredis for Redis, a Dramatiq StubBroker for the queue (``OBSERVENET_ENV=test``).
* One event loop for the whole session (see pyproject) so the async engine pool stays valid.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

TEST_DB_URL = os.environ.get(
    "OBSERVENET_TEST_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:55432/observenet_test"
)
BASE_ENV = {
    "OBSERVENET_ENV": "test",
    "OBSERVENET_SECRET_KEY": "test-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz",
    "OBSERVENET_DATABASE_URL": TEST_DB_URL,
    "OBSERVENET_REDIS_URL": "redis://127.0.0.1:1/15",  # never used: fakeredis is injected
    "OBSERVENET_DEMO_MODE": "false",
    "OBSERVENET_ADMIN_PASSWORD": "",
    "OBSERVENET_BOOTSTRAP_AGENT_TOKEN": "",
    "OBSERVENET_METRICS_PORT": "0",
    "OBSERVENET_PUBLIC_URL": "http://testserver",
    "OBSERVENET_OTEL_EXPORTER_OTLP_ENDPOINT": "",
    "OBSERVENET_NEO4J_URI": "",
    "OBSERVENET_ALERT_WEBHOOK_URL": "",
    "OBSERVENET_OUI_FILE": "",
    "OBSERVENET_LOG_LEVEL": "WARNING",
}
os.environ.update(BASE_ENV)
for _name in ("OBSERVENET_GRAPHQL_INTROSPECTION", "OBSERVENET_COOKIE_SECURE", "OBSERVENET_ALLOWED_HOSTS"):
    os.environ.pop(_name, None)

# Everything below may import application modules: the environment is final.
import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from observenet.core.config import get_config  # noqa: E402

get_config.cache_clear()

from observenet.auth import service as auth_service  # noqa: E402
from observenet.core import redis as redis_module  # noqa: E402
from observenet.core.db import dispose_engine, session_scope  # noqa: E402
from observenet.models import Base  # noqa: E402
from observenet.services import timeseries  # noqa: E402
from observenet.workers.broker import broker  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]
ADMIN_PASSWORD = "correct-horse-battery-staple"
VIEWER_PASSWORD = "viewer-password-long-enough"


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    """Reset the test database and build the schema with the real migration (upgrade head)."""
    assert "observenet_test" in TEST_DB_URL or os.environ.get("OBSERVENET_TEST_ALLOW_ANY_DB") == "1", (
        "refusing to wipe a database that is not named *observenet_test*"
    )
    engine = create_engine(TEST_DB_URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.cmd_opts = argparse.Namespace(x=[f"url={TEST_DB_URL}"])
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")  # the downgrade path must work too (circular FK, partitions)
    command.upgrade(cfg, "head")
    yield TEST_DB_URL


_db_use = {"checkouts": 1}  # start dirty so the very first test truncates


def _track_checkouts() -> None:
    from sqlalchemy import event

    from observenet.core.db import get_engine

    engine = get_engine().sync_engine
    if not event.contains(engine, "checkout", _on_checkout):
        event.listen(engine, "checkout", _on_checkout)


def _on_checkout(*_: Any) -> None:
    _db_use["checkouts"] += 1


@pytest_asyncio.fixture(autouse=True)
async def clean_state(migrated_database: str) -> AsyncIterator[None]:
    """Fresh tables, fresh fake Redis, empty stub queues and default config for every test.

    Truncation is skipped when the previous test never touched the database (pure-logic tests).
    """
    os.environ.update(BASE_ENV)
    get_config.cache_clear()
    _track_checkouts()
    if _db_use["checkouts"]:
        await _truncate_all()
        _db_use["checkouts"] = 0
    timeseries.clear_caches()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis_module.set_redis(fake)
    broker.flush_all()
    yield
    redis_module.set_redis(None)
    await fake.aclose()
    get_config.cache_clear()


async def _truncate_all() -> None:
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with session_scope() as session:
        partitions = (
            await session.execute(
                text(
                    "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                    "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'metric_samples' "
                    "AND c.relname <> 'metric_samples_default'"
                )  # fmt: skip
            )
        ).scalars()
        for name in list(partitions):
            await session.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
        await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_engine_at_end() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest.fixture
def set_config(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Change process configuration for one test: ``set_config(demo_mode="true")``."""

    def _set(**values: str) -> None:
        for key, value in values.items():
            monkeypatch.setenv(f"OBSERVENET_{key.upper()}", value)
        get_config.cache_clear()

    return _set


@pytest_asyncio.fixture
async def app() -> Any:
    from observenet.api.app import bootstrap, create_app

    await bootstrap()  # what the lifespan does, minus the live hub / engine teardown
    return create_app()


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest_asyncio.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with _client(app) as c:
        yield c


async def login(c: httpx.AsyncClient, username: str, password: str) -> httpx.Response:
    resp = await c.post("/api/v1/auth/login", json={"username": username, "password": password})
    if resp.status_code == 200:
        c.headers["x-csrf-token"] = resp.json()["csrf_token"]
    return resp


@pytest_asyncio.fixture
async def admin_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with session_scope() as session:
        await auth_service.create_user(session, username="admin", password=ADMIN_PASSWORD, role="admin")
    async with _client(app) as c:
        assert (await login(c, "admin", ADMIN_PASSWORD)).status_code == 200
        yield c


@pytest_asyncio.fixture
async def viewer_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with session_scope() as session:
        await auth_service.create_user(session, username="viewer", password=VIEWER_PASSWORD, role="viewer")
    async with _client(app) as c:
        assert (await login(c, "viewer", VIEWER_PASSWORD)).status_code == 200
        yield c


@pytest_asyncio.fixture
async def agent_token() -> str:
    async with session_scope() as session:
        raw, _row = await auth_service.issue_agent_token(
            session, name="test agent", created_by=None, ttl_days=30
        )
    return raw


@pytest_asyncio.fixture
async def default_rules() -> None:
    """Seed the alert-rule and check catalogs (the API does this at startup)."""
    from observenet.security.engine import sync_check_catalog
    from observenet.services.alerts import ensure_default_rules

    async with session_scope() as session:
        await ensure_default_rules(session)
        await sync_check_catalog(session)
