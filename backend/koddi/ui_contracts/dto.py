from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


PermissionProfile = Literal["read-only", "ask", "trusted", "full"]
AgentMode = Literal["chat", "plan", "build", "debug", "review", "goal", "safety"]


class AppSettingsDto(BaseModel):
    theme: Literal["dark", "light", "system"] = "dark"
    locale: Literal["ru", "en"] = "ru"
    permission_profile: PermissionProfile = "trusted"
    selected_model: str = ""
    context_budget: Literal["small", "medium", "large", "maximum"] = "medium"
    left_panel_open: bool = True
    right_panel_open: bool = True


class ProjectDto(BaseModel):
    id: int
    name: str
    path: str
    pinned: bool = False
    git_branch: str | None = None
    dirty_files: int = 0
    active_tasks: int = 0


class AttachmentDto(BaseModel):
    id: int
    original_name: str
    media_type: str
    size_bytes: int
    sha256: str
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolDescriptor(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    result_schema: dict[str, Any]
    permissions: list[str]
    risk: Literal["low", "medium", "high", "critical"] = "low"
    timeout_seconds: int = 30
    ui_display: str = "event"


class TaskDto(BaseModel):
    id: int
    title: str
    mode: AgentMode = "build"
    role: str = "parent"
    status: str = "queued"
    progress: dict[str, Any] = Field(default_factory=dict)
    checklist: list[dict[str, Any]] = Field(default_factory=list)


class ModelDto(BaseModel):
    source: Literal["remote"] = "remote"
    name: str
    tag: str | None = None
    context_tokens: int = 32768
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UiEvent(BaseModel):
    event: str
    data: dict[str, Any] = Field(default_factory=dict)
