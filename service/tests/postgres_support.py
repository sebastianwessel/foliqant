"""Isolated real-PostgreSQL fixtures for durable storage integration tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest


def _postgres_program(name: str) -> str:
    program = shutil.which(name)
    if program is None:
        pytest.skip(f"PostgreSQL test program is unavailable: {name}")
    return program


def _run(command: list[str]) -> None:
    subprocess.run(
        command,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    """Use an explicit CI test database or own a temporary native cluster.

    ``FOLIQANT_TEST_POSTGRES_DSN`` must name a disposable test database: the
    per-test fixture intentionally drops the fixed ``foliqant`` schema.
    """
    configured = os.environ.get("FOLIQANT_TEST_POSTGRES_DSN")
    if configured:
        yield configured
        return

    initdb = _postgres_program("initdb")
    pg_ctl = _postgres_program("pg_ctl")
    with tempfile.TemporaryDirectory(prefix="foliqant-pg-", dir="/tmp") as temporary:
        root = Path(temporary)
        data = root / "data"
        socket = root / "socket"
        socket.mkdir()
        log = root / "postgres.log"
        _run(
            [
                initdb,
                "-D",
                str(data),
                "--auth=trust",
                "--username=postgres",
                "--no-locale",
                "--encoding=UTF8",
            ]
        )
        options = f"-F -h '' -k {socket} -p 55432"
        _run([pg_ctl, "-D", str(data), "-l", str(log), "-o", options, "-w", "start"])
        try:
            yield f"dbname=postgres user=postgres host={socket} port=55432"
        finally:
            subprocess.run(
                [pg_ctl, "-D", str(data), "-m", "immediate", "-w", "stop"],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


@pytest.fixture
async def isolated_postgres_dsn(postgres_dsn: str) -> AsyncIterator[str]:
    """Reset the adapter's fixed schema around every integration test."""
    async with await psycopg.AsyncConnection.connect(postgres_dsn, autocommit=True) as connection:
        await connection.execute("DROP SCHEMA IF EXISTS foliqant CASCADE")
    try:
        yield postgres_dsn
    finally:
        async with await psycopg.AsyncConnection.connect(
            postgres_dsn, autocommit=True
        ) as connection:
            await connection.execute("DROP SCHEMA IF EXISTS foliqant CASCADE")
