import { useEffect, useMemo, useState } from "react";
import { api, getToken, setToken } from "./api";
import type { ModelInfo, Pipeline, Run, Stage, StageResult } from "./types";
import { StageList } from "./components/StageList";
import { StageEditor } from "./components/StageEditor";
import { ResultPanel } from "./components/ResultPanel";
import { CostOverlay } from "./components/CostOverlay";

const WELL_KNOWN: Record<number, string> = {
  0: "input_data",
  1: "enrichment",
  2: "profile_analysis",
  3: "sections_numeric",
  4: "scenarios",
  5: "analysis_sections",
  6: "summary",
};
const slug = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/_+/g, "_").replace(/^_|_$/g, "");

export default function App() {
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [inputData, setInputData] = useState<any>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [results, setResults] = useState<Record<number, StageResult>>({});
  const [stopOnFailure, setStopOnFailure] = useState(true);
  const [busy, setBusy] = useState(false);
  const [runStartAt, setRunStartAt] = useState<number | null>(null);
  const [nowTick, setNowTick] = useState(Date.now());
  const [totalCost, setTotalCost] = useState(0);
  const [runs, setRuns] = useState<any[]>([]);
  const [cmp, setCmp] = useState<{ order: number; results: any[] } | null>(null);
  const [showCosts, setShowCosts] = useState(false);
  const [reportCaps, setReportCaps] = useState({ generator: false, pdf: false });
  const [reportBusy, setReportBusy] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [needToken, setNeedToken] = useState(false);
  const [tokenDraft, setTokenDraft] = useState("");

  function init() {
    api.pipelines()
      .then((ps) => {
        setPipeline(ps[0]);
        setSelectedId(ps[0]?.stages[0]?.id ?? null);
      })
      .catch((e) => {
        if (String(e).includes("401") || String(e).includes("unauthorized"))
          setNeedToken(true);
      });
    api.models().then(setModels).catch(() => {});
    api.runs().then(setRuns).catch(() => {});
    api.reportCapabilities().then(setReportCaps).catch(() => {});
  }

  useEffect(() => {
    // Magic link: a shared URL with ?token=... logs the device in automatically,
    // then the token is stripped from the address bar. After the first open it
    // persists in localStorage, so the CEO just opens the link and enters a name+FID.
    try {
      const u = new URL(window.location.href);
      const t = u.searchParams.get("token");
      if (t) {
        setToken(t.trim());
        u.searchParams.delete("token");
        window.history.replaceState({}, "", u.pathname + u.search + u.hash);
      }
    } catch {
      /* no-op */
    }
    api.health()
      .then((h) => {
        if (h.auth && !getToken()) setNeedToken(true);
        else init();
      })
      .catch(() => init());
  }, []);

  // Tick a clock once a second while a run is in flight, for the elapsed timer.
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(t);
  }, [busy]);

  // Reliability poll: the SSE stream drives the run, but some browsers (notably
  // iOS Safari) buffer fetch-streamed responses, so live stage events never
  // reach the UI. Poll the run from the server every few seconds while a run is
  // in flight so progress always updates regardless of stream buffering.
  useEffect(() => {
    if (!busy || !runId) return;
    let stop = false;
    const t = setInterval(async () => {
      try {
        const run: Run = await api.run(runId);
        if (stop) return;
        const map: Record<number, StageResult> = {};
        for (const r of run.results) map[r.order] = r;
        setResults(map);
        setTotalCost(run.total_cost_usd);
        if (run.status !== "running") {
          setBusy(false);
          api.runs().then(setRuns).catch(() => {});
        }
      } catch {
        /* transient — keep polling */
      }
    }, 3000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [busy, runId]);

  function newRun() {
    setRunId(null);
    setResults({});
    setTotalCost(0);
    setRunStartAt(null);
    setInputData(null);
    // jump to stage 0
    setSelectedId(pipeline?.stages.find((s) => s.order === 0)?.id ?? null);
  }

  // Resolve a report URL, honouring the backend deliver-gate: on 409 the report
  // failed its checks — surface the issues and let the operator deliver anyway
  // only after an explicit confirm.
  async function resolveReportUrl(format: "html" | "pdf"): Promise<string | null> {
    try {
      return await api.reportUrl(runId!, format);
    } catch (e: any) {
      if (e?.status === 409 && Array.isArray(e?.issues)) {
        const ok = confirm(
          "Raportti ei läpäissyt tarkistuksia:\n\n• " +
            e.issues.join("\n• ") +
            "\n\nTarkista luvut lähteestä. Toimita silti?"
        );
        if (!ok) return null;
        return await api.reportUrl(runId!, format, true);
      }
      throw e;
    }
  }

  async function openReport(format: "html" | "pdf") {
    if (!runId) return;
    setReportBusy(true);
    try {
      const url = await resolveReportUrl(format);
      if (url) window.open(url, "_blank");
    } catch (e: any) {
      alert("Report generation failed:\n" + (e?.message || e));
    } finally {
      setReportBusy(false);
    }
  }

  async function openPreview() {
    if (!runId) return;
    setReportBusy(true);
    try {
      const url = await resolveReportUrl("html");
      if (url) setPreviewUrl(url);
    } catch (e: any) {
      alert("Preview failed:\n" + (e?.message || e));
    } finally {
      setReportBusy(false);
    }
  }

  const selected = useMemo(
    () => pipeline?.stages.find((s) => s.id === selectedId) ?? null,
    [pipeline, selectedId]
  );

  const context = useMemo(() => {
    const ctx: Record<string, any> = {};
    if (inputData != null) ctx["input_data"] = inputData;
    if (!pipeline) return ctx;
    const mergeSections = (sections: any[]) => {
      const byId: Record<string, any> = {};
      for (const sec of ctx["sections_analysis"]?.sections ?? []) {
        if (sec?.id != null) byId[String(sec.id)] = sec;
      }
      for (const sec of sections) {
        if (sec?.id != null) byId[String(sec.id)] = sec;
      }
      ctx["sections_analysis"] = { sections: Object.values(byId) };
    };
    for (const s of pipeline.stages) {
      if (selected && s.order >= selected.order) continue;
      const r = results[s.order];
      if (r && (r.status === "ok" || r.status === "validation_failed")) {
        const out = r.parsed_json ?? { raw: r.raw_response };
        ctx[WELL_KNOWN[s.order] ?? slug(s.name)] = out;
        ctx[slug(s.name)] = out;
        if (out?.growth_assessment) ctx["growth_assessment"] = out.growth_assessment;
        if (out?.scoring) ctx["scoring"] = out.scoring;
        if (s.order === 4) ctx["scenarios"] = out;
        if (Array.isArray(out?.sections)) {
          if (s.order === 3) ctx["sections_numeric"] = out;
          if ([2, 4, 5].includes(s.order)) mergeSections(out.sections);
        }
      }
    }
    return ctx;
  }, [pipeline, selected, results, inputData]);

  async function refreshRun(rid: string) {
    const run: Run = await api.run(rid);
    const map: Record<number, StageResult> = {};
    for (const r of run.results) map[r.order] = r;
    setResults(map);
    setTotalCost(run.total_cost_usd);
    if (run.input_data != null) setInputData(run.input_data);
  }

  async function runAll() {
    if (!pipeline) return;
    setBusy(true);
    setRunStartAt(Date.now());
    setNowTick(Date.now());
    setResults({});
    try {
      const { run_id } = await api.startRun({
        pipeline_id: pipeline.id,
        input_data: inputData ?? undefined,
        stop_on_failure: stopOnFailure,
      });
      setRunId(run_id);
      await api.startRunBg(run_id); // runs server-side; the poll drives the UI
      refreshRun(run_id); // immediate first paint
    } catch (e: any) {
      setBusy(false);
      alert("Run failed to start:\n" + (e?.message || e));
    }
  }

  async function rerun(order: number, from = false) {
    if (!runId) return runAll();
    setBusy(true);
    setRunStartAt(Date.now());
    setNowTick(Date.now());
    try {
      await api.startRunBg(runId, from ? { from_order: order } : { only: order });
      refreshRun(runId);
    } catch (e: any) {
      setBusy(false);
      alert("Rerun failed:\n" + (e?.message || e));
    }
  }

  async function compare(order: number, ms: string[]) {
    if (!runId || ms.length === 0) return;
    setBusy(true);
    try {
      const { results: r } = await api.compare(runId, order, ms);
      setCmp({ order, results: r });
    } finally {
      setBusy(false);
    }
  }

  async function saveStage(s: Stage) {
    const updated = await api.updateStage(s.id, s);
    setPipeline((p) =>
      p ? { ...p, stages: p.stages.map((x) => (x.id === s.id ? updated : x)) } : p
    );
  }
  async function reseedDefaults() {
    if (!pipeline) return;
    if (!confirm("Reset all stage prompts to repo defaults?")) return;
    const res = await api.reseedDefaults();
    setPipeline(res.pipeline);
    setSelectedId((id) =>
      id && res.pipeline.stages.some((s) => s.id === id)
        ? id
        : res.pipeline.stages[0]?.id ?? null
    );
  }
  async function toggleStage(id: string, enabled: boolean) {
    const s = pipeline!.stages.find((x) => x.id === id)!;
    saveStage({ ...s, enabled });
  }
  async function addStage() {
    if (!pipeline) return;
    const order = Math.max(...pipeline.stages.map((s) => s.order)) + 1;
    const s = await api.addStage(pipeline.id, {
      name: `Stage ${order} – new`,
      order,
      enabled: true,
      model: "google/gemini-2.5-flash",
      prompt_template: "",
      temperature: 0.2,
      max_tokens: 16000,
      reasoning_effort: null,
      web_search: false,
      expects_json: true,
      validator_code: null,
      input_mapping: {},
    });
    const p = await api.pipeline(pipeline.id);
    setPipeline(p);
    setSelectedId(s.id);
  }
  async function deleteStage(id: string) {
    if (!pipeline) return;
    await api.deleteStage(id);
    const p = await api.pipeline(pipeline.id);
    setPipeline(p);
    if (selectedId === id) setSelectedId(p.stages[0]?.id ?? null);
  }
  async function moveStage(id: string, dir: -1 | 1) {
    if (!pipeline) return;
    const movable = pipeline.stages.filter((s) => s.order !== 0);
    const idx = movable.findIndex((s) => s.id === id);
    const j = idx + dir;
    if (j < 0 || j >= movable.length) return;
    const arr = [...movable];
    [arr[idx], arr[j]] = [arr[j], arr[idx]];
    const p = await api.reorder(pipeline.id, arr.map((s) => s.id));
    setPipeline(p);
  }

  async function loadRun(rid: string) {
    setRunId(rid);
    await refreshRun(rid);
  }

  function saveToken() {
    setToken(tokenDraft.trim());
    setNeedToken(false);
    init();
  }

  if (needToken)
    return (
      <div className="h-full flex items-center justify-center">
        <div className="bg-neutral-900 border border-neutral-700 rounded-lg p-6 w-96">
          <div className="font-semibold mb-2">Access token required</div>
          <div className="text-xs text-neutral-400 mb-3">Backend is protected by APP_TOKEN.</div>
          <input
            type="password"
            value={tokenDraft}
            onChange={(e) => setTokenDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveToken()}
            placeholder="paste token"
            className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-2 text-sm mb-3"
            autoFocus
          />
          <button
            onClick={saveToken}
            className="w-full px-3 py-2 rounded bg-emerald-700 hover:bg-emerald-600 text-sm"
          >
            Save and continue
          </button>
        </div>
      </div>
    );

  if (!pipeline || !selected)
    return <div className="p-8 text-neutral-400">Loading…</div>;

  const hasRun = runId != null;

  // ── run progress (live signal) ──
  const TERMINAL = ["ok", "validation_failed", "skipped", "error"];
  const runStages = pipeline.stages
    .filter((s) => s.order >= 1 && s.enabled)
    .sort((a, b) => a.order - b.order);
  const totalStages = runStages.length;
  const doneCount = runStages.filter((s) =>
    TERMINAL.includes(results[s.order]?.status as string)
  ).length;
  const runningStage = runStages.find((s) => results[s.order]?.status === "running");
  const elapsed = runStartAt ? Math.max(0, Math.floor((nowTick - runStartAt) / 1000)) : 0;
  const mmss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  const pct = totalStages ? Math.round((doneCount / totalStages) * 100) : 0;
  const showProgress = busy || (runStartAt != null && doneCount < totalStages);

  // Client-side health hint for the report buttons (the backend deliver-gate is
  // the real enforcement). Any errored/validation-failed stage = review first.
  const reportIssues = hasRun
    ? runStages
        .filter((s) =>
          ["error", "validation_failed"].includes(results[s.order]?.status as string)
        )
        .map((s) =>
          results[s.order]?.status === "error"
            ? `Vaihe ${s.order} virhe`
            : `Vaihe ${s.order} tarkistus hylätty`
        )
    : [];

  return (
    <div className="h-full flex flex-col">
      {/* ── top bar ── */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-neutral-800 bg-neutral-950 shrink-0">
        <span className="font-semibold text-sm text-neutral-300">{pipeline.name}</span>

        {/* New Run — most important action */}
        <button
          onClick={newRun}
          disabled={busy}
          className="px-3 py-1.5 rounded bg-emerald-700 hover:bg-emerald-600 text-sm font-semibold disabled:opacity-40"
          title="Clear current run and start fresh with a new company"
        >
          ✚ New Run
        </button>

        {/* Run all */}
        <button
          disabled={busy || !inputData}
          onClick={runAll}
          className="px-3 py-1.5 rounded bg-sky-700 hover:bg-sky-600 text-sm font-medium disabled:opacity-40"
          title={!inputData ? "Fetch company data first (Stage 0)" : "Run all enabled stages"}
        >
          {busy ? "Running…" : "▶ Run all stages"}
        </button>

        <label className="flex items-center gap-1 text-xs text-neutral-400">
          <input
            type="checkbox"
            checked={stopOnFailure}
            onChange={(e) => setStopOnFailure(e.target.checked)}
            className="accent-sky-500"
          />
          Stop on failure
        </label>

        <div className="flex-1" />

        {/* cost */}
        {hasRun && (
          <span className="text-xs text-emerald-300 font-mono">
            ${totalCost.toFixed(5)}
          </span>
        )}

        {/* history */}
        <select
          onChange={(e) => e.target.value && loadRun(e.target.value)}
          value={runId ?? ""}
          className="bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-xs max-w-[220px]"
        >
          <option value="">— run history ({runs.length}) —</option>
          {runs.map((r) => (
            <option key={r.id} value={r.id}>
              {r.company_name ? `${r.company_name} · ` : ""}
              {r.created_at?.slice(0, 16)} · {r.status} · ${r.total_cost_usd?.toFixed(4)}
            </option>
          ))}
        </select>

        <button
          onClick={() => setShowCosts(true)}
          className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700"
        >
          💰 Costs
        </button>

        {/* reports */}
        {reportCaps.generator && (
          <>
            {hasRun && reportIssues.length > 0 && (
              <span
                title={"Tarkista ennen toimitusta:\n• " + reportIssues.join("\n• ")}
                className="text-xs px-2 py-1 rounded bg-red-900/60 text-red-200 font-medium"
              >
                ⚠ {reportIssues.length} {reportIssues.length === 1 ? "ongelma" : "ongelmaa"} — tarkista
              </span>
            )}
            <button
              disabled={!runId || reportBusy}
              onClick={openPreview}
              className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700 disabled:opacity-40"
              title="Esikatsele raportti sovelluksessa ennen toimitusta"
            >
              👁 Preview
            </button>
            <button
              disabled={!runId || reportBusy}
              onClick={() => openReport("html")}
              className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700 disabled:opacity-40"
            >
              📄 HTML
            </button>
            <button
              disabled={!runId || reportBusy || !reportCaps.pdf}
              onClick={() => openReport("pdf")}
              title={!reportCaps.pdf ? "Chrome not found — PDF unavailable" : ""}
              className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700 disabled:opacity-40"
            >
              📄 PDF
            </button>
          </>
        )}

        <button
          onClick={reseedDefaults}
          className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700"
          title="Reset stage prompts to repo defaults"
        >
          Reset prompts
        </button>

        <button
          onClick={() => api.refreshModels().then(setModels)}
          className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700"
          title="Refresh model list from OpenRouter"
        >
          ⟳ models ({models.length})
        </button>

        <button
          onClick={() => { setTokenDraft(getToken()); setNeedToken(true); }}
          className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700"
          title="Change access token"
        >
          🔒
        </button>
      </div>

      {/* ── run progress banner (live signal) ── */}
      {showProgress && (
        <div className="px-4 py-2 border-b border-neutral-800 bg-neutral-900 shrink-0">
          <div className="flex items-center gap-3 text-sm">
            <span className="w-2.5 h-2.5 rounded-full bg-sky-400 animate-pulse shrink-0" />
            <span className="font-medium text-sky-200">
              {busy
                ? runningStage
                  ? `Running stage ${runningStage.order}/${totalStages} — ${runningStage.name}`
                  : `Starting… (${doneCount}/${totalStages} done)`
                : `Finished ${doneCount}/${totalStages} stages`}
            </span>
            <span className="text-neutral-400 tabular-nums">⏱ {mmss(elapsed)}</span>
            <span className="text-neutral-500 text-xs">
              · Pro-models take ~1–3 min/stage · live cost ${totalCost.toFixed(4)}
            </span>
            <div className="flex-1" />
            <div className="flex items-center gap-1">
              {runStages.map((s) => {
                const st = results[s.order]?.status;
                const color =
                  st === "ok" || st === "validation_failed"
                    ? "bg-emerald-500"
                    : st === "error"
                    ? "bg-red-500"
                    : st === "running"
                    ? "bg-sky-400 animate-pulse"
                    : "bg-neutral-700";
                return (
                  <span
                    key={s.order}
                    title={`${s.order}. ${s.name}${st ? " — " + st : ""}`}
                    className={`w-6 h-1.5 rounded-full ${color}`}
                  />
                );
              })}
            </div>
          </div>
          <div className="mt-1.5 h-1 bg-neutral-800 rounded overflow-hidden">
            <div
              className="h-full bg-sky-500 transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}

      {/* ── body ── */}
      <div className="flex-1 grid grid-cols-[260px_1fr_1fr] min-h-0">
        {/* left: stage list */}
        <div className="border-r border-neutral-800 bg-neutral-950 overflow-auto">
          <StageList
            pipeline={pipeline}
            selectedId={selectedId}
            results={results}
            onSelect={setSelectedId}
            onToggle={toggleStage}
            onAdd={addStage}
            onDelete={deleteStage}
            onMove={moveStage}
          />
        </div>

        {/* middle: editor */}
        <div className="border-r border-neutral-800 overflow-hidden">
          <StageEditor
            key={selected.id}
            stage={selected}
            models={models}
            context={context}
            inputData={inputData}
            onSave={saveStage}
            onSetInputData={setInputData}
          />
        </div>

        {/* right: output */}
        <div className="overflow-hidden">
          <ResultPanel
            result={results[selected.order]}
            stage={selected}
            models={models}
            busy={busy}
            onRerun={(o) => rerun(o, false)}
            onRerunFrom={(o) => rerun(o, true)}
            onCompare={compare}
          />
        </div>
      </div>

      {cmp && <CompareOverlay data={cmp} onClose={() => setCmp(null)} />}
      {showCosts && (
        <CostOverlay pipeline={pipeline} results={results} onClose={() => setShowCosts(false)} />
      )}
      {previewUrl && (
        <PreviewOverlay
          url={previewUrl}
          onClose={() => {
            URL.revokeObjectURL(previewUrl);
            setPreviewUrl(null);
          }}
        />
      )}
    </div>
  );
}

function PreviewOverlay({ url, onClose }: { url: string; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center p-6 z-50" onClick={onClose}>
      <div
        className="bg-white rounded-lg overflow-hidden w-[90vw] h-[92vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-center px-3 py-2 bg-neutral-900 text-neutral-200 shrink-0">
          <span className="text-sm font-semibold">Raportin esikatselu</span>
          <div className="flex items-center gap-2">
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700"
            >
              Avaa uuteen välilehteen
            </a>
            <button onClick={onClose} className="text-neutral-400 hover:text-white text-lg leading-none">
              ✕
            </button>
          </div>
        </div>
        <iframe title="report-preview" src={url} className="flex-1 w-full bg-white" />
      </div>
    </div>
  );
}

function CompareOverlay({
  data,
  onClose,
}: {
  data: { order: number; results: any[] };
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center p-8 z-50"
      onClick={onClose}
    >
      <div
        className="bg-neutral-900 border border-neutral-700 rounded-lg p-4 max-w-[90vw] max-h-[85vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-center mb-3">
          <span className="font-semibold">Model comparison — Stage #{data.order}</span>
          <button onClick={onClose} className="text-neutral-400 hover:text-white">✕</button>
        </div>
        <div
          className="grid gap-3"
          style={{ gridTemplateColumns: `repeat(${data.results.length}, minmax(320px,1fr))` }}
        >
          {data.results.map((r, i) => (
            <div key={i} className="border border-neutral-700 rounded p-3">
              <div className="text-xs font-mono text-sky-300 mb-1">{r.model}</div>
              <div className="text-xs text-neutral-400 mb-2 flex gap-3">
                <span>{r.status}</span>
                <span>finish: {r.finish_reason}</span>
                <span className="text-emerald-300">${r.cost_usd?.toFixed(5)}</span>
              </div>
              {r.error_message && (
                <div className="text-xs text-red-300 mb-2">{r.error_message}</div>
              )}
              {r.validator_report && (
                <div className={`text-xs mb-2 ${r.validator_report.passed ? "text-emerald-400" : "text-red-400"}`}>
                  validator: {r.validator_report.passed ? "passed" : "failed"}
                </div>
              )}
              <pre className="text-[11px] whitespace-pre-wrap max-h-80 overflow-auto bg-neutral-950 p-2 rounded">
                {r.parsed_json ? JSON.stringify(r.parsed_json, null, 2) : r.raw_response}
              </pre>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
