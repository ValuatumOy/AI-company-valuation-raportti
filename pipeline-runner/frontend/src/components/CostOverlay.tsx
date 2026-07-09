import { useEffect, useState } from "react";
import { api } from "../api";
import type { Pipeline, StageResult } from "../types";

export function CostOverlay({
  pipeline,
  results,
  onClose,
}: {
  pipeline: Pipeline;
  results: Record<number, StageResult>;
  onClose: () => void;
}) {
  const [summary, setSummary] = useState<{
    grand_total_usd: number;
    by_model: any[];
    runs: any[];
  } | null>(null);

  useEffect(() => {
    api.costs().then(setSummary);
  }, []);

  const modelOf = (order: number) =>
    pipeline.stages.find((s) => s.order === order)?.model ?? "?";
  const current = Object.values(results).sort((a, b) => a.order - b.order);
  const runTotal = current.reduce((s, r) => s + (r.cost_usd || 0), 0);

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center p-8 z-50"
      onClick={onClose}
    >
      <div
        className="bg-neutral-900 border border-neutral-700 rounded-lg p-4 w-[860px] max-h-[85vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-center mb-4">
          <span className="font-semibold">Kustannukset</span>
          <button onClick={onClose} className="text-neutral-400 hover:text-white">
            ✕
          </button>
        </div>

        {/* current run, per prompt/stage */}
        <div className="text-xs uppercase tracking-wide text-neutral-500 mb-1">
          Nykyinen ajo — per vaihe (prompt)
        </div>
        {current.length === 0 ? (
          <div className="text-xs text-neutral-500 mb-4">ei ajoa</div>
        ) : (
          <table className="w-full text-xs mb-4">
            <thead className="text-neutral-500 text-left">
              <tr>
                <th className="py-1">#</th>
                <th>Vaihe</th>
                <th>Malli</th>
                <th className="text-right">prompt-tok</th>
                <th className="text-right">compl-tok</th>
                <th className="text-right">USD</th>
              </tr>
            </thead>
            <tbody>
              {current.map((r) => (
                <tr key={r.order} className="border-t border-neutral-800">
                  <td className="py-1">{r.order}</td>
                  <td className="truncate max-w-[180px]">{r.name}</td>
                  <td className="font-mono text-neutral-400">{modelOf(r.order)}</td>
                  <td className="text-right">{r.tokens_prompt}</td>
                  <td className="text-right">{r.tokens_completion}</td>
                  <td className="text-right text-emerald-300">
                    ${(r.cost_usd || 0).toFixed(5)}
                  </td>
                </tr>
              ))}
              <tr className="border-t border-neutral-700 font-semibold">
                <td colSpan={5} className="py-1 text-right">
                  Ajo yhteensä
                </td>
                <td className="text-right text-emerald-300">
                  ${runTotal.toFixed(5)}
                </td>
              </tr>
            </tbody>
          </table>
        )}

        {/* global by model */}
        <div className="text-xs uppercase tracking-wide text-neutral-500 mb-1">
          Kaikki ajot — per malli
        </div>
        <table className="w-full text-xs mb-4">
          <thead className="text-neutral-500 text-left">
            <tr>
              <th className="py-1">Malli</th>
              <th className="text-right">kutsut</th>
              <th className="text-right">prompt-tok</th>
              <th className="text-right">compl-tok</th>
              <th className="text-right">USD</th>
            </tr>
          </thead>
          <tbody>
            {summary?.by_model.map((m) => (
              <tr key={m.model} className="border-t border-neutral-800">
                <td className="py-1 font-mono text-neutral-300">{m.model}</td>
                <td className="text-right">{m.calls}</td>
                <td className="text-right">{m.tokens_prompt}</td>
                <td className="text-right">{m.tokens_completion}</td>
                <td className="text-right text-emerald-300">
                  ${m.cost_usd.toFixed(5)}
                </td>
              </tr>
            ))}
            <tr className="border-t border-neutral-700 font-semibold">
              <td colSpan={4} className="py-1 text-right">
                Kaikki yhteensä
              </td>
              <td className="text-right text-emerald-300">
                ${(summary?.grand_total_usd ?? 0).toFixed(5)}
              </td>
            </tr>
          </tbody>
        </table>

        {/* per run */}
        <div className="text-xs uppercase tracking-wide text-neutral-500 mb-1">
          Ajohistoria — per pipeline-ajo
        </div>
        <table className="w-full text-xs">
          <thead className="text-neutral-500 text-left">
            <tr>
              <th className="py-1">Aika</th>
              <th>Raportti</th>
              <th>Tila</th>
              <th className="text-right">vaiheita</th>
              <th className="text-right">USD</th>
            </tr>
          </thead>
          <tbody>
            {summary?.runs.map((r) => (
              <tr key={r.id} className="border-t border-neutral-800">
                <td className="py-1">{r.created_at?.slice(0, 19)}</td>
                <td className="truncate max-w-[220px]">
                  {r.company_name || "—"}
                </td>
                <td>{r.status}</td>
                <td className="text-right">{r.stage_count}</td>
                <td className="text-right text-emerald-300">
                  ${r.total_cost_usd.toFixed(5)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
