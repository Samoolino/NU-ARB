from __future__ import annotations

from dataclasses import dataclass

from .contracts import ExecutionRequirements, VenueCapabilities
from .requirements import validate_capabilities


@dataclass(frozen=True, slots=True)
class RouteDecision:
    ok: bool
    route: tuple[str, ...]
    reasons: tuple[str, ...] = ()


def validate_route(
    venue_capabilities: dict[str, VenueCapabilities],
    venues: tuple[str, ...],
    requirements: ExecutionRequirements,
) -> RouteDecision:
    reasons: list[str] = []
    for venue in venues:
        caps = venue_capabilities.get(venue)
        if caps is None:
            reasons.append(f"{venue}:capabilities_unavailable")
            continue
        result = validate_capabilities(caps, requirements)
        reasons.extend(f"{venue}:{reason}" for reason in result.reasons)
    return RouteDecision(not reasons, venues, tuple(reasons))
