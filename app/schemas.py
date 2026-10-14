from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Output):
    segment_id: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=2000)


class Decision(Output):
    text: str = Field(min_length=1, max_length=1000)
    evidence: Evidence


class SummaryOutput(Output):
    summary: str = Field(min_length=1, max_length=3000)
    decisions: list[Decision] = Field(default_factory=list, max_length=50)
    keywords: list[str] = Field(default_factory=list, max_length=20)


class TaskEvent(Output):
    task_id: str = Field(min_length=1, max_length=80)
    kind: Literal["create", "assign", "reschedule", "cancel"]
    task: str = Field(min_length=1, max_length=1000)
    owner: str | None = Field(default=None, max_length=80)
    due: str | None = Field(default=None, max_length=100)
    evidence: Evidence


class ActionOutput(Output):
    events: list[TaskEvent] = Field(default_factory=list, max_length=200)


class ReviewOutput(Output):
    approved: bool
    issues: list[str] = Field(default_factory=list, max_length=100)


class AnswerOutput(Output):
    answer: str = Field(min_length=1, max_length=3000)
    citations: list[str] = Field(default_factory=list, max_length=20)
