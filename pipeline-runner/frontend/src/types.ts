export const DATA_FETCHER_MODEL = "__data_fetcher__";

export interface Stage {
  id: string;
  pipeline_id: string;
  order: number;
  name: string;
  enabled: boolean;
  model: string;
  prompt_template: string;
  temperature: number;
  max_tokens: number;
  reasoning_effort: string | null;
  expects_json: boolean;
  web_search: boolean;
  validator_code: string | null;
  input_mapping: Record<string, string>;
}

export interface Pipeline {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  stages: Stage[];
}

export interface ValidatorCheck {
  name: string;
  passed: boolean;
  detail: string;
}
export interface ValidatorReport {
  passed: boolean;
  checks: ValidatorCheck[];
}

export type StageStatus =
  | "pending"
  | "running"
  | "ok"
  | "validation_failed"
  | "error"
  | "skipped";

export interface StageResult {
  stage_id: string;
  run_id: string;
  order: number;
  name: string;
  model: string | null;
  status: StageStatus;
  request_payload: any;
  raw_response: string | null;
  parsed_json: any;
  validator_passed: boolean | null;
  validator_report: ValidatorReport | null;
  tokens_prompt: number;
  tokens_completion: number;
  cost_usd: number;
  latency_ms: number;
  finish_reason: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Run {
  id: string;
  pipeline_id: string;
  input_data: any;
  status: string;
  stop_on_failure: boolean;
  total_cost_usd: number;
  created_at: string;
  results: StageResult[];
}

export interface ModelInfo {
  id: string;
  name: string;
  context_length: number | null;
  prompt_price: number;
  completion_price: number;
}
