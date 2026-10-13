"""Fail-closed permission rules for the project-management domain."""
from .schemas import Actor

READ_ROLES = {"viewer", "contributor", "project_manager", "reviewer", "admin"}
WRITE_ENGAGEMENT_ROLES = {"contributor", "project_manager", "admin"}
MANAGE_PROJECT_ROLES = {"project_manager", "admin"}
REVIEW_ROLES = {"reviewer", "admin"}

def require_role(actor: Actor | None, allowed: set[str]) -> None:
    if actor is None or actor.role not in allowed:
        raise PermissionError("Insufficient permissions")

def can_review(actor: Actor | None, proposer_id: str) -> bool:
    return bool(actor and actor.role in REVIEW_ROLES and actor.user_id != proposer_id)
