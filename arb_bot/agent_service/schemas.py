"""Strict request schemas for agent descriptions and project engagements."""
from __future__ import annotations
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class EngagementCreate(StrictModel):
    title: str = Field(min_length=3, max_length=160)
    objective: str = Field(min_length=10, max_length=4000)
    category: Literal["development", "documentation", "research", "collaboration", "review"] = "development"
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    owner_id: UUID | None = None
    due_at: datetime | None = None
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("acceptance_criteria")
    @classmethod
    def criteria_are_bounded(cls, values):
        if any(not item.strip() or len(item) > 500 for item in values):
            raise ValueError("Each acceptance criterion must contain 1–500 characters")
        return [item.strip() for item in values]

class EngagementPatch(StrictModel):
    title: str | None = Field(default=None, min_length=3, max_length=160)
    objective: str | None = Field(default=None, min_length=10, max_length=4000)
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    status: Literal["open", "in_progress", "blocked", "completed", "cancelled"] | None = None
    owner_id: UUID | None = None
    due_at: datetime | None = None
    acceptance_criteria: list[str] | None = Field(default=None, max_length=30)

class DefinitionDraft(StrictModel):
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(min_length=10, max_length=2000)
    instructions: str = Field(min_length=20, max_length=12000)
    capabilities: list[str] = Field(default_factory=list, max_length=30)
    action: Literal["save_draft", "request_review"] = "save_draft"
    expected_revision: int = Field(ge=0)

class ApprovalDecision(StrictModel):
    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=2000)

class Actor(BaseModel):
    user_id: str
    role: Literal["viewer", "contributor", "project_manager", "reviewer", "admin"]

class ToolResult(BaseModel):
    ok: bool
    data: dict | list | None = None
    error: dict | None = None
    request_id: str
    audit_event_id: str | None = None
