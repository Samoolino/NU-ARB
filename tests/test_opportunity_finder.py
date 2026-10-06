from __future__ import annotations

import json
import time

from arbx.hybrid.opportunity_finder import (
    WebhookLatencyRegistry,
    monte_carlo,
)


def test_monte_carlo_is_deterministic_and_exposes_tail():
    a = monte_carlo(35, 3, 8, 10, 2, trials=400, seed=7, notional_usd=3)
    b = monte_carlo(35, 3, 8, 10, 2, trials=400, seed=7, notional_usd=3)
    assert a.expected_net_usd == b.expected_net_usd
    assert 0 <= a.probability_positive <= 1
    assert a.p05_net_usd <= a.p50_net_usd <= a.p95_net_usd


def test_webhook_latency_registry_rejects_bad_signature():
    registry = WebhookLatencyRegistry({"feed": "secret"})
    body = json.dumps({"event_ts_ms": int(time.time() * 1000), "sequence": 1}).encode()
    try:
        registry.ingest("feed", json.loads(body), signature="bad", raw_body=body)
    except ValueError as exc:
        assert str(exc) == "webhook_signature_invalid"
    else:
        raise AssertionError("invalid webhook signature was accepted")


def test_webhook_latency_registry_accepts_signed_fresh_event():
    registry = WebhookLatencyRegistry({"feed": "secret"})
    body = json.dumps({"event_ts_ms": int(time.time() * 1000), "sequence": 2}).encode()
    import hashlib, hmac
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    obs = registry.ingest("feed", json.loads(body), signature=signature, raw_body=body)
    assert obs.valid_signature is True
    assert obs.fresh is True
