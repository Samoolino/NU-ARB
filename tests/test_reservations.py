import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "arb_bot"))

from arbx.journal import TradeJournal


def test_reservation_is_atomic_and_durable(tmp_path):
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    manager = journal.reservations

    first = manager.acquire(
        session_id="s1",
        opportunity_id="o1",
        resources=[("inventory:binance:USDT", 8.0, 10.0), ("execution-slot:s1", 1.0, 1.0)],
        ttl_s=10.0,
        now=100.0,
    )
    assert first is not None
    blocked = manager.acquire(
        session_id="s2",
        opportunity_id="o2",
        resources=[("inventory:binance:USDT", 3.0, 10.0), ("execution-slot:s2", 1.0, 1.0)],
        ttl_s=10.0,
        now=101.0,
    )
    assert blocked is None
    assert len(manager.active(now=101.0)) == 2
    journal.close()


def test_reservation_release_and_expiry_reopen_capacity(tmp_path):
    journal = TradeJournal(tmp_path / "journal.sqlite3")
    manager = journal.reservations

    first = manager.acquire(
        session_id="s1", opportunity_id="o1",
        resources=[("inventory:bybit:BTC", 1.0, 1.0)],
        ttl_s=5.0, now=100.0,
    )
    assert first is not None
    assert manager.release(first.reservation_id, now=101.0)

    second = manager.acquire(
        session_id="s2", opportunity_id="o2",
        resources=[("inventory:bybit:BTC", 1.0, 1.0)],
        ttl_s=5.0, now=102.0,
    )
    assert second is not None
    assert manager.acquire(
        session_id="s3", opportunity_id="o3",
        resources=[("inventory:bybit:BTC", 1.0, 1.0)],
        ttl_s=5.0, now=103.0,
    ) is None

    manager.recover_expired(now=108.0)
    third = manager.acquire(
        session_id="s3", opportunity_id="o3",
        resources=[("inventory:bybit:BTC", 1.0, 1.0)],
        ttl_s=5.0, now=109.0,
    )
    assert third is not None
    journal.close()
