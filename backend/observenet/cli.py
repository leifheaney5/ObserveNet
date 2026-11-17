"""Operator CLI: ``python -m observenet.cli <command>``."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

from observenet.auth import service
from observenet.core.db import dispose_engine, session_scope


async def _create_admin(username: str, password: str | None) -> int:
    password = password or getpass.getpass("New admin password (12+ chars): ")
    async with session_scope() as session:
        try:
            user = await service.create_user(session, username=username, password=password, role="admin")
        except service.AuthError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print(f"created admin '{user.username}'")
    return 0


async def _create_token(name: str, ttl_days: int) -> int:
    async with session_scope() as session:
        raw, row = await service.issue_agent_token(session, name=name, created_by=None, ttl_days=ttl_days)
    print(f"token id={row.id} expires={row.expires_at}\n{raw}\n(store it now; it is not shown again)")
    return 0


def _export_openapi(path: str) -> int:
    from observenet.api.app import create_app

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(create_app().openapi(), fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {path}")
    return 0


def _export_graphql(path: str) -> int:
    from observenet.graphql.schema import build_schema

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build_schema().as_str() + "\n")
    print(f"wrote {path}")
    return 0


async def _run(coro: object) -> int:
    try:
        return await coro  # type: ignore[misc]
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(prog="observenet")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin", help="create an administrator account")
    p.add_argument("--username", default="admin")
    p.add_argument("--password", default=None, help="omit to be prompted")
    p = sub.add_parser("create-agent-token", help="issue a collector token")
    p.add_argument("--name", required=True)
    p.add_argument("--ttl-days", type=int, default=90)
    p = sub.add_parser("export-openapi", help="write the OpenAPI document")
    p.add_argument("path")
    p = sub.add_parser("export-graphql", help="write the GraphQL SDL")
    p.add_argument("path")
    args = parser.parse_args()
    if args.cmd == "create-admin":
        return asyncio.run(_run(_create_admin(args.username, args.password)))
    if args.cmd == "create-agent-token":
        return asyncio.run(_run(_create_token(args.name, args.ttl_days)))
    if args.cmd == "export-openapi":
        return _export_openapi(args.path)
    return _export_graphql(args.path)


if __name__ == "__main__":
    sys.exit(main())
