import { useEffect, useState, useCallback } from "react";
import {
  supabase,
  type DashboardAuthToken,
  type BotRow,
  type BotConfigRow,
  type BotConfigFlow,
  type MenuButtonData,
} from "./lib/supabase";
import { BotHeader } from "./components/BotHeader";
import { ConfigEditor } from "./components/ConfigEditor";
import { BotList } from "./components/BotList";
import { Toast } from "./components/Toast";

type View = "loading" | "error" | "dashboard" | "bots";

declare global {
  interface Window {
    Telegram?: {
      WebApp: {
        ready: () => void;
        expand: () => void;
        themeParams: Record<string, string>;
        initData: string;
        initDataUnsafe: { user?: { id?: number } };
        BackButton: {
          show: () => void;
          hide: () => void;
          onClick: (cb: () => void) => void;
          offClick: (cb: () => void) => void;
        };
        HapticFeedback: {
          impactOccurred: (style: string) => void;
          notificationOccurred: (type: string) => void;
        };
        setHeaderColor?: (color: string) => void;
        setBackgroundColor?: (color: string) => void;
      };
    };
  }
}

export default function App() {
  const [view, setView] = useState<View>("loading");
  const [errorMsg, setErrorMsg] = useState("");
  const [authToken, setAuthToken] = useState<DashboardAuthToken | null>(null);
  const [bot, setBot] = useState<BotRow | null>(null);
  const [config, setConfig] = useState<BotConfigRow | null>(null);
  const [bots, setBots] = useState<BotRow[]>([]);
  const [toast, setToast] = useState<{ msg: string; type: "success" | "error" } | null>(null);

  const showToast = useCallback((msg: string, type: "success" | "error" = "success") => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3000);
  }, []);

  const haptic = useCallback((style: "light" | "medium" | "heavy" = "light") => {
    window.Telegram?.WebApp?.HapticFeedback?.impactOccurred(style);
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("t");
    const botIdParam = params.get("bid");

    if (!token || !botIdParam) {
      setErrorMsg("Missing authentication token. Please open this dashboard from the Main Bot.");
      setView("error");
      return;
    }

    const botId = parseInt(botIdParam, 10);

    (async () => {
      try {
        const { data: tokenRows, error: tokenError } = await supabase
          .from("dashboard_auth_tokens")
          .select("*")
          .eq("token", token)
          .limit(1);

        if (tokenError || !tokenRows || tokenRows.length === 0) {
          setErrorMsg("Invalid or expired token. Please request a new dashboard link from the Main Bot.");
          setView("error");
          return;
        }

        const tokenRow = tokenRows[0] as DashboardAuthToken;

        if (new Date(tokenRow.expires_at) < new Date()) {
          setErrorMsg("This dashboard link has expired. Please request a new one from the Main Bot.");
          setView("error");
          return;
        }

        if (tokenRow.bot_id !== botId) {
          setErrorMsg("Token does not match this bot.");
          setView("error");
          return;
        }

        setAuthToken(tokenRow);

        const { data: botData, error: botError } = await supabase
          .from("bots")
          .select("*")
          .eq("id", botId)
          .maybeSingle();

        if (botError || !botData) {
          setErrorMsg("Bot not found.");
          setView("error");
          return;
        }

        setBot(botData as BotRow);

        const { data: cfgData, error: cfgError } = await supabase
          .from("bot_configs")
          .select("*")
          .eq("bot_id", botId)
          .maybeSingle();

        if (cfgError || !cfgData) {
          setErrorMsg("Bot configuration not found.");
          setView("error");
          return;
        }

        setConfig(cfgData as BotConfigRow);
        setView("dashboard");
      } catch {
        setErrorMsg("Failed to load dashboard. Please try again.");
        setView("error");
      }
    })();
  }, []);

  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    if (tg) {
      tg.ready();
      tg.expand();
    }
  }, []);

  const loadBots = useCallback(async () => {
    if (!authToken || !bot) return;
    const { data, error } = await supabase
      .from("bots")
      .select("*")
      .eq("owner_id", bot.owner_id)
      .order("created_at", { ascending: false });
    if (!error && data) {
      setBots(data as BotRow[]);
    }
  }, [authToken, bot]);

  const handleSaveConfig = useCallback(
    async (newFlow: BotConfigFlow) => {
      if (!config || !bot) return;
      haptic("medium");

      const { error } = await supabase
        .from("bot_configs")
        .update({ flow: newFlow, updated_at: new Date().toISOString() })
        .eq("bot_id", bot.id);

      if (error) {
        showToast("Failed to save configuration", "error");
        haptic("heavy");
      } else {
        setConfig({ ...config, flow: newFlow as Record<string, unknown> });
        showToast("Configuration saved successfully!");
        haptic("light");
      }
    },
    [config, bot, showToast, haptic]
  );

  const handleSwitchBot = useCallback((newBot: BotRow) => {
    setBot(newBot);
    setView("loading");
    (async () => {
      const { data, error } = await supabase
        .from("bot_configs")
        .select("*")
        .eq("bot_id", newBot.id)
        .maybeSingle();
      if (!error && data) {
        setConfig(data as BotConfigRow);
        setView("dashboard");
      } else {
        setErrorMsg("Failed to load bot configuration.");
        setView("error");
      }
    })();
  }, []);

  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    if (!tg) return;

    if (view === "bots") {
      tg.BackButton.show();
      const backHandler = () => setView("dashboard");
      tg.BackButton.onClick(backHandler);
      return () => {
        tg.BackButton.offClick(backHandler);
        tg.BackButton.hide();
      };
    }
  }, [view]);

  if (view === "loading") {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-4">
        <div className="spinner" />
        <p style={{ color: "var(--tg-hint)" }}>Loading dashboard...</p>
      </div>
    );
  }

  if (view === "error") {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen px-6 text-center gap-4">
        <div className="text-5xl">⚠️</div>
        <p className="text-lg font-medium">{errorMsg}</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen pb-8 animate-fadeIn">
      {toast && <Toast message={toast.msg} type={toast.type} />}

      {view === "dashboard" && bot && config && (
        <>
          <BotHeader bot={bot} />
          <ConfigEditor
            config={config}
            bot={bot}
            onSave={handleSaveConfig}
            onShowBots={() => {
              haptic("light");
              loadBots();
              setView("bots");
            }}
          />
        </>
      )}

      {view === "bots" && (
        <BotList bots={bots} currentBotId={bot?.id} onSelect={handleSwitchBot} />
      )}
    </div>
  );
}
