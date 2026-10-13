from pydantic import ValidationError
import pytest
from agent_service.permissions import can_review, require_role
from agent_service.schemas import Actor, DefinitionDraft, EngagementCreate

def test_engagement_schema_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        EngagementCreate(title="Add docs", objective="Document supported development workflow", extra="no")

def test_engagement_schema_bounds_and_strips_acceptance_criteria():
    item = EngagementCreate(title="Add docs", objective="Document supported development workflow", acceptance_criteria=["  Docs reviewed  "])
    assert item.acceptance_criteria == ["Docs reviewed"]
    with pytest.raises(ValidationError):
        EngagementCreate(title="x", objective="short")

def test_definition_revision_is_required_and_nonnegative():
    with pytest.raises(ValidationError):
        DefinitionDraft(name="Agent", description="Helpful engineering agent", instructions="Follow the documented safe workflow", expected_revision=-1)

def test_role_checks_fail_closed():
    with pytest.raises(PermissionError):
        require_role(None, {"contributor"})
    with pytest.raises(PermissionError):
        require_role(Actor(user_id="u1", role="viewer"), {"contributor"})
    require_role(Actor(user_id="u1", role="contributor"), {"contributor"})

def test_reviewer_must_be_distinct_from_proposer():
    reviewer = Actor(user_id="u2", role="reviewer")
    assert can_review(reviewer, "u1") is True
    assert can_review(reviewer, "u2") is False
    assert can_review(Actor(user_id="u1", role="contributor"), "u2") is False
