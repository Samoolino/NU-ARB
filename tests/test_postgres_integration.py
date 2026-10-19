"""Disposable-PostgreSQL integration checks; never point this at production."""
import os
import uuid

import psycopg
import pytest

from arbx.postgres import run_postgres_migrations


@pytest.fixture(scope="module")
def database_url():
    value = os.getenv("ARBX_TEST_POSTGRES_URL")
    if not value:
        pytest.skip("set ARBX_TEST_POSTGRES_URL for disposable PostgreSQL integration tests")
    if "localhost" not in value and "127.0.0.1" not in value and "postgres" not in value:
        pytest.fail("integration tests only accept a local/disposable PostgreSQL host")
    run_postgres_migrations(value)
    return value


def test_migrations_are_idempotent(database_url):
    assert run_postgres_migrations(database_url) == []
    with psycopg.connect(database_url) as conn:
        versions = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert versions == [("001",), ("002",), ("003",), ("004",)]


def test_foreign_key_and_unique_constraints_are_enforced(database_url):
    email = f"integration-{uuid.uuid4().hex}@example.invalid"
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
            (email, "test-hash"),
        ).fetchone()
        user_id = row[0]
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO users (email, password_hash) VALUES (%s, %s)",
                    (email, "other-hash"),
                )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.transaction():
                conn.execute(
                    """INSERT INTO exchange_credentials
                       (user_id, venue, auth_mode, encrypted_payload)
                       VALUES (%s, %s, %s, %s)""",
                    (user_id + 999999, "fake-venue", "test", b"ciphertext"),
                )


def test_transaction_rollback_leaves_no_partial_row(database_url):
    email = "rollback@example.invalid"
    with psycopg.connect(database_url) as conn:
        with pytest.raises(RuntimeError, match="force rollback"):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO users (email, password_hash) VALUES (%s, %s)",
                    (email, "test-hash"),
                )
                raise RuntimeError("force rollback")
        count = conn.execute("SELECT count(*) FROM users WHERE email = %s", (email,)).fetchone()[0]
    assert count == 0


def test_concurrent_unique_insert_has_one_winner(database_url):
    from concurrent.futures import ThreadPoolExecutor

    email = "concurrency@example.invalid"
    with psycopg.connect(database_url) as conn:
        conn.execute("DELETE FROM users WHERE email = %s", (email,))

    def insert_if_absent(label):
        with psycopg.connect(database_url) as conn:
            result = conn.execute(
                """INSERT INTO users (email, password_hash) VALUES (%s, %s)
                   ON CONFLICT (email) DO NOTHING""",
                (email, label),
            )
            return result.rowcount

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(insert_if_absent, ("first", "second")))
    assert sum(outcomes) == 1
    with psycopg.connect(database_url) as conn:
        count = conn.execute("SELECT count(*) FROM users WHERE email = %s", (email,)).fetchone()[0]
    assert count == 1
