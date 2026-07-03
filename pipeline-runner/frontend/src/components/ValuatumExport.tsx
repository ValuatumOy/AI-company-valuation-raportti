import { useEffect, useState } from "react";
import { api, streamRun } from "../api";

type Phase = "idle" | "running" | "ready" | "error";

const STEP_LABEL: Record<string, string> = {
  fetch: "Fetching modeldata…",
  backfill: "Backfilling actuals…",
  ready: "JSON ready",
};

export function ValuatumExport({
  onUseAsInput,
  onClose,
}: {
  onUseAsInput: (data: any) => void;
  onClose: () => void;
}) {
  const [config, setConfig] = useState<{
    token: boolean;
    profinder: boolean;
    kit: boolean;
  } | null>(null);
  const [name, setName] = useState("");
  const [fid, setFid] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [codeOverride, setCodeOverride] = useState("");
  const [actuals, setActuals] = useState(15);
  const [estimates, setEstimates] = useState(10);

  const [phase, setPhase] = useState<Phase>("idle");
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{
    filename: string;
    json: any;
    warnings: string[];
  } | null>(null);

  useEffect(() => {
    api.valuatumConfig().then(setConfig).catch(() => {});
  }, []);

  async function generate() {
    if (!name.trim() || !fid.trim()) return;
    setPhase("running");
    setStatus(STEP_LABEL.fetch);
    setError(null);
    setResult(null);
    try {
      await streamRun(
        "/api/valuatum/company-json",
        "POST",
        (e) => {
          if (e.step === "fetch" || e.step === "backfill") {
            setStatus(STEP_LABEL[e.step]);
          } else if (e.step === "ready") {
            setStatus(STEP_LABEL.ready);
            setResult({ filename: e.filename, json: e.json, warnings: e.warnings || [] });
            setPhase("ready");
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
          company_code_override: advanced && codeOverride.trim() ? codeOverride.trim() : null,
        }
      );
    } catch (err: any) {
      setError(String(err));
      setPhase("error");
    }
  }

  function download() {
    if (!result) return;
    const blob = new Blob([JSON.stringify(result.json, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = result.filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  const forecastWarning = result?.warnings.find((w) =>
    w.startsWith("Forecasts may need")
  );
  const otherWarnings = result?.warnings.filter((w) => w !== forecastWarning) || [];

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center p-8 z-50"
      onClick={onClose}
    >
      <div
        className="bg-neutral-900 border border-neutral-700 rounded-lg p-5 w-[560px] max-h-[88vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-center mb-4">
          <span className="font-semibold">Valuatum JSON Export</span>
          <button onClick={onClose} className="text-neutral-400 hover:text-white">
            ✕
          </button>
        </div>

        {config && !config.token && (
          <div className="text-xs text-amber-300 bg-amber-950/40 border border-amber-800 rounded p-2 mb-3">
            VALUATUM_TOKEN puuttuu backendin <span className="font-mono">.env</span>
            :stä — haku ei toimi ilman sitä.
          </div>
        )}
        {config && config.token && !config.profinder && (
          <div className="text-xs text-neutral-400 mb-3">
            VALU_MCP_PROFINDER_URL ei asetettu — actuals-backfill ohitetaan.
          </div>
        )}

        <label className="block text-xs text-neutral-400 mb-1">Company name</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="esim. Teippimestarit Oy"
          className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-2 text-sm mb-3"
        />
        <label className="block text-xs text-neutral-400 mb-1">FID</label>
        <input
          value={fid}
          onChange={(e) => setFid(e.target.value.replace(/[^0-9]/g, ""))}
          placeholder="esim. 227394"
          inputMode="numeric"
          className="w-full bg-neutral-800 border border-neutral-700 rounded px-3 py-2 text-sm mb-3 font-mono"
        />

        <button
          onClick={() => setAdvanced((a) => !a)}
          className="text-xs text-sky-400 hover:text-sky-300 mb-2"
        >
          {advanced ? "▾" : "▸"} Advanced
        </button>
        {advanced && (
          <div className="bg-neutral-850 border border-neutral-800 rounded p-3 mb-3 space-y-3">
            <div>
              <label className="block text-xs text-neutral-400 mb-1">
                company_code_override (y-tunnus ilman väliviivaa)
              </label>
              <input
                value={codeOverride}
                onChange={(e) => setCodeOverride(e.target.value.replace(/[^0-9Kk]/g, ""))}
                placeholder="esim. 24388345"
                className="w-full bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-sm font-mono"
              />
              <div className="text-[10px] text-neutral-500 mt-1">
                Käytä jos backfill epäonnistuu tai companyCode näyttää väärältä.
                Teippimestarit Oy: 24388345.
              </div>
            </div>
            <div className="flex gap-3">
              <label className="flex-1">
                <span className="block text-xs text-neutral-400 mb-1">actuals</span>
                <input
                  type="number"
                  value={actuals}
                  onChange={(e) => setActuals(+e.target.value)}
                  className="w-full bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-sm"
                />
              </label>
              <label className="flex-1">
                <span className="block text-xs text-neutral-400 mb-1">estimates</span>
                <input
                  type="number"
                  value={estimates}
                  onChange={(e) => setEstimates(+e.target.value)}
                  className="w-full bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-sm"
                />
              </label>
            </div>
          </div>
        )}

        <button
          onClick={generate}
          disabled={phase === "running" || !name.trim() || !fid.trim()}
          className="w-full px-3 py-2 rounded bg-emerald-700 hover:bg-emerald-600 text-sm font-medium disabled:opacity-40"
        >
          {phase === "running" ? "Generoidaan…" : "Generate JSON"}
        </button>

        {phase === "running" && (
          <div className="flex items-center gap-2 text-sm text-sky-300 mt-3">
            <span className="w-3 h-3 rounded-full bg-sky-400 animate-pulse" />
            {status}
          </div>
        )}

        {phase === "error" && (
          <div className="mt-3 text-xs text-red-200 bg-red-950/50 border border-red-700 rounded p-2 whitespace-pre-wrap">
            {error}
          </div>
        )}

        {phase === "ready" && result && (
          <div className="mt-4 space-y-3">
            <div className="text-sm text-emerald-400">✓ JSON ready</div>

            {forecastWarning && (
              <div className="text-xs text-amber-200 bg-amber-950/40 border border-amber-700 rounded p-2">
                ⚠ {forecastWarning}
              </div>
            )}
            {otherWarnings.map((w, i) => (
              <div key={i} className="text-xs text-neutral-400 bg-neutral-850 rounded p-2">
                {w}
              </div>
            ))}

            <div className="flex gap-2">
              <button
                onClick={download}
                className="flex-1 px-3 py-2 rounded bg-sky-700 hover:bg-sky-600 text-sm"
              >
                ⤓ {result.filename}
              </button>
              <button
                onClick={() => {
                  onUseAsInput(result.json);
                  onClose();
                }}
                className="flex-1 px-3 py-2 rounded bg-neutral-700 hover:bg-neutral-600 text-sm"
                title="Aseta Stage 0:n input_dataksi"
              >
                → Käytä Stage 0 input_datana
              </button>
            </div>

            <pre className="text-[11px] bg-neutral-950 border border-neutral-800 rounded p-2 max-h-64 overflow-auto">
              {JSON.stringify(result.json?.meta ?? {}, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
