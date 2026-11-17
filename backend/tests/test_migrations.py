"""The schema under test is produced by the real Alembic migration (see conftest)."""

from __future__ import annotations

from sqlalchemy import text

from observenet.core.db import session_scope
from observenet.models import Base


async def test_migration_creates_every_model_table_and_is_at_head() -> None:
    async with session_scope() as session:
        existing = set(
            (
                await session.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
            ).scalars()
        )
        version = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    assert version == "0001"
    missing = {t.name for t in Base.metadata.sorted_tables} - existing
    assert not missing, f"tables missing after migration: {missing}"
    required = {
        "assets", "asset_identities", "interfaces", "address_observations", "data_sources", "collector_heartbeats",
        "capabilities", "network_segments", "metric_samples", "metric_rollups_1m", "metric_rollups_1h", "probe_targets",
        "probe_results", "outages", "events", "alerts", "alert_rules", "notifications", "assessments", "checks",
        "findings", "finding_evidence", "remediation_steps", "verification_runs", "verification_diffs", "users",
        "sessions", "api_tokens", "audit_events", "settings",
    }  # fmt: skip
    assert required <= existing


async def test_metric_samples_is_range_partitioned_with_a_default_partition() -> None:
    async with session_scope() as session:
        strategy = (
            await session.execute(
                text(
                    "SELECT partstrat FROM pg_partitioned_table pt JOIN pg_class c ON c.oid = pt.partrelid "
                    "WHERE c.relname = 'metric_samples'"
                )  # fmt: skip
            )
        ).scalar_one()
        default = (await session.execute(text("SELECT to_regclass('metric_samples_default')"))).scalar()
    assert strategy in ("r", b"r")
    assert default is not None


async def test_models_and_migration_do_not_drift() -> None:
    """Autogenerate against the migrated DB must find nothing to add/remove."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def diff(sync_conn: object) -> list[object]:
        ctx = MigrationContext.configure(sync_conn, opts={"compare_type": True})  # type: ignore[arg-type]
        return [d for d in compare_metadata(ctx, Base.metadata) if "metric_samples_default" not in repr(d)]

    async with session_scope() as session:
        conn = await session.connection()
        changes = await conn.run_sync(diff)
    assert changes == []


async def test_real_mode_database_starts_empty_of_measurements() -> None:
    async with session_scope() as session:
        for table in ("metric_samples", "assets", "findings", "probe_results", "data_sources"):
            assert (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0  # noqa: S608
