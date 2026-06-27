import type {
  ModelInfo, Pipeline, Run, SavedCompany, Stage, ValidatorReport,
} from "./types";

// Empty in dev (Vite proxies /api → :8000). On Vercel set VITE_API_BASE to the
// hosted backend origin, e.g. https://valu-pipeline.fly.dev
export const API_BASE = import.meta.env.VITE_API_BASE ?? "";

const TOKEN_KEY = "app_token";
export const getToken = () => localStorage.getItem(TOKEN_KEY) || "";
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t);

export function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function req(path: string, init: RequestInit = {}): Promise<Response> {
  const r = await fetch(API_BASE + path, {
    ...init,
    headers: { ...authHeaders(), ...(init.headers || {}) },
  });
  return r;
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}

const jsonHeaders = { "Content-Type": "application/json" };

export const api = {
  health: () =>
    req("/api/health").then((r) => j<{ ok: boolean; auth: boolean }>(r)),

  pipelines: () => req("/api/pipelines").then((r) => j<Pipeline[]>(r)),
  pipeline: (id: string) => req(`/api/pipelines/${id}`).then((r) => j<Pipeline>(r)),
  reseedDefaults: () =>
    req("/api/reseed", { method: "POST" }).then((r) =>
      j<{ ok: boolean; created: number; updated: number; pipeline: Pipeline }>(r)
    ),

  updateStage: (sid: string, s: Stage) =>
    req(`/api/stages/${sid}`, {
      method: "PUT",
      headers: jsonHeaders,
      body: JSON.stringify(s),
    }).then((r) => j<Stage>(r)),

  addStage: (pid: string, s: Partial<Stage>) =>
    req(`/api/pipelines/${pid}/stages`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(s),
    }).then((r) => j<Stage>(r)),

  deleteStage: (sid: string) =>
    req(`/api/stages/${sid}`, { method: "DELETE" }).then((r) => j(r)),

  reorder: (pid: string, stage_ids: string[]) =>
    req(`/api/pipelines/${pid}/reorder`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ stage_ids }),
    }).then((r) => j<Pipeline>(r)),

  models: () => req("/api/models").then((r) => j<ModelInfo[]>(r)),
  refreshModels: () =>
    req("/api/models/refresh", { method: "POST" }).then((r) => j<ModelInfo[]>(r)),

  sampleInputData: () => req("/api/sample-input-data").then((r) => j<any>(r)),

  fetchCompany: (identifier: string, params: any = {}) =>
    req("/api/fetch-company", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ identifier, params }),
    }).then((r) => j<any>(r)),

  validate: (validator_code: string, output: any, context: any) =>
    req("/api/validate", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ validator_code, output, context }),
    }).then((r) => j<ValidatorReport>(r)),

  startRun: (body: {
    pipeline_id: string;
    input_data?: any;
    identifier?: string;
    stop_on_failure: boolean;
  }) =>
    req("/api/runs", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(body),
    }).then((r) => j<{ run_id: string }>(r)),

  // Kick off the run server-side (background task, decoupled from the client).
  startRunBg: (rid: string, opts: { from_order?: number; only?: number } = {}) => {
    const q = new URLSearchParams();
    if (opts.from_order != null) q.set("from_order", String(opts.from_order));
    if (opts.only != null) q.set("only", String(opts.only));
    const qs = q.toString();
    return req(`/api/runs/${rid}/start${qs ? "?" + qs : ""}`, {
      method: "POST",
    }).then((r) => j<{ ok: boolean; started: boolean }>(r));
  },

  runs: () => req("/api/runs").then((r) => j<any[]>(r)),
  run: (id: string) => req(`/api/runs/${id}`).then((r) => j<Run>(r)),
  deleteRun: (id: string) =>
    req(`/api/runs/${id}`, { method: "DELETE" }).then((r) => j<{ ok: boolean }>(r)),

  // Saved companies: remembered name + FID + last fetched data, for instant reuse.
  companies: () => req("/api/companies").then((r) => j<SavedCompany[]>(r)),
  company: (fid: number) =>
    req(`/api/companies/${fid}`).then((r) => j<SavedCompany & { input_data: any }>(r)),
  deleteCompany: (fid: number) =>
    req(`/api/companies/${fid}`, { method: "DELETE" }).then((r) => j(r)),

  compare: (rid: string, order: number, models: string[]) =>
    req(`/api/runs/${rid}/stages/${order}/compare`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ models }),
    }).then((r) => j<{ results: any[] }>(r)),

  costs: () =>
    req("/api/costs").then((r) =>
      j<{ grand_total_usd: number; by_model: any[]; runs: any[] }>(r)
    ),
  reportCapabilities: () =>
    req("/api/report-capabilities").then((r) =>
      j<{ generator: boolean; pdf: boolean }>(r)
    ),

  valuatumConfig: () =>
    req("/api/valuatum/config").then((r) =>
      j<{ token: boolean; profinder: boolean; kit: boolean }>(r)
    ),

  runReadiness: (rid: string) =>
    req(`/api/runs/${rid}/readiness`).then((r) =>
      j<{ ready: boolean; issues: string[] }>(r)
    ),

  // report files need auth header too → fetch as blob, return object URL.
  // The backend blocks delivering an unhealthy run with 409 + an issue list;
  // pass force to override after the operator has reviewed.
  reportUrl: async (rid: string, format: "html" | "pdf", force = false) => {
    const r = await req(`/api/runs/${rid}/report.${format}${force ? "?force=1" : ""}`);
    if (!r.ok) {
      const text = await r.text();
      const err: any = new Error(text);
      err.status = r.status;
      try {
        const body = JSON.parse(text);
        if (body?.detail?.issues) err.issues = body.detail.issues as string[];
      } catch {
        /* not JSON */
      }
      throw err;
    }
    return URL.createObjectURL(await r.blob());
  },
};

// SSE over fetch streaming (so we can send the auth header; EventSource can't).
export async function streamRun(
  path: string,
  method: "GET" | "POST",
  onEvent: (e: any) => void,
  body?: any
): Promise<void> {
  const resp = await req(path, {
    method,
    headers: body ? jsonHeaders : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) throw new Error((await resp.text()) || resp.statusText);
  if (!resp.body) throw new Error("no stream body");
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    // SSE frames are separated by a blank line. sse-starlette emits CRLF
    // (\r\n\r\n); the spec also allows \n\n and \r\r. Split on all of them —
    // splitting on "\n\n" alone never matches \r\n\r\n, which silently drops
    // every event and hangs the caller (the "stuck on Fetching…" bug).
    const parts = buf.split(/\r\n\r\n|\n\n|\r\r/);
    buf = parts.pop() || "";
    for (const part of parts) {
      const line = part
        .split(/\r\n|\n|\r/)
        .find((l) => l.startsWith("data:"));
      if (line) {
        try {
          onEvent(JSON.parse(line.slice(5).trim()));
        } catch {
          /* ignore keep-alive */
        }
      }
    }
  }
}
