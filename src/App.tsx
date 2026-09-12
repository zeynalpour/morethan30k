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
  const [isHome, setIsHome] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: "success" | "error" } | null>(null);

  const showToast = useCallback((msg: string, type: "success" | "error" = "success") => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3000);
  }, []);

  const haptic = useCallback((style: "light" | "medium" | "heavy" = "light") => {
    window.Telegram?.WebApp?.HapticFeedback?.impactOccurred(style);
  }, []);

  useEffect(() => {
    // Auth comes from Telegram itself: the Mini App's initData, signed with
    // the bot token. Without it (plain browser / unregistered domain), the
    // backend would reject us.
    const tg = window.Telegram?.WebApp;
    const initData =
      tg?.initData ||
      // Telegram injects initData into the URL hash (#tgWebAppData=...) on
      // the WebView's first load; read it directly in case the platform
      // script hasn't populated the object yet.
      new URLSearchParams(window.location.hash.slice(1)).get("tgWebAppData") ||
      new URLSearchParams(window.location.search).get("tgWebAppData") ||
      "";
    if (!initData) {
      const hashHint = window.location.hash
        ? ` hash present (${window.location.hash.slice(1, 40)}…)`
        : " no hash";
      setErrorMsg(
        `This dashboard opens only as a Mini App inside Telegram — tap “🚀 Open Dashboard” in the Main Bot, or use the 🚀 Dashboard menu button / bot-profile Mini App. If it still fails, fully restart the Telegram app. (URL: "${window.location.search}"${hashHint})`
      );
      setView("error");
      return;
    }
    setAuthToken(initData);

    const params = new URLSearchParams(window.location.search);
    const botIdParam = params.get("bid");

    if (!botIdParam) {
      // Home page: list every bot the user owns.
      setIsHome(true);
      (async () => {
        try {
          setBots(await api.listBots());
          setView("bots");
        } catch (e) {
          setErrorMsg(e instanceof Error ? e.message : "Failed to load your bots.");
          setView("error");
        }
      })();
      return;
    }

    const botId = parseInt(botIdParam, 10);
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
            : "Failed to load the dashboard. Please try again from the Main Bot."
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

  const handleToggleActive = useCallback(async () => {
    if (!bot) return;
    haptic("medium");
    setToggling(true);
    try {
      const updated = await api.updateBot(bot.id, { is_active: !bot.is_active });
      setBot(updated);
      showToast(
        updated.is_active ? "Bot enabled — it answers again" : "Bot disabled — messages ignored"
      );
      haptic("light");
    } catch (e) {
      showToast(e instanceof Error ? e.message : "Failed to update bot", "error");
      haptic("heavy");
    } finally {
      setToggling(false);
    }
  }, [bot, showToast, haptic]);

  const handleTypeChange = useCallback(
    async (newType: string) => {
      if (!bot || newType === bot.bot_type) return;
      haptic("medium");
      try {
        const updated = await api.updateBot(bot.id, { bot_type: newType });
        const flow = await api.getConfig(bot.id);
        setBot(updated);
        setConfig(wrapConfig(updated, flow));
        showToast(`Switched to ${newType} — config reset to defaults`);
        haptic("light");
      } catch (e) {
        showToast(e instanceof Error ? e.message : "Failed to switch bot type", "error");
        haptic("heavy");
      }
    },
    [bot, showToast, haptic]
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

  // Archive cleanup: a bot deleted in BotFather can't be revived (its token
  // is gone) — removing it just forgets the row in TME.
  const handleRemoveBot = useCallback(
    async (target: BotRow) => {
      const label = target.title || target.username || `Bot #${target.id}`;
      if (!window.confirm(`Remove ${label} from your dashboard? This cannot be undone.`)) {
        return;
      }
      haptic("medium");
      try {
        await api.deleteBot(target.id);
        setBots((prev) => prev.filter((b) => b.id !== target.id));
        showToast(`${label} removed`);
        haptic("light");
      } catch (e) {
        showToast(e instanceof Error ? e.message : "Failed to remove bot", "error");
        haptic("heavy");
      }
    },
    [showToast, haptic]
  );

  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    if (!tg) return;

    if (view === "bots" && !isHome) {
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
          <BotHeader bot={bot} onToggleActive={handleToggleActive} toggling={toggling} />
          <ConfigEditor
            config={config}
            bot={bot}
            onSave={handleSaveConfig}
            onTypeChange={handleTypeChange}
            onShowBots={() => {
              haptic("light");
              loadBots();
              setView("bots");
            }}
          />
        </>
      )}

      {view === "bots" && (
        <BotList
          bots={bots}
          currentBotId={bot?.id}
          onSelect={handleSwitchBot}
          onRemove={handleRemoveBot}
        />
      )}
    </div>
  );
}
