"""Pydantic 请求/响应模型。"""
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class PlanRequest(BaseModel):
    goal_id: int
    date: str
    hours_left: float


class ToolCallItem(BaseModel):
    tool: str
    input: Any = None
    output: str
    agent: str = ""
    via: str = "local"


class PlanResponse(BaseModel):
    day_id: int
    hours_left: float
    assignments: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    plan: list[dict]
    life: list[dict]
    trimmed_plan: list[dict]
    cut: list[dict]
    tool_calls: list[ToolCallItem]


class TasksPatchRequest(BaseModel):
    task_ids: list[int]
    done: bool = True


class TasksPatchResponse(BaseModel):
    updated: list[int]


class ReviewRequest(BaseModel):
    day_id: int
    interview_notes: Optional[str] = Field(
        default="",
        description="覆盖备注（可选）；空则用 interviews.json 的 post_notes",
    )


class ReviewResponse(BaseModel):
    review: str
    tomorrow: str
    interview_review: str = ""
    logs: list[dict]


class GoalCreateRequest(BaseModel):
    title: str
    description: str = ""


class GoalCreateResponse(BaseModel):
    id: int
    title: str
    status: str


class InterviewCreateRequest(BaseModel):
    date: str
    time: str
    company: str
    role: str = ""
    duration_hours: float = 1.0
    notes: str = ""
    post_notes: str = ""
    stage: str = "一面"
    insights: str = ""


class InterviewPatchRequest(BaseModel):
    """按 date+time+company 定位并更新（尤其 stage / insights）。"""
    date: str
    time: str
    company: str
    stage: Optional[str] = None
    insights: Optional[str] = None
    role: Optional[str] = None
    notes: Optional[str] = None
    post_notes: Optional[str] = None
    duration_hours: Optional[float] = None


class AnalyzeRequest(BaseModel):
    goal_id: int


class AnalyzeResponse(BaseModel):
    stats: dict[str, Any] = Field(default_factory=dict)
    analysis: str = ""
    suggestions: str = ""
    summary: str = ""
    assignments: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[ToolCallItem] = Field(default_factory=list)


class AskRequest(BaseModel):
    query: str


class AskResponse(BaseModel):
    answer: str = ""
    mode: str = "semantic"
    chunks: list[dict] = Field(default_factory=list)
    tool_calls: list[ToolCallItem] = Field(default_factory=list)


class ContextCreateRequest(BaseModel):
    type: Literal["meeting", "block", "note"]
    time: Optional[str] = None
    title: Optional[str] = None
    duration_hours: Optional[float] = None
    note: Optional[str] = None
    text: Optional[str] = None

    @model_validator(mode="after")
    def validate_by_type(self) -> "ContextCreateRequest":
        if self.type == "meeting":
            if not self.time or not self.title:
                raise ValueError("meeting 需要 time 与 title")
            if self.duration_hours is None:
                self.duration_hours = 1.0
        elif self.type == "block":
            if not self.time or not self.note:
                raise ValueError("block 需要 time 与 note")
        elif self.type == "note":
            if not (self.text and str(self.text).strip()):
                raise ValueError("note 需要非空 text")
        return self
