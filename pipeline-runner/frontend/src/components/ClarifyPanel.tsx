import { useState } from "react";
import type { ClarificationRequest } from "../types";

// Round-1 → round-2 bridge: the AI's own list of what it could not verify.
// The user answers what they know; a round-2 run treats those as ground truth.
export function ClarifyPanel({
  requests,
  busy,
  onSubmit,
}: {
  requests: ClarificationRequest[];
  busy: boolean;
  onSubmit: (
    answers: { id: string; question: string; answer: string }[],
    freeText: string,
    showOldNumbers: boolean,
    scenarioProbabilities?: { pessimistic: number; base: number; optimistic: number }
  ) => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [freeText, setFreeText] = useState("");
  const [showOldNumbers, setShowOldNumbers] = useState(false);
  const [probs, setProbs] = useState({ pessimistic: "", base: "", optimistic: "" });
  const probsFilled = [probs.pessimistic, probs.base, probs.optimistic].filter(
    (v) => v.trim() !== ""
  ).length;
  const probsSum =
    (parseInt(probs.pessimistic) || 0) +
    (parseInt(probs.base) || 0) +
    (parseInt(probs.optimistic) || 0);
  const probsValid = probsFilled === 3 && probsSum === 100;
  // Partial or non-100 entry is an error the user must fix or clear.
  const probsError = probsFilled > 0 && !probsValid;
  const answered =
    Object.values(answers).filter((v) => v.trim()).length +
    (freeText.trim() ? 1 : 0) +
    (probsValid ? 1 : 0);

  return (
    <div className="px-4 py-3 border-b border-amber-900/50 bg-amber-950/20 shrink-0">
      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-amber-300 font-semibold text-sm">
          Täydennä ja tarkenna
        </span>
        <span className="text-[11px] text-neutral-400">
          AI ei voinut varmentaa näitä julkisista lähteistä. Vastaa mihin voit —
          kierros 2 laskee arvon uudelleen näillä tiedoilla.
        </span>
      </div>
      <div className="grid gap-2 md:grid-cols-2">
        {requests.map((r) => (
          <div
            key={r.id}
            className="bg-neutral-900 border border-neutral-800 rounded p-2"
          >
            <div className="text-xs text-neutral-200 font-medium">
              {r.question}
            </div>
            {r.valuation_impact ? (
              <div className="text-[10px] text-amber-400/80 mt-0.5">
                Vaikutus: {r.valuation_impact}
              </div>
            ) : null}
            {r.current_assumption ? (
              <div className="text-[10px] text-neutral-500 mt-0.5">
                Nykyoletus: {r.current_assumption}
              </div>
            ) : null}
            <textarea
              value={answers[r.id] || ""}
              onChange={(e) =>
                setAnswers((a) => ({ ...a, [r.id]: e.target.value }))
              }
              disabled={busy}
              rows={2}
              placeholder="Vastauksesi (jätä tyhjäksi jos et tiedä)"
              className="mt-1 w-full bg-neutral-950 border border-neutral-700 rounded px-2 py-1 text-xs text-neutral-200 resize-y disabled:opacity-50"
            />
          </div>
        ))}
      </div>
      <div className="mt-2 bg-neutral-900 border border-neutral-800 rounded p-2">
        <div className="text-xs text-neutral-200 font-medium">
          Skenaarioiden todennäköisyydet (valinnainen)
        </div>
        <div className="text-[10px] text-neutral-500 mt-0.5">
          Jätä tyhjäksi = AI valitsee profiilin. Täytä kaikki kolme, summa 100 %.
        </div>
        <div className="flex items-center gap-2 mt-1.5">
          {(
            [
              ["pessimistic", "Pessimistinen"],
              ["base", "Konservatiivinen"],
              ["optimistic", "Optimistinen"],
            ] as const
          ).map(([k, label]) => (
            <label key={k} className="flex flex-col text-[10px] text-neutral-400">
              {label}
              <input
                type="number"
                min={0}
                max={100}
                step={5}
                value={probs[k]}
                onChange={(e) => setProbs((p) => ({ ...p, [k]: e.target.value }))}
                disabled={busy}
                placeholder="%"
                className="mt-0.5 w-16 bg-neutral-950 border border-neutral-700 rounded px-2 py-1 text-xs text-neutral-200 disabled:opacity-50"
              />
            </label>
          ))}
          <span
            className={`text-[10px] ${probsError ? "text-red-400" : "text-neutral-500"}`}
          >
            {probsFilled > 0 ? `Summa ${probsSum} %` : ""}
            {probsError ? " — täytä kaikki kolme, summan oltava 100 %" : ""}
          </span>
        </div>
      </div>
      <textarea
        value={freeText}
        onChange={(e) => setFreeText(e.target.value)}
        disabled={busy}
        rows={2}
        placeholder="Muuta täydennettävää (vapaa teksti)…"
        className="mt-2 w-full bg-neutral-950 border border-neutral-700 rounded px-2 py-1 text-xs text-neutral-200 resize-y disabled:opacity-50"
      />
      <div className="flex items-center gap-3 mt-2">
        <button
          onClick={() =>
            onSubmit(
              requests
                .map((r) => ({
                  id: r.id,
                  question: r.question,
                  answer: (answers[r.id] || "").trim(),
                }))
                .filter((a) => a.answer),
              freeText.trim(),
              showOldNumbers,
              probsValid
                ? {
                    pessimistic: parseInt(probs.pessimistic),
                    base: parseInt(probs.base),
                    optimistic: parseInt(probs.optimistic),
                  }
                : undefined
            )
          }
          disabled={busy || answered === 0 || probsError}
          className="px-3 py-1.5 rounded bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold disabled:opacity-40"
        >
          Aja kierros 2 tarkennetuilla tiedoilla
        </button>
        <label className="flex items-center gap-1.5 text-[11px] text-neutral-400 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showOldNumbers}
            onChange={(e) => setShowOldNumbers(e.target.checked)}
            disabled={busy}
            className="accent-amber-600"
          />
          Näytä vanhat luvut (vanha → uusi)
        </label>
        <span className="text-[11px] text-neutral-500">
          {answered} vastausta annettu
        </span>
      </div>
    </div>
  );
}
