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


class FetchIn(BaseModel):
    identifier: str
    params: dict[str, Any] = Field(default_factory=dict)


class ValidateIn(BaseModel):
    validator_code: str
    output: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)


class CompareIn(BaseModel):
    models: list[str]


class ValuatumExportIn(BaseModel):
    company_name: str
    fid: int
    actuals: int = 5
    estimates: int = 10
    company_code_override: Optional[str] = None
