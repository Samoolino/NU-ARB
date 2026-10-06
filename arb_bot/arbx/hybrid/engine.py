from __future__ import annotations

import os
from dataclasses import dataclass

from .adapters import (
    CCXTAdapter,
    CCXTProAdapter,
    NativeSDKAdapter,
    AdapterError,
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
        adapters: dict = {}
        for spec in VENUE_CATALOG:
            match = next((x for x in cfg.exchanges if (x.venue_id or x.id) == spec.id), None)
            if not match:
                continue

            credentials = {}
            if match.api_key:
                credentials["apiKey"] = match.api_key
            if match.secret:
                credentials["secret" if match.auth_mode == "hmac" else "privateKey"] = match.secret
            if match.password:
                credentials["password"] = match.password

            selector = (
                os.getenv(f"BOT_{spec.id.upper()}_ADAPTER")
                or os.getenv("BOT_ADAPTER")
                or spec.adapter
            ).lower()

            if selector == "native":
                adapter = NativeSDKAdapter(spec.id, credentials)
            elif selector == "ccxt":
                adapter = CCXTAdapter(spec.id, spec.ccxt_id, credentials)
            else:
                adapter = CCXTProAdapter(spec.id, spec.ccxt_id, credentials)

            adapters[spec.id] = adapter

        return cls(cfg, adapters, {}, {})

    async def validate_venue(self, venue_id, symbol="BTC/USDT", notional_usd=None):
        a = self.adapters[venue_id]
        reasons: list[str] = []
        rest = public = private = balance = perm = execution = depth = False
        detail = {"venue": venue_id, "adapter": a.adapter_name, "symbol": symbol}

        try:
            await a.connect()

            try:
                detail["rest"] = await a.verify_rest()
                rest = bool(detail["rest"].get("ok"))
            except Exception as exc:
                detail["rest"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

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
                detail["capabilities"] = {"error": f"{type(exc).__name__}: {exc}"}

            try:
                detail["publicWS"] = await a.verify_public_stream(symbol, max(50, self.cfg.depth))
                public = bool(detail["publicWS"].get("ok"))
            except Exception as exc:
                detail["publicWS"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

            try:
                bal = await a.get_balances()
                balance = isinstance(bal, dict) and all(isinstance(bal.get(k), dict) for k in ("free", "used", "total"))
                detail["balance"] = {"ok": balance}
            except Exception as exc:
                detail["balance"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

            if self.cfg.mode == "live":
                try:
                    detail["privateWS"] = await a.verify_private_stream()
                    private = bool(detail["privateWS"].get("ok"))
                except Exception as exc:
                    detail["privateWS"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            else:
                detail["privateWS"] = {"ok": True, "state": "not_required_in_paper"}
                private = True

            try:
                detail["permissions"] = await a.verify_permissions()
                perm = bool(detail["permissions"].get("liveEligible"))
            except Exception as exc:
                detail["permissions"] = {"liveEligible": False, "error": f"{type(exc).__name__}: {exc}"}

            try:
                detail["execution"] = await a.verify_execution(symbol)
                execution = all(detail["execution"].get(k) for k in (
                    "market", "spot", "createOrder", "fetchOrder", "watchOrderBook", "iocLimit"
                ))
            except Exception as exc:
                detail["execution"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

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
                    detail["depth"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
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
                (type(exc).__name__, str(exc)), detail,
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
