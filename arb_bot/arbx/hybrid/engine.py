from __future__ import annotations

import os
from dataclasses import dataclass

from .adapters import (
    CCXTAdapter,
    CCXTProAdapter,
    NativeSDKAdapter,
    AdapterError,
    safe_ccxt_error,
)
from .contracts import ExecutionRequirements, VenueCapabilities, VenueEvidence
from .depth import validate_depth
from .registry import VENUE_CATALOG
from .requirements import validate_capabilities
from .router import RouteDecision, validate_route
from .strategies import strategy_catalog
from arbx.journal import TradeJournal


@dataclass
class HybridEngine:
    cfg: object
    adapters: dict
    evidence: dict
    capabilities: dict

    @classmethod
    def create(cls, cfg):
        """
        Build the canonical venue adapter set.

        Behavior:
        - By default, preserve the configured venue scope.
        - BOT_HYBRID_ALL_VENUES=1 expands the scope to every venue in
          VENUE_CATALOG.
        - Public market-data adapters do not require credentials.
        - Credentials are attached only when present.
        - CCXT Pro is preferred by the registry unless explicitly overridden.
        - Native SDK selection remains fail-closed because NativeSDKAdapter
          requires a concrete certified implementation.
        """
        adapters: dict = {}

        configured = {
            (getattr(x, "venue_id", None) or getattr(x, "id", None)): x
            for x in getattr(cfg, "exchanges", [])
        }

        all_venues = os.getenv(
            "BOT_HYBRID_ALL_VENUES",
            "0",
        ).strip().lower() in {"1", "true", "yes", "on"}

        for spec in VENUE_CATALOG:
            match = configured.get(spec.id)

            # In normal mode preserve the configured exchange scope.
            # In all-venue mode create public-data adapters for the complete
            # canonical venue catalog.
            if match is None and not all_venues:
                continue

            credentials = {}
            auth_mode = getattr(match, "auth_mode", "hmac") if match else "hmac"

            # First preference: credentials already loaded by Config.
            if match is not None:
                api_key = getattr(match, "api_key", None)
                secret = getattr(match, "secret", None)
                private_key = getattr(match, "private_key", None)
                password = getattr(match, "password", None)

                if api_key:
                    credentials["apiKey"] = api_key

                if auth_mode == "hmac" and secret:
                    credentials["secret"] = secret
                elif auth_mode in ("rsa", "ed25519") and private_key:
                    credentials["privateKey"] = private_key

                if password:
                    credentials["password"] = password

            # Second preference: environment fallback. Never print values.
            prefix = spec.id.upper().replace("-", "_")

            if "apiKey" not in credentials:
                env_api_key = (
                    os.getenv(f"{prefix}_API_KEY")
                    or os.getenv(f"BOT_{prefix}_API_KEY")
                )
                if env_api_key:
                    credentials["apiKey"] = env_api_key

            if "secret" not in credentials and "privateKey" not in credentials:
                if auth_mode == "hmac":
                    env_secret = (
                        os.getenv(f"{prefix}_API_SECRET")
                        or os.getenv(f"BOT_{prefix}_API_SECRET")
                    )
                    if env_secret:
                        credentials["secret"] = env_secret
                elif auth_mode in ("rsa", "ed25519"):
                    env_private_key = (
                        os.getenv(f"{prefix}_PRIVATE_KEY")
                        or os.getenv(f"BOT_{prefix}_PRIVATE_KEY")
                    )
                    if env_private_key:
                        credentials["privateKey"] = env_private_key

            if "password" not in credentials:
                env_password = (
                    os.getenv(f"{prefix}_PASSWORD")
                    or os.getenv(f"{prefix}_PASSPHRASE")
                    or os.getenv(f"BOT_{prefix}_PASSWORD")
                    or os.getenv(f"BOT_{prefix}_PASSPHRASE")
                )
                if env_password:
                    credentials["password"] = env_password

            selector = (
                os.getenv(f"BOT_{spec.id.upper()}_ADAPTER")
                or os.getenv("BOT_ADAPTER")
                or spec.adapter
            ).lower()

            if selector == "native":
                adapter = NativeSDKAdapter(spec.id, credentials)
            elif selector == "ccxt":
                adapter = CCXTAdapter(
                    spec.id,
                    spec.ccxt_id,
                    credentials,
                    auth_mode=auth_mode,
                )
            else:
                adapter = CCXTProAdapter(
                    spec.id,
                    spec.ccxt_id,
                    credentials,
                    auth_mode=auth_mode,
                )

            adapters[spec.id] = adapter

        return cls(cfg, adapters, {}, {})

    async def validate_venue(self, venue_id, symbol="BTC/USDT", notional_usd=None):
        a = self.adapters[venue_id]
        reasons: list[str] = []
        rest = public = private = balance = perm = execution = depth = False
        detail = {"venue": venue_id, "adapter": a.adapter_name, "symbol": symbol}

        def safe_error(exc):
            return safe_ccxt_error(
                exc,
                getattr(a, "ex", None),
                getattr(a, "credentials", None),
            )

        try:
            await a.connect()

            try:
                detail["rest"] = await a.verify_rest()
                rest = bool(detail["rest"].get("ok"))
            except Exception as exc:
                detail["rest"] = {"ok": False, "error": safe_error(exc)}

            try:
                capability_obj = await a.get_capabilities()
                detail["capabilities"] = {
                    name: getattr(capability_obj, name)
                    for name in VenueCapabilities.__dataclass_fields__
                }
                caps = VenueCapabilities(**detail["capabilities"])
                self.capabilities[venue_id] = caps
            except Exception as exc:
                caps = VenueCapabilities()
                self.capabilities[venue_id] = caps
                detail["capabilities"] = {"error": safe_error(exc)}

            try:
                detail["publicWS"] = await a.verify_public_stream(symbol, max(50, self.cfg.depth))
                public = bool(detail["publicWS"].get("ok"))
            except Exception as exc:
                detail["publicWS"] = {"ok": False, "error": safe_error(exc)}

            try:
                bal = await a.get_balances()
                balance = isinstance(bal, dict) and all(isinstance(bal.get(k), dict) for k in ("free", "used", "total"))
                detail["balance"] = {"ok": balance}
            except Exception as exc:
                detail["balance"] = {"ok": False, "error": safe_error(exc)}

            if self.cfg.mode == "live":
                try:
                    detail["privateWS"] = await a.verify_private_stream()
                    private = bool(detail["privateWS"].get("ok"))
                except Exception as exc:
                    detail["privateWS"] = {"ok": False, "error": safe_error(exc)}
            else:
                detail["privateWS"] = {"ok": True, "state": "not_required_in_paper"}
                private = True

            try:
                detail["permissions"] = await a.verify_permissions()
                perm = bool(detail["permissions"].get("liveEligible"))
            except Exception as exc:
                detail["permissions"] = {"liveEligible": False, "error": safe_error(exc)}

            try:
                detail["execution"] = await a.verify_execution(symbol)
                execution = all(detail["execution"].get(k) for k in (
                    "market", "spot", "createOrder", "fetchOrder", "watchOrderBook", "iocLimit"
                ))
            except Exception as exc:
                detail["execution"] = {"ok": False, "error": safe_error(exc)}

            requirements = ExecutionRequirements(
                market_type="spot",
                order_type="limit",
                min_liquidity_usd=float(notional_usd or self.cfg.trade_size_usd),
                require_websocket=self.cfg.mode == "live",
                require_private_stream=self.cfg.mode == "live",
                require_ioc=True,
            )
            cap_result = validate_capabilities(caps, requirements)
            detail["routeRequirements"] = {
                "ok": cap_result.ok,
                "reasons": cap_result.reasons,
            }
            execution = execution and cap_result.ok

            if public:
                try:
                    book = await a.watch_depth(symbol, max(50, self.cfg.depth))
                    d = validate_depth(
                        book,
                        notional_usd or self.cfg.trade_size_usd,
                        self.cfg.max_book_age_ms,
                    )
                    depth = d.ok
                    detail["depth"] = {
                        "ok": depth,
                        "reason": d.reason,
                        "depthUsd": d.depth_usd,
                        "bestBid": d.top_bid,
                        "bestAsk": d.top_ask,
                        "spreadBps": d.spread_bps,
                        "levels": d.levels,
                    }
                except Exception as exc:
                    detail["depth"] = {"ok": False, "error": safe_error(exc)}
            else:
                detail["depth"] = {"ok": False, "reason": "public_ws_unverified"}

            checks = (
                ("rest", rest),
                ("public_ws", public),
                ("private_ws", private),
                ("balance", balance),
                ("permissions", perm),
                ("execution", execution),
                ("depth", depth),
            )
            reasons.extend(name for name, ok in checks if not ok)
            reasons.extend(cap_result.reasons)

            live = all(ok for _, ok in checks) if self.cfg.mode == "live" else all(
                ok for name, ok in checks if name not in ("private_ws", "permissions")
            )
            ev = VenueEvidence(
                venue_id,
                a.adapter_name,
                rest,
                public,
                private,
                balance,
                perm,
                execution,
                depth,
                live,
                tuple(dict.fromkeys(reasons)),
                detail,
            )
        except Exception as exc:
            ev = VenueEvidence(
                venue_id, a.adapter_name, False, False, False, False,
                False, False, False, False,
                (safe_error(exc),), detail,
            )

        self.evidence[venue_id] = ev
        journal = TradeJournal(self.cfg.journal_path.with_suffix(".sqlite3"))
        try:
            journal.record_certification(
                session_id=getattr(self.cfg, "session_id", None),
                venue=venue_id,
                symbol=symbol,
                notional_usd=float(notional_usd or self.cfg.trade_size_usd),
                live_eligible=ev.live_eligible,
                reasons=ev.reasons,
                evidence=ev.evidence,
            )
        finally:
            journal.close()
        return ev

    def validate_route(
        self,
        venues: tuple[str, ...],
        requirements: ExecutionRequirements,
    ) -> RouteDecision:
        return validate_route(self.capabilities, venues, requirements)

    def eligible_venues(self):
        return [venue for venue, evidence in self.evidence.items() if evidence.live_eligible]

    def strategy_catalog(self):
        return strategy_catalog()
