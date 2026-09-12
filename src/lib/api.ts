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
// backend falls back per field: user language → English → base flow.
export interface Translation {
  welcome_message?: string;
  fallback_message?: string;
  greeting?: string;
  echo_prefix?: string;
  menu_buttons?: MenuButtonData[];
}

export interface BotConfigFlow {
  bot_type?: string;
  version?: number;
  welcome_message?: string;
  menu_buttons?: MenuButtonData[];
  active_modules?: string[];
  fallback_message?: string;
  greeting?: string;
  echo_prefix?: string;
  translations?: Record<string, Translation>;
  single_language?: boolean;
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
