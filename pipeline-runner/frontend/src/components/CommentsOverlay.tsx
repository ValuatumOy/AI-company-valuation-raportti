import { useEffect, useState } from "react";
import { api } from "../api";

// What the customer wrote, round by round. Every field here was already stored
// on the run; nothing displayed it, so after a refinement nobody could tell what
// had been asked for — or whether the new report answered it.
type Round = {
  run_id: string;
  round: number;
  created_at: string;
  status: string;
  user_input: string;
  clarifications: { id?: string; question?: string; answer?: string }[];
  clarifications_free_text: string;
  forecast_changes: string;
  forecast_previews: { at: string; text: string; summary: string; rows: any[] }[];
  empty: boolean;
};

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mt-2">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-neutral-500">
        {label}
      </div>
      <div className="mt-1 whitespace-pre-wrap text-xs text-neutral-200 bg-neutral-950 border border-neutral-800 rounded p-2">
        {children}
      </div>
    </div>
  );
}

export function CommentsOverlay({ rid, onClose }: { rid: string; onClose: () => void }) {
  const [rounds, setRounds] = useState<Round[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .runComments(rid)
      .then((r) => setRounds(r.rounds as Round[]))
      .catch((e) => setError(String(e)));
  }, [rid]);

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
          <span className="font-semibold">Asiakkaan kommentit</span>
          <button onClick={onClose} className="text-neutral-400 hover:text-white">
            ✕
          </button>
        </div>

        {error && <div className="text-sm text-red-400">{error}</div>}
        {!rounds && !error && <div className="text-sm text-neutral-500">Ladataan…</div>}

        <div className="space-y-3">
          {rounds?.map((r) => (
            <div key={r.run_id} className="border border-neutral-800 rounded p-3">
              <div className="flex items-center gap-3 text-sm">
                <span className="font-semibold text-neutral-100">
                  {r.round === 1 ? "Kierros 1 (alkuperäinen)" : `Kierros ${r.round}`}
                </span>
                <span className="text-xs text-neutral-500">
                  {r.created_at?.slice(0, 16).replace("T", " ")} · {r.status} ·{" "}
                  {r.run_id.slice(0, 8)}
                </span>
              </div>

              {r.empty && (
                <div className="mt-2 text-xs text-neutral-500">
                  Ei kommentteja tällä kierroksella.
                </div>
              )}

              {r.user_input && <Block label="Tilauksen lisätiedot">{r.user_input}</Block>}

              {r.clarifications.length > 0 && (
                <Block label="Vastaukset tarkentaviin kysymyksiin">
                  {r.clarifications
                    .map((c) => `${c.question || c.id || ""}\n→ ${c.answer || ""}`)
                    .join("\n\n")}
                </Block>
              )}

              {r.clarifications_free_text && (
                <Block label="Vapaa palaute">{r.clarifications_free_text}</Block>
              )}

              {/* Written only when edits were actually submitted and imported. A
                  round with previews but no forecast_changes means the customer
                  described a change, saw the proposal, and never applied it. */}
              {r.forecast_changes && (
                <Block label="Ennustemuutokset (viety malliin)">{r.forecast_changes}</Block>
              )}

              {r.forecast_previews.map((p, i) => (
                <Block
                  key={i}
                  label={`Ennustepyyntö AI:lle ${p.at?.slice(0, 16).replace("T", " ")}${
                    r.forecast_changes ? "" : " — EI VIETY MALLIIN"
                  }`}
                >
                  {p.text}
                  {p.summary ? `\n\nAI:n ehdotus: ${p.summary}` : ""}
                </Block>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
