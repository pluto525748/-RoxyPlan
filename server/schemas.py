from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, constr


MessageText = constr(strip_whitespace=True, min_length=1, max_length=4000)
ShortText = constr(strip_whitespace=True, min_length=1, max_length=240)
SessionTitle = constr(strip_whitespace=True, min_length=1, max_length=60)
ConfirmationId = constr(strip_whitespace=True, min_length=1, max_length=64)
ConversationId = constr(
    strip_whitespace=True,
    min_length=1,
    max_length=128,
    regex=r"^[A-Za-z0-9_.:-]+$",
)


class AgentRequestSchema(BaseModel):
    message: MessageText
    conversation_id: ConversationId = "local_default"

    class Config:
        extra = "forbid"


class AgentResponseSchema(BaseModel):
    schema_version: str = "1.0"
    request_id: str
    conversation_id: Optional[str] = None
    status: str
    message: str
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    tool_results: List[Dict[str, Any]] = Field(default_factory=list)
    pending_confirmation: Optional[Dict[str, Any]] = None
    client_actions: List[Dict[str, Any]] = Field(default_factory=list)

    class Config:
        extra = "ignore"


class HealthResponseSchema(BaseModel):
    status: str
    service: str


class PlanCreateSchema(BaseModel):
    title: ShortText

    class Config:
        extra = "forbid"


class ActionCreateSchema(BaseModel):
    content: ShortText

    class Config:
        extra = "forbid"


class ConversationCreateSchema(BaseModel):
    title: SessionTitle = "新对话"

    class Config:
        extra = "forbid"


class ConversationRenameSchema(BaseModel):
    title: SessionTitle

    class Config:
        extra = "forbid"


class CandidateDecisionSchema(BaseModel):
    confirmed: bool = False

    class Config:
        extra = "forbid"


class CandidateEditedAcceptSchema(BaseModel):
    content: MessageText
    confirmed: bool = False

    class Config:
        extra = "forbid"


class ConflictResolutionSchema(BaseModel):
    resolution: Literal["keep_old", "use_new", "merge", "keep_both", "defer"]
    merged_content: Optional[MessageText] = None
    confirmed: bool = False

    class Config:
        extra = "forbid"


class ToolResultSchema(BaseModel):
    schema_version: str = "1.0"
    tool_call_id: str
    success: bool
    status: str
    tool: str
    message: str
    data: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[Dict[str, Any]] = None

    class Config:
        extra = "ignore"


class ServiceStatusSchema(BaseModel):
    service: str
    ollama: str
    model: str
    knowledge_files: int


class ConversationSummarySchema(BaseModel):
    session_id: str
    title: str
    started_at: str
    updated_at: str
    message_count: int


class ConversationDetailSchema(ConversationSummarySchema):
    summary: str = ""
    messages: List[Dict[str, Any]] = Field(default_factory=list)
