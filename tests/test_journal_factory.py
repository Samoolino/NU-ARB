from __future__ import annotations

import pytest

from arbx.journal import TradeJournal, create_trade_journal
from arbx.journal_storage import BackendTradeJournal


def test_factory_preserves_sqlite_default_and_requested_path(tmp_path, monkeypatch):
    monkeypatch.delenv("ARBX_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("ARBX_DATABASE_URL", raising=False)
    path = tmp_path / "selected.sqlite3"

    journal = create_trade_journal(path)
    try:
        assert isinstance(journal, TradeJournal)
        assert journal.path == path
    finally:
        journal.close()


def test_factory_selects_postgres_only_when_explicitly_configured(monkeypatch):
    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "ARBX_DATABASE_URL",
        "postgresql://nu_arb_test:nu_arb_test_only@localhost:5432/nu_arb_test",
    )

    journal = create_trade_journal("ignored-for-postgres.sqlite3")
    try:
        assert isinstance(journal, BackendTradeJournal)
        assert journal.dialect == "postgres"
    finally:
        journal.close()


def test_factory_fails_closed_for_missing_postgres_url(monkeypatch):
    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "postgres")
    monkeypatch.delenv("ARBX_DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="ARBX_DATABASE_URL"):
        create_trade_journal("ignored.sqlite3")


def test_factory_rejects_unknown_backend(monkeypatch):
    monkeypatch.setenv("ARBX_STORAGE_BACKEND", "mysql")

    with pytest.raises(ValueError, match="ARBX_STORAGE_BACKEND"):
        create_trade_journal("ignored.sqlite3")
