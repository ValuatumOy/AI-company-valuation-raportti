"""Pydantic v2 request/response schemas."""
from typing import Any, Optional

from pydantic import BaseModel, Field

DATA_FETCHER_MODEL = "__data_fetcher__"


class StageIn(BaseModel):
    name: str
    order: int
    enabled: bool = True
    model: str
    prompt_template: str = ""
    temperature: float = 0.2
    max_tokens: int = 16000
    reasoning_effort: Optional[str] = None
    expects_json: bool = True
    web_search: bool = False
    validator_code: Optional[str] = None
    input_mapping: dict[str, Any] = Field(default_factory=dict)


class StageOut(StageIn):
    id: str
    pipeline_id: str


class PipelineIn(BaseModel):
    name: str


class PipelineOut(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str
    stages: list[StageOut] = []


class ReorderIn(BaseModel):
    # ordered list of stage ids → new 1..N order (stage 0 fetcher keeps order 0)
    stage_ids: list[str]


class RunIn(BaseModel):
    pipeline_id: str
    input_data: Optional[dict[str, Any]] = None
    identifier: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    stop_on_failure: bool = True


class AccessKeyIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    # 0 = unlimited (e.g. an internal/CEO key); >0 = capped credits.
    generations_limit: int = Field(default=3, ge=0, le=100000)
    expires_at: Optional[str] = None


class ExpertGenerateIn(BaseModel):
    fid: int
    company_name: str = Field(min_length=1, max_length=300)
    company_code: Optional[str] = None
    pipeline_id: Optional[str] = None
    user_input: str = Field(default="", max_length=4000)


class ClarificationAnswer(BaseModel):
    id: str = ""
    question: str = ""
    answer: str = Field(default="", max_length=4000)


class Round2In(BaseModel):
    clarifications: list[ClarificationAnswer] = Field(default_factory=list)
    clarifications_free_text: str = Field(default="", max_length=8000)


class FetchIn(BaseModel):
    identifier: str
    params: dict[str, Any] = Field(default_factory=dict)


class OrderIn(BaseModel):
    # Public website order intake. Lengths capped: this endpoint is unauthenticated.
    company: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=5, max_length=200, pattern=r".+@.+\..+")
    user_input: str = Field(default="", max_length=4000)
    website: str = ""  # honeypot — humans leave it empty


class OrderStatusIn(BaseModel):
    status: str = Field(pattern=r"^(open|in_progress|delivered|spam)$")


class ValidateIn(BaseModel):
    validator_code: str
    output: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)


class CompareIn(BaseModel):
    models: list[str]


class ValuatumExportIn(BaseModel):
    company_name: str
    fid: int
    actuals: int = 15  # default to a long history; export clamps to what exists
    estimates: int = 10
    company_code_override: Optional[str] = None
