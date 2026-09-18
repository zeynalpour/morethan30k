// API client for the TME settings dashboard (S0.4).
// Talks to the FastAPI backend (/api/*) with the dashboard bearer token
// issued by the Main Bot — no direct database access, no secrets in the
// browser.

export interface BotRow {
  id: number;
  username: string | null;
  title: string | null;
  bot_type: string;
  is_active: boolean;
  webhook_registered: boolean;
  created_at: string;
  // "active" (serving) | "paused" (owner switched it off) | "archived"
  // (deleted in BotFather — Telegram rejected the token, detected by the
  // backend's liveness probe; see tme/services/bot_health.py).
  state: string;
}

export interface MenuButtonData {
  text: string;
  callback?: string | null;
  url?: string | null;
}

// Per-language copy overrides (Phase 1 i18n). Every field optional — the
// backend falls back per field: user language → main language → base flow.
export interface StepTranslation {
  prompt?: string;
  // Localized option LABELS, positionally aligned with the step's options
  // (the option `value` is never translated — it is the answer key).
  options?: string[];
}

export interface Translation {
  welcome_message?: string;
  fallback_message?: string;
  greeting?: string;
  echo_prefix?: string;
  menu_buttons?: MenuButtonData[];
  // Issue #23 — per-step copy, keyed by the step's `id` in the flow's
  // `steps` array.
  steps?: Record<string, StepTranslation>;
}

// One option of a flow step. `value` is the answer key (what a quiz scores
// against) and is never translated; `label` is the user-visible button text.
export interface StepOptionData {
  label: string;
  value: string;
}

// A step of a multi-step `steps` flow (the S2.2 primitive, issue #23). Lives
// as an `extra="allow"` rider in the flow — no schema column, no migration.
export interface FlowStepData {
  id: string;
  prompt: string;
  options?: StepOptionData[];
  // "auto" (choice when options exist, else free text) | "free_text" | "none"
  // ("none" = informational / terminal result step).
  answer_type?: string;
  correct_answers?: string[];
}

export interface BotConfigFlow {
  bot_type?: string;
  version?: number;
  welcome_message?: string;
  menu_buttons?: MenuButtonData[];
  // BOOKKEEPING, not a setting (IDEAS N step 0): the backend DERIVES this from
  // the flow's own content on every write (tme.modules.normalize_flow), so a
  // bot can never advertise a module its flow does not contain. The dashboard
  // does not send it — see ModulesPanel for what the owner actually sees.
  active_modules?: string[];
  fallback_message?: string;
  greeting?: string;
  echo_prefix?: string;
  translations?: Record<string, Translation>;
  single_language?: boolean;
  // Issue #23 — the language the bot's base copy is written in (ISO-639-1).
  // null/absent = English middle layer (the historical behaviour).
  main_language?: string | null;
  steps?: FlowStepData[];
  template?: { id: string; version: number };
}

// IDEAS N step 0 — one engine capability from the in-code module registry,
// resolved against a single bot. `active` is DERIVED by the backend from the
// flow the engine executes; the dashboard only renders it (never sends it
// back), so the toggle can never disagree with the bot's actual flow.
export interface ModuleSummary {
  id: string;
  version: number;
  display_name: string;
  description: string;
  config_keys: string[];
  dependencies: string[];
  active: boolean;
}

// S2.3 — a registry template card (latest version, no seed data).
export interface TemplateSummary {
  id: string;
  version: number;
  bot_type: string;
  display_name: string;
  description: string;
}

// S2.3 — a bot's template lineage + "update available" badge (pure read).
export interface TemplateProvenance {
  current: { id: string; version: number } | null;
  latest_version: number | null;
  update_available: boolean;
}

export interface BotConfigRow {
  id: number;
  bot_id: number;
  flow: BotConfigFlow;
  description?: string | null;
  created_at: string;
  updated_at: string;
}

let authToken: string | null = null;

export function setAuthToken(token: string): void {
  authToken = token;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  // Telegram WebApp initData — signed with the bot token; the backend
  // validates it on every request (see tme/services/auth.py).
  if (authToken) headers.set("X-Telegram-Init-Data", authToken);
  if (init?.body) headers.set("Content-Type", "application/json");

  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail?.error) detail = body.detail.error;
      else if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body — keep the status-based message */
    }
    throw new Error(detail);
  }
  // 204 No Content (bot deletion) — nothing to parse.
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  listBots: () => request<BotRow[]>("/api/bots"),
  getBot: (botId: number) => request<BotRow>(`/api/bots/${botId}`),
  getConfig: (botId: number) => request<BotConfigFlow>(`/api/bots/${botId}/config`),
  saveConfig: (botId: number, flow: BotConfigFlow) =>
    request<BotConfigFlow>(`/api/bots/${botId}/config`, {
      method: "PATCH",
      body: JSON.stringify({ flow }),
    }),
  // Management knobs: enable/disable, switch bot type (resets config to
  // that type's defaults on the backend).
  updateBot: (botId: number, patch: { is_active?: boolean; bot_type?: string }) =>
    request<BotRow>(`/api/bots/${botId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  // Permanent removal (archive cleanup for bots deleted in BotFather).
  deleteBot: (botId: number) =>
    request<void>(`/api/bots/${botId}`, { method: "DELETE" }),
  // IDEAS N step 0 — the module registry, resolved against this bot's flow
  // (read-only: `active` is derived from the flow, never typed by the owner).
  listModules: (botId: number) => request<ModuleSummary[]>(`/api/bots/${botId}/modules`),
  // S2.3 — template registry + re-clone ("Reset to template").
  listTemplates: () => request<TemplateSummary[]>("/api/templates"),
  getBotTemplate: (botId: number) =>
    request<TemplateProvenance>(`/api/bots/${botId}/template`),
  recloneTemplate: (
    botId: number,
    body: { template_id: string; version?: number; preserve?: string[] }
  ) =>
    request<BotRow>(`/api/bots/${botId}/reclone`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

export function wrapConfig(bot: BotRow, flow: BotConfigFlow): BotConfigRow {
  return {
    id: bot.id,
    bot_id: bot.id,
    flow,
    created_at: bot.created_at,
    updated_at: bot.created_at,
  };
}
