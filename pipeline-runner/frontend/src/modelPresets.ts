import type { ModelInfo } from "./types";

// Curated latest model per family. Ids verified against the live OpenRouter
// /models list (prices below are indicative; live $/M is read from the models
// API at runtime via modelPrice()).
export interface ModelPreset {
  id: string;
  label: string;
}
export const MODEL_GROUPS: { group: string; items: ModelPreset[] }[] = [
  {
    group: "Claude",
    items: [
      { id: "anthropic/claude-sonnet-5", label: "Claude Sonnet 5" },
      { id: "anthropic/claude-opus-4.8", label: "Claude Opus 4.8" },
      { id: "anthropic/claude-sonnet-4.6", label: "Claude Sonnet 4.6" },
      { id: "anthropic/claude-haiku-4.5", label: "Claude Haiku 4.5" },
    ],
  },
  {
    group: "OpenAI",
    items: [
      { id: "openai/gpt-5.5", label: "GPT-5.5" },
      { id: "openai/gpt-5.5-pro", label: "GPT-5.5 Pro" },
      { id: "openai/gpt-5.4", label: "GPT-5.4" },
      { id: "openai/gpt-5.4-mini", label: "GPT-5.4 Mini" },
    ],
  },
  {
    group: "Gemini",
    items: [
      { id: "google/gemini-3.5-flash", label: "Gemini 3.5 Flash" },
      { id: "google/gemini-3.1-pro-preview", label: "Gemini 3.1 Pro" },
      { id: "google/gemini-2.5-flash", label: "Gemini 2.5 Flash" },
    ],
  },
  {
    group: "GLM",
    items: [
      { id: "z-ai/glm-5.2", label: "GLM 5.2" },
      { id: "z-ai/glm-4.7", label: "GLM 4.7" },
    ],
  },
  {
    group: "Qwen",
    items: [
      { id: "qwen/qwen3.7-max", label: "Qwen3.7 Max" },
      { id: "qwen/qwen3.7-plus", label: "Qwen3.7 Plus" },
    ],
  },
  {
    group: "Kimi",
    items: [
      { id: "moonshotai/kimi-k2.6", label: "Kimi K2.6" },
      { id: "moonshotai/kimi-k2.5", label: "Kimi K2.5" },
    ],
  },
  {
    group: "DeepSeek",
    items: [
      { id: "deepseek/deepseek-v4-pro", label: "DeepSeek V4 Pro" },
      { id: "deepseek/deepseek-v4-flash", label: "DeepSeek V4 Flash" },
    ],
  },
];

export const PRESET_IDS = MODEL_GROUPS.flatMap((g) => g.items.map((m) => m.id));

// OpenRouter prices come back per-token; show them per-million tokens.
export function modelPrice(models: ModelInfo[] | undefined, id: string) {
  const m = models?.find((x) => x.id === id);
  if (!m) return null;
  return { inM: m.prompt_price * 1e6, outM: m.completion_price * 1e6 };
}

export function fmtUsd(n: number) {
  return "$" + parseFloat(n.toFixed(2));
}

export function priceText(models: ModelInfo[] | undefined, id: string) {
  const p = modelPrice(models, id);
  return p ? `${fmtUsd(p.inM)}/${fmtUsd(p.outM)} /M` : "";
}
