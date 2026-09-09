import { useEffect, useState, useCallback } from "react";
import {
  api,
  setAuthToken,
  wrapConfig,
  type BotRow,
  type BotConfigRow,
  type BotConfigFlow,
} from "./lib/api";
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
    setAuthToken(token);

    (async () => {
      try {
        const botData = await api.getBot(botId);
        const flow = await api.getConfig(botId);
        setBot(botData);
        setConfig(wrapConfig(botData, flow));
        setView("dashboard");
      } catch (e) {
        setErrorMsg(
          e instanceof Error
            ? e.message
            : "Failed to load dashboard. Please request a new link from the Main Bot."
        );
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
    try {
      const data = await api.listBots();
      setBots(data);
    } catch (e) {
      showToast(e instanceof Error ? e.message : "Failed to load your bots", "error");
    }
  }, [showToast]);

  const handleSaveConfig = useCallback(
    async (newFlow: BotConfigFlow) => {
      if (!config || !bot) return;
      haptic("medium");

      try {
        const saved = await api.saveConfig(bot.id, newFlow);
        setConfig(wrapConfig(bot, saved));
        showToast("Configuration saved successfully!");
        haptic("light");
      } catch (e) {
        showToast(e instanceof Error ? e.message : "Failed to save configuration", "error");
        haptic("heavy");
      }
    },
    [config, bot, showToast, haptic]
  );

  const handleSwitchBot = useCallback((newBot: BotRow) => {
    setBot(newBot);
    setView("loading");
    (async () => {
      try {
        const flow = await api.getConfig(newBot.id);
        setConfig(wrapConfig(newBot, flow));
        setView("dashboard");
      } catch (e) {
        setErrorMsg(
          e instanceof Error ? e.message : "Failed to load bot configuration."
        );
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
