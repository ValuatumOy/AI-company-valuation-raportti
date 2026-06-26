import { useEffect, useMemo, useState } from "react";
import Editor from "@monaco-editor/react";
import type { ModelInfo, Stage } from "../types";
import { DATA_FETCHER_MODEL } from "../types";
import { ModelSelect } from "./ModelSelect";
import { api, streamRun } from "../api";

function substitute(template: string, ctx: Record<string, any>) {
  const missing: string[] = [];
  const text = template.replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_m, k) => {
    if (!(k in ctx)) { missing.push(k); return `{{${k}}}`; }
    const v = ctx[k];
    return typeof v === "string" ? v : JSON.stringify(v, null, 2);
  });
  return { text, missing };
}

export function StageEditor({
  stage,
  models,
  context,
  inputData,
  onSave,
  onSetInputData,
}: {
  stage: Stage;
  models?: ModelInfo[];
  context: Record<string, any>;
  inputData: any;
  onSave: (s: Stage) => void | Promise<void>;
  onSetInputData: (d: any) => void;
}) {
  const [draft, setDraft] = useState<Stage>(stage);
  const [showValidator, setShowValidator] = useState(false);
  const [showPrompt, setShowPrompt] = useState(true);
  const [showPreview, setShowPreview] = useState(false);
  const [saveState, setSaveState] = useState<"saved" | "dirty" | "saving" | "error">("saved");

  useEffect(() => {
    setDraft(stage);
    setSaveState("saved");
    setShowValidator(false);
  }, [stage.id]);

  const patch = (p: Partial<Stage>) => {
    setDraft((d) => ({ ...d, ...p }));
    setSaveState("dirty");
  };

  async function saveDraft() {
    setSaveState("saving");
    try { await onSave(draft); setSaveState("saved"); }
    catch { setSaveState("error"); }
  }

  const isFetcher = draft.model === DATA_FETCHER_MODEL;
  const preview = useMemo(
    () => substitute(draft.prompt_template, context),
    [draft.prompt_template, context]
  );
  const mappingKeys = Object.keys(draft.input_mapping || {});
  const unavailable = mappingKeys.filter((k) => !(k in context));

  return (
    <div className="flex flex-col h-full overflow-auto">
      {/* header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-neutral-800">
        <input
          value={draft.name}
          onChange={(e) => patch({ name: e.target.value })}
          className="bg-transparent border-b border-neutral-700 focus:border-sky-500 outline-none px-1 py-0.5 text-sm font-semibold flex-1 min-w-0"
        />
        <button
          onClick={saveDraft}
          disabled={saveState === "saving" || saveState === "saved"}
          className="px-3 py-1.5 rounded bg-sky-700 hover:bg-sky-600 disabled:bg-neutral-800 disabled:text-neutral-500 text-xs font-medium shrink-0"
        >
          {saveState === "saving" ? "Saving…" : saveState === "saved" ? "Saved" : "Save"}
        </button>
      </div>

      <div className="flex-1 overflow-auto px-4 py-3 space-y-4">
        {/* model + params row */}
        {!isFetcher && (
          <div className="space-y-2">
            <div className="text-xs text-neutral-500 uppercase tracking-wide">Model</div>
            <ModelSelect value={draft.model} models={models} onChange={(v) => patch({ model: v })} />
            <div className="flex flex-wrap gap-4 text-xs mt-2">
              <label className="flex items-center gap-1.5 text-neutral-400">
                max_tokens
                <input
                  type="number"
                  value={draft.max_tokens}
                  onChange={(e) => patch({ max_tokens: +e.target.value })}
                  className="bg-neutral-800 border border-neutral-700 rounded px-2 py-1 w-24"
                />
              </label>
              <label className="flex items-center gap-1.5 text-neutral-400">
                temp
                <input
                  type="number"
                  step="0.05"
                  value={draft.temperature}
                  onChange={(e) => patch({ temperature: +e.target.value })}
                  className="bg-neutral-800 border border-neutral-700 rounded px-2 py-1 w-20"
                />
              </label>
              <label className="flex items-center gap-1.5 text-neutral-400" title="Reasoning / thinking effort (reasoning-capable models only)">
                thinking
                <select
                  value={draft.reasoning_effort ?? ""}
                  onChange={(e) => patch({ reasoning_effort: e.target.value || null })}
                  className="bg-neutral-800 border border-neutral-700 rounded px-2 py-1"
                >
                  <option value="">off</option>
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                  <option value="xhigh">xhigh</option>
                </select>
              </label>
              <label
                className={`flex items-center gap-1.5 ${draft.web_search ? "text-sky-300" : "text-neutral-400"}`}
                title="Run this stage with live OpenRouter web search (~$4 / 1000 results)"
              >
                <input
                  type="checkbox"
                  checked={draft.web_search}
                  onChange={(e) => patch({ web_search: e.target.checked })}
                  className="accent-sky-500"
                />
                🌐 web search
              </label>
              <label className="flex items-center gap-1.5 text-neutral-400">
                <input
                  type="checkbox"
                  checked={draft.expects_json}
                  onChange={(e) => patch({ expects_json: e.target.checked })}
                  className="accent-sky-500"
                />
                expects JSON
              </label>
            </div>
          </div>
        )}

        {/* Stage 0 fetcher */}
        {isFetcher && (
          <Stage0Fetcher inputData={inputData} onSetInputData={onSetInputData} />
        )}

        {/* prompt section */}
        {!isFetcher && (
          <div className="space-y-2">
            <button
              onClick={() => setShowPrompt((s) => !s)}
              className="flex items-center gap-1.5 text-xs text-neutral-400 hover:text-neutral-200"
            >
              <span>{showPrompt ? "▾" : "▸"}</span>
              <span className="uppercase tracking-wide">Prompt template</span>
              {mappingKeys.length > 0 && (
                <span className="ml-2">
                  {mappingKeys.map((k) => (
                    <span
                      key={k}
                      className={`font-mono mr-1.5 ${k in context ? "text-emerald-500" : "text-red-400"}`}
                    >
                      {`{{${k}}}`}
                    </span>
                  ))}
                  {unavailable.length > 0 && (
                    <span className="text-red-400">⚠ not yet available</span>
                  )}
                </span>
              )}
            </button>
            {showPrompt && (
              <>
                <div className="border border-neutral-700 rounded overflow-hidden">
                  <Editor
                    height="220px"
                    theme="vs-dark"
                    defaultLanguage="markdown"
                    path={`prompt-${draft.id}`}
                    value={draft.prompt_template}
                    onChange={(v) => patch({ prompt_template: v ?? "" })}
                    options={{ minimap: { enabled: false }, fontSize: 12, wordWrap: "on" }}
                  />
                </div>
                <button
                  onClick={() => setShowPreview((s) => !s)}
                  className="text-xs text-sky-400 hover:text-sky-300"
                >
                  {showPreview ? "Hide" : "Show"} substituted preview
                </button>
                {showPreview && (
                  <pre className="text-[11px] bg-neutral-950 border border-neutral-800 rounded p-2 max-h-48 overflow-auto whitespace-pre-wrap">
                    {preview.missing.length > 0 && (
                      <span className="text-red-400 block mb-1">
                        missing: {preview.missing.join(", ")}
                      </span>
                    )}
                    {preview.text}
                  </pre>
                )}
              </>
            )}
          </div>
        )}

        {/* validator */}
        {!isFetcher && (
          <div className="space-y-2">
            <button
              onClick={() => setShowValidator((s) => !s)}
              className="flex items-center gap-1.5 text-xs text-neutral-400 hover:text-neutral-200"
            >
              <span>{showValidator ? "▾" : "▸"}</span>
              <span className="uppercase tracking-wide">Validator</span>
              <span className={draft.validator_code ? "text-emerald-500" : "text-neutral-600"}>
                {draft.validator_code ? "defined" : "none"}
              </span>
            </button>
            {showValidator && (
              <div className="border border-neutral-700 rounded overflow-hidden">
                <Editor
                  height="240px"
                  theme="vs-dark"
                  defaultLanguage="python"
                  path={`validator-${draft.id}`}
                  value={draft.validator_code ?? ""}
                  onChange={(v) => patch({ validator_code: v || null })}
                  options={{ minimap: { enabled: false }, fontSize: 12 }}
                />
              </div>
            )}
          </div>
        )}

        {saveState === "error" && (
          <div className="text-xs text-red-400">Save failed.</div>
        )}
      </div>
    </div>
  );
}

// ─── Stage 0: integrated Valuatum fetch + manual paste ───────────────────────

type FetchPhase = "idle" | "running" | "done" | "error";

function Stage0Fetcher({
  inputData,
  onSetInputData,
}: {
  inputData: any;
  onSetInputData: (d: any) => void;
}) {
  const [name, setName] = useState("");
  const [fid, setFid] = useState("");
  const [codeOverride, setCodeOverride] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [actuals, setActuals] = useState(5);
  const [estimates, setEstimates] = useState(10);

  const [phase, setPhase] = useState<FetchPhase>("idle");
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  const [showPaste, setShowPaste] = useState(false);
  const [pasteText, setPasteText] = useState(inputData ? JSON.stringify(inputData, null, 2) : "");
  const [pasteErr, setPasteErr] = useState<string | null>(null);
  const [pasteOk, setPasteOk] = useState(false);

  useEffect(() => {
    setPasteText(inputData ? JSON.stringify(inputData, null, 2) : "");
  }, [inputData]);

  // Auto-apply pasted JSON once it's valid — no separate "Use as input_data"
  // press needed. (Fetched data is already applied automatically on success.)
  useEffect(() => {
    if (!showPaste || !pasteText.trim()) return;
    const t = setTimeout(() => {
      try {
        const parsed = JSON.parse(pasteText);
        if (JSON.stringify(parsed) !== JSON.stringify(inputData)) {
          onSetInputData(parsed);
          setPasteErr(null);
          setPasteOk(true);
          window.setTimeout(() => setPasteOk(false), 2500);
        }
      } catch {
        /* partial/invalid JSON — wait until it parses */
      }
    }, 700);
    return () => clearTimeout(t);
  }, [pasteText, showPaste]);

  async function fetchValuatum() {
    if (!name.trim() || !fid.trim()) return;
    setPhase("running");
    setStatus("Fetching modeldata…");
    setError(null);
    setWarnings([]);
    try {
      await streamRun(
        "/api/valuatum/company-json",
        "POST",
        (e) => {
          if (e.step === "fetch") setStatus("Fetching modeldata from Valuatum…");
          else if (e.step === "backfill") setStatus(`Backfilling actuals (${e.company_code ?? ""})…`);
          else if (e.step === "ready") {
            setStatus("Done");
            setPhase("done");
            setWarnings(e.warnings ?? []);
            onSetInputData(e.json);
          } else if (e.step === "error") {
            setError(e.message);
            setPhase("error");
          }
        },
        {
          company_name: name.trim(),
          fid: Number(fid),
          actuals,
          estimates,
          company_code_override: showAdvanced && codeOverride.trim() ? codeOverride.trim() : null,
        }
      );
    } catch (err: any) {
      setError(String(err));
      setPhase("error");
    }
  }

  function applyPaste() {
    try {
      const parsed = JSON.parse(pasteText);
      onSetInputData(parsed);
      setPasteErr(null);
      setShowPaste(false);
      setPasteOk(true);
      window.setTimeout(() => setPasteOk(false), 3000);
    } catch (e: any) {
      setPasteErr("JSON parse error: " + e.message);
    }
  }

  const hasData = inputData != null;

  return (
    <div className="space-y-4">
      {/* Valuatum fetch form */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-4 space-y-3">
        <div className="text-xs text-neutral-500 uppercase tracking-wide mb-1">Fetch from Valuatum</div>
        <div className="flex gap-2">
          <div className="flex-1">
            <div className="text-xs text-neutral-400 mb-1">Company name</div>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && fetchValuatum()}
              placeholder="e.g. Teippimestarit Oy"
              disabled={phase === "running"}
              className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-2 text-sm disabled:opacity-50"
            />
          </div>
          <div className="w-32">
            <div className="text-xs text-neutral-400 mb-1">FID</div>
            <input
              value={fid}
              onChange={(e) => setFid(e.target.value.replace(/[^0-9]/g, ""))}
              onKeyDown={(e) => e.key === "Enter" && fetchValuatum()}
              placeholder="227394"
              inputMode="numeric"
              disabled={phase === "running"}
              className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-2 text-sm font-mono disabled:opacity-50"
            />
          </div>
        </div>

        <button
          onClick={() => setShowAdvanced((a) => !a)}
          className="text-xs text-sky-400 hover:text-sky-300"
        >
          {showAdvanced ? "▾" : "▸"} Advanced options
        </button>
        {showAdvanced && (
          <div className="bg-neutral-850 border border-neutral-800 rounded p-3 space-y-2">
            <div>
              <div className="text-xs text-neutral-400 mb-1">company_code override (y-tunnus without dash)</div>
              <input
                value={codeOverride}
                onChange={(e) => setCodeOverride(e.target.value.replace(/[^0-9Kk]/g, ""))}
                placeholder="e.g. 24388345"
                className="w-full bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-sm font-mono"
              />
            </div>
            <div className="flex gap-3">
              <label className="flex-1 text-xs text-neutral-400">
                actuals
                <input type="number" value={actuals} onChange={(e) => setActuals(+e.target.value)}
                  className="mt-1 w-full bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-sm" />
              </label>
              <label className="flex-1 text-xs text-neutral-400">
                estimates
                <input type="number" value={estimates} onChange={(e) => setEstimates(+e.target.value)}
                  className="mt-1 w-full bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-sm" />
              </label>
            </div>
          </div>
        )}

        <button
          onClick={fetchValuatum}
          disabled={phase === "running" || !name.trim() || !fid.trim()}
          className="w-full py-2 rounded bg-indigo-700 hover:bg-indigo-600 disabled:opacity-40 text-sm font-medium"
        >
          {phase === "running" ? "Fetching…" : "Fetch company data"}
        </button>

        {phase === "running" && (
          <div className="flex items-center gap-2 text-xs text-sky-300">
            <span className="w-2 h-2 rounded-full bg-sky-400 animate-pulse" />
            {status}
          </div>
        )}
        {phase === "error" && error && (
          <div className="text-xs text-red-200 bg-red-950/40 border border-red-700 rounded p-2 whitespace-pre-wrap">
            {error}
          </div>
        )}
        {phase === "done" && (
          <div className="text-xs text-emerald-400">
            ✓ Data loaded{inputData?.meta?.company_name ? ` — ${inputData.meta.company_name}` : ""} · ready to run (press ▶ Run pipeline)
            {warnings.length > 0 && (
              <ul className="mt-1 text-amber-300 space-y-0.5">
                {warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}
              </ul>
            )}
          </div>
        )}
      </div>

      {/* data preview */}
      {hasData && (
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <div className="text-xs text-neutral-500 uppercase tracking-wide">Input data loaded</div>
            {pasteOk && (
              <span className="text-xs text-emerald-400">✓ set as input_data</span>
            )}
          </div>
          <pre className="text-[11px] bg-neutral-950 border border-neutral-800 rounded p-2 max-h-36 overflow-auto">
            {JSON.stringify(inputData?.meta ?? inputData, null, 2)}
          </pre>
        </div>
      )}

      {/* manual paste */}
      <div className="space-y-2">
        <button
          onClick={() => setShowPaste((s) => !s)}
          className="text-xs text-neutral-400 hover:text-neutral-200"
        >
          {showPaste ? "▾" : "▸"} Or paste JSON manually
        </button>
        {showPaste && (
          <>
            <div className="border border-neutral-700 rounded overflow-hidden">
              <Editor
                height="280px"
                theme="vs-dark"
                defaultLanguage="json"
                value={pasteText}
                onChange={(v) => setPasteText(v ?? "")}
                options={{ minimap: { enabled: false }, fontSize: 12 }}
              />
            </div>
            {pasteErr && <div className="text-xs text-red-400">{pasteErr}</div>}
            <div className="flex items-center gap-2">
              <span className="text-xs text-emerald-400">
                {pasteOk ? "✓ applied" : "Applies automatically when the JSON is valid"}
              </span>
              <button
                onClick={applyPaste}
                className="px-2 py-1 text-xs rounded bg-neutral-700 hover:bg-neutral-600"
                title="Optional — paste auto-applies"
              >
                apply now
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
