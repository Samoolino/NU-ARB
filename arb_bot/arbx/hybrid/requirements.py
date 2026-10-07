from __future__ import annotations

from dataclasses import dataclass

from .contracts import ExecutionRequirements, VenueCapabilities


@dataclass(frozen=True, slots=True)
class RequirementResult:
    ok: bool
    reasons: tuple[str, ...] = ()


def validate_capabilities(
    capabilities: VenueCapabilities,
    requirements: ExecutionRequirements,
) -> RequirementResult:
    reasons: list[str] = []
    if requirements.market_type == "spot" and not capabilities.spot:
        reasons.append("spot_unavailable")
    if requirements.order_type == "limit" and not capabilities.limit_orders:
        reasons.append("limit_orders_unavailable")
    if requirements.order_type == "market" and not capabilities.market_orders:
        reasons.append("market_orders_unavailable")
    if requirements.require_market_order and not capabilities.market_orders:
        reasons.append("market_orders_required")
    if requirements.require_websocket and not capabilities.websocket:
        reasons.append("websocket_required")
    if requirements.require_private_stream and not capabilities.user_stream:
        reasons.append("private_stream_required")
    if requirements.require_ioc and not capabilities.ioc:
        reasons.append("ioc_required")
    if requirements.require_post_only and not capabilities.post_only:
        reasons.append("post_only_required")
    return RequirementResult(not reasons, tuple(reasons))
