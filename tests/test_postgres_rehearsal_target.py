from __future__ import annotations

import pytest

from scripts.postgres_rehearsal_target import validate_disposable_target


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:pass@db.example.com:5432/test",
        "postgresql://user:pass@192.168.1.20:5432/test",
        "postgresql://user:pass@localhost:5432/production",
        "postgresql://user:pass@localhost:5432/app",
        "https://localhost/not-postgres",
        "",
    ],
)
def test_rehearsal_target_rejects_non_local_or_non_disposable_targets(url):
    with pytest.raises(ValueError):
        validate_disposable_target(url)


@pytest.mark.parametrize("url", [
    "postgresql://tester:secret@localhost:5432/nu_arb_rehearsal",
    "postgres://tester:secret@127.0.0.1:5432/test_db",
    "postgresql://tester:secret@postgres:5432/disposable_test",
])
def test_rehearsal_target_accepts_explicit_local_test_database(url):
    assert validate_disposable_target(url) is True


def test_rehearsal_target_rejects_production_url_even_if_local():
    target = "postgresql://tester:secret@localhost:5432/nu_arb_test"
    production = "postgresql://tester:secret@localhost:5432/nu_arb_test"

    with pytest.raises(ValueError, match="production"):
        validate_disposable_target(target, production_url=production)


def test_rehearsal_target_error_does_not_echo_credentials():
    secret_url = "postgresql://private-user:super-secret@remote.example.com:5432/prod"

    with pytest.raises(ValueError) as error:
        validate_disposable_target(secret_url)

    assert "private-user" not in str(error.value)
    assert "super-secret" not in str(error.value)
    assert "remote.example.com" not in str(error.value)
