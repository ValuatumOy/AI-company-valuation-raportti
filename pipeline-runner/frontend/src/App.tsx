import { useEffect, useMemo, useState } from "react";
import { api, streamRun, getToken, setToken } from "./api";
import type { ModelInfo, Pipeline, Run, Stage, StageResult } from "./types";
import { StageList } from "./components/StageList";
import { StageEditor } from "./components/StageEditor";
import { ResultPanel } from "./components/ResultPanel";
import { CostOverlay } from "./components/CostOverlay";
import { ValuatumExport } from "./components/ValuatumExport";

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
  const [totalCost, setTotalCost] = useState(0);
  const [runs, setRuns] = useState<any[]>([]);
  const [cmp, setCmp] = useState<{ order: number; results: any[] } | null>(null);
  const [showCosts, setShowCosts] = useState(false);
  const [showValuatum, setShowValuatum] = useState(false);
  const [reportCaps, setReportCaps] = useState({ generator: false, pdf: false });
  const [reportBusy, setReportBusy] = useState(false);
  const [needToken, setNeedToken] = useState(false);
  const [tokenDraft, setTokenDraft] = useState("");

  function init() {
    api
      .pipelines()
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
    api
      .health()
      .then((h) => {
        if (h.auth && !getToken()) setNeedToken(true);
        else init();
      })
      .catch(() => init());
  }, []);

  async function openReport(format: "html" | "pdf") {
    if (!runId) return;
    setReportBusy(true);
    try {
      const url = await api.reportUrl(runId, format);
      window.open(url, "_blank");
    } catch (e: any) {
      alert("Raportin generointi epäonnistui:\n" + (e?.message || e));
    } finally {
      setReportBusy(false);
    }
  }

  const selected = useMemo(
    () => pipeline?.stages.find((s) => s.id === selectedId) ?? null,
    [pipeline, selectedId]
  );

  // context available to the selected stage, derived from completed results
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

  function applyEvent(rid: string, e: any) {
    if (e.event === "stage") {
      setResults((prev) => ({
        ...prev,
        [e.order]: {
          ...(prev[e.order] as any),
          order: e.order,
          name: e.name,
          status: e.status,
          finish_reason: e.finish_reason ?? prev[e.order]?.finish_reason ?? null,
          validator_passed:
            e.validator_passed ?? prev[e.order]?.validator_passed ?? null,
          error_message: e.error_message ?? null,
        } as StageResult,
      }));
      if (["ok", "validation_failed", "error", "skipped"].includes(e.status)) {
        refreshRun(rid);
      }
    } else if (e.event === "done") {
      setTotalCost(e.total_cost_usd);
      refreshRun(rid);
      setBusy(false);
      api.runs().then(setRuns);
    }
  }

  async function runAll() {
    if (!pipeline) return;
    setBusy(true);
    setResults({});
    const { run_id } = await api.startRun({
      pipeline_id: pipeline.id,
      input_data: inputData ?? undefined,
      stop_on_failure: stopOnFailure,
    });
    setRunId(run_id);
    await streamRun(`/api/runs/${run_id}/stream`, "GET", (e) =>
      applyEvent(run_id, e)
    ).finally(() => setBusy(false));
  }

  async function rerun(order: number, from = false) {
    if (!runId) {
      // no run yet → start a fresh full run instead
      return runAll();
    }
    setBusy(true);
    const url = from
      ? `/api/runs/${runId}/stages/${order}/rerun-from`
      : `/api/runs/${runId}/stages/${order}/rerun`;
    await streamRun(url, "POST", (e) => applyEvent(runId, e)).finally(() =>
      setBusy(false)
    );
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

  // ---- stage CRUD ----
  async function saveStage(s: Stage) {
    const updated = await api.updateStage(s.id, s);
    setPipeline((p) =>
      p ? { ...p, stages: p.stages.map((x) => (x.id === s.id ? updated : x)) } : p
    );
  }
  async function reseedDefaults() {
    if (!pipeline) return;
    const ok = confirm(
      "Päivitetään oletusvaiheiden promptit repo-version mukaisiksi. Jatketaanko?"
    );
    if (!ok) return;
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
      name: `Vaihe ${order} – uusi`,
      order,
      enabled: true,
      model: "deepseek/deepseek-v4-flash",
      prompt_template: "",
      temperature: 0.2,
      max_tokens: 16000,
      reasoning_effort: null,
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
    const p = await api.reorder(
      pipeline.id,
      arr.map((s) => s.id)
    );
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
          <div className="font-semibold mb-2">Pääsytunnus vaaditaan</div>
          <div className="text-xs text-neutral-400 mb-3">
            Backend on suojattu jaetulla tunnuksella (APP_TOKEN).
          </div>
          <input
            type="password"
            value={tokenDraft}
            onChange={(e) => setTokenDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveToken()}
            placeholder="liitä token"
            className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-2 text-sm mb-3"
            autoFocus
          />
          <button
            onClick={saveToken}
            className="w-full px-3 py-2 rounded bg-emerald-700 hover:bg-emerald-600 text-sm"
          >
            Tallenna ja jatka
          </button>
        </div>
      </div>
    );

  if (!pipeline || !selected)
    return <div className="p-8 text-neutral-400">Ladataan…</div>;

  return (
    <div className="h-full flex flex-col">
      {/* top bar */}
      <div className="flex items-center gap-3 px-4 py-2 border-b border-neutral-800 bg-neutral-900">
        <span className="font-semibold">{pipeline.name}</span>
        <button
          onClick={() => setShowValuatum(true)}
          className="px-3 py-1.5 rounded bg-indigo-700 hover:bg-indigo-600 text-sm font-medium"
          title="Hae FAKTAT input_data Valuatumista (fid + nimi)"
        >
          📊 Valuatum JSON
        </button>
        <button
          disabled={busy}
          onClick={runAll}
          className="px-3 py-1.5 rounded bg-emerald-700 hover:bg-emerald-600 text-sm font-medium disabled:opacity-40"
        >
          {busy ? "Ajetaan…" : "▶ Aja koko pipeline"}
        </button>
        <label className="flex items-center gap-1 text-xs text-neutral-400">
          <input
            type="checkbox"
            checked={stopOnFailure}
            onChange={(e) => setStopOnFailure(e.target.checked)}
            className="accent-sky-500"
          />
          Pysäytä jos validaattori failaa
        </label>
        <div className="flex-1" />
        <span className="text-xs text-emerald-300">
          run-total: ${totalCost.toFixed(5)}
        </span>
        <select
          onChange={(e) => e.target.value && loadRun(e.target.value)}
          value={runId ?? ""}
          className="bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-xs"
        >
          <option value="">— ajohistoria —</option>
          {runs.map((r) => (
            <option key={r.id} value={r.id}>
              {r.created_at?.slice(0, 19)} · {r.status} · ${r.total_cost_usd?.toFixed(4)}
            </option>
          ))}
        </select>
        <button
          onClick={() => setShowCosts(true)}
          className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700"
        >
          € kustannukset
        </button>
        <button
          onClick={reseedDefaults}
          className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700"
          title="palauta oletusvaiheiden promptit repo-version mukaisiksi"
        >
          Päivitä promptit
        </button>
        {reportCaps.generator && (
          <>
            <button
              disabled={!runId || reportBusy}
              onClick={() => openReport("html")}
              className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700 disabled:opacity-40"
              title="Generoi HTML-raportti loppuvaiheen JSONista"
            >
              📄 raportti HTML
            </button>
            <button
              disabled={!runId || reportBusy || !reportCaps.pdf}
              onClick={() => openReport("pdf")}
              className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700 disabled:opacity-40"
              title={reportCaps.pdf ? "Generoi PDF" : "Chrome puuttuu — PDF ei käytettävissä"}
            >
              📄 PDF
            </button>
          </>
        )}
        <button
          onClick={() => api.refreshModels().then(setModels)}
          className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700"
          title="päivitä mallilista"
        >
          ⟳ mallit ({models.length})
        </button>
        <button
          onClick={() => {
            setTokenDraft(getToken());
            setNeedToken(true);
          }}
          className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700"
          title="vaihda pääsytunnus"
        >
          🔒
        </button>
      </div>

      {/* body */}
      <div className="flex-1 grid grid-cols-[280px_1fr_1fr] min-h-0">
        <div className="border-r border-neutral-800 bg-neutral-900 min-h-0">
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
        <div className="border-r border-neutral-800 min-h-0">
          <StageEditor
            key={selected.id}
            stage={selected}
            models={models}
            context={context}
            inputData={inputData}
            onSave={saveStage}
            onSetInputData={setInputData}
            onFetch={async (id) => {
              const r = await api.fetchCompany(id);
              if (r.ok) setInputData(r.input_data);
              else alert(r.message);
            }}
          />
        </div>
        <div className="min-h-0">
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

      {cmp && (
        <CompareOverlay data={cmp} onClose={() => setCmp(null)} />
      )}
      {showCosts && (
        <CostOverlay
          pipeline={pipeline}
          results={results}
          onClose={() => setShowCosts(false)}
        />
      )}
      {showValuatum && (
        <ValuatumExport
          onUseAsInput={(data) => {
            setInputData(data);
            setSelectedId(pipeline.stages.find((s) => s.order === 0)?.id ?? selectedId);
          }}
          onClose={() => setShowValuatum(false)}
        />
      )}
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
          <span className="font-semibold">
            Mallivertailu — Vaihe #{data.order}
          </span>
          <button onClick={onClose} className="text-neutral-400 hover:text-white">
            ✕
          </button>
        </div>
        <div
          className="grid gap-3"
          style={{ gridTemplateColumns: `repeat(${data.results.length}, minmax(320px,1fr))` }}
        >
          {data.results.map((r, i) => (
            <div key={i} className="border border-neutral-700 rounded p-2">
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
                <div
                  className={`text-xs mb-2 ${
                    r.validator_report.passed ? "text-emerald-400" : "text-red-400"
                  }`}
                >
                  validaattori: {r.validator_report.passed ? "läpäisi" : "failasi"}
                </div>
              )}
              <pre className="text-[11px] whitespace-pre-wrap max-h-80 overflow-auto bg-neutral-950 p-2 rounded">
                {r.parsed_json
                  ? JSON.stringify(r.parsed_json, null, 2)
                  : r.raw_response}
              </pre>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
