import { createClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
  auth: {
    persistSession: false,
    autoRefreshToken: false,
  },
});

export interface DashboardAuthToken {
  id: string;
  token: string;
  bot_id: number;
  owner_telegram_id: number;
  expires_at: string;
  used_at: string | null;
  created_at: string;
}

export interface BotRow {
  id: number;
  token: string;
  telegram_bot_id: number;
  username: string | null;
  title: string | null;
  bot_type: string;
  owner_id: number;
  is_active: boolean;
  webhook_registered: boolean;
  created_at: string;
  updated_at: string;
}

export interface BotConfigRow {
  id: number;
  bot_id: number;
  flow: Record<string, unknown>;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface MenuButtonData {
  text: string;
  callback?: string | null;
  url?: string | null;
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
}
