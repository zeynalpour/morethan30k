import { useState, useCallback } from "react";
import type { BotRow, BotConfigRow, BotConfigFlow, MenuButtonData, Translation } from "../lib/api";
import { MenuButtonsEditor } from "./MenuButtonsEditor";

interface ConfigEditorProps {
  config: BotConfigRow;
  bot: BotRow;
  onSave: (flow: BotConfigFlow) => void;
  onTypeChange: (botType: string) => void;
  onShowBots: () => void;
}

export function ConfigEditor({ config, bot, onSave, onTypeChange, onShowBots }: ConfigEditorProps) {
  const flow = config.flow as BotConfigFlow;
  const [welcomeMessage, setWelcomeMessage] = useState(flow.welcome_message || "");
  const [fallbackMessage, setFallbackMessage] = useState(flow.fallback_message || "");
  const [greeting, setGreeting] = useState(flow.greeting || "");
  const [echoPrefix, setEchoPrefix] = useState(flow.echo_prefix || "");
  const [activeModules, setActiveModules] = useState(
    Array.isArray(flow.active_modules) ? flow.active_modules.join(", ") : ""
  );
  const [translations, setTranslations] = useState<Record<string, Translation>>(
    flow.translations || {}
  );
  const [activeLang, setActiveLang] = useState<string | null>(
    Object.keys(flow.translations || {})[0] ?? null
  );
  const [newLang, setNewLang] = useState("");
  const [menuButtons, setMenuButtons] = useState<MenuButtonData[]>(
    Array.isArray(flow.menu_buttons) ? flow.menu_buttons : []
  );
  const [saving, setSaving] = useState(false);

  const isHello = bot.bot_type === "hello";
  const isEcho = bot.bot_type === "echo";

  const addLang = useCallback(() => {
    const code = newLang.trim().toLowerCase().replace(/[^a-z]/g, "");
    if (!code) return;
    setTranslations((prev) => ({ ...prev, [code]: prev[code] || {} }));
    setActiveLang(code);
    setNewLang("");
  }, [newLang]);

  const removeLang = useCallback(() => {
    if (!activeLang) return;
    setTranslations((prev) => {
      const next = { ...prev };
      delete next[activeLang];
      return next;
    });
    setActiveLang(null);
  }, [activeLang]);

  const setLangField = useCallback(
    (field: keyof Translation, value: string | MenuButtonData[]) => {
      if (!activeLang) return;
      setTranslations((prev) => ({
        ...prev,
        [activeLang]: { ...(prev[activeLang] || {}), [field]: value },
      }));
    },
    [activeLang]
  );

  const copyFromBase = useCallback(() => {
    if (!activeLang) return;
    setTranslations((prev) => {
      const cur = prev[activeLang] || {};
      const merged: Translation = { ...cur };
      if (!merged.welcome_message) merged.welcome_message = welcomeMessage;
      if (!merged.fallback_message) merged.fallback_message = fallbackMessage;
      if (isHello && !merged.greeting) merged.greeting = greeting;
      if (isEcho && !merged.echo_prefix) merged.echo_prefix = echoPrefix;
      if (!merged.menu_buttons || merged.menu_buttons.length === 0) {
        merged.menu_buttons = menuButtons;
      }
      return { ...prev, [activeLang]: merged };
    });
  }, [activeLang, welcomeMessage, fallbackMessage, greeting, echoPrefix, menuButtons, isHello, isEcho]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    // Drop empty languages and empty fields (empty string = "unset").
    const cleanedTranslations: Record<string, Translation> = {};
    for (const [lang, tr] of Object.entries(translations)) {
      const cleaned: Translation = {};
      for (const [field, value] of Object.entries(tr)) {
        if (Array.isArray(value) ? value.length > 0 : value !== undefined && value !== "") {
          cleaned[field as keyof Translation] = value;
        }
      }
      if (Object.keys(cleaned).length > 0) cleanedTranslations[lang] = cleaned;
    }

    const newFlow: BotConfigFlow = {
      ...flow,
      bot_type: flow.bot_type || "generic",
      version: flow.version || 1,
      welcome_message: welcomeMessage,
      fallback_message: fallbackMessage,
      menu_buttons: menuButtons,
      active_modules: activeModules
        .split(",")
        .map((m) => m.trim())
        .filter(Boolean),
      translations: cleanedTranslations,
    };
    if (isHello) newFlow.greeting = greeting;
    if (isEcho) newFlow.echo_prefix = echoPrefix;
    onSave(newFlow);
    setSaving(false);
  }, [flow, welcomeMessage, fallbackMessage, menuButtons, activeModules, translations, greeting, echoPrefix, isHello, isEcho, onSave]);

  return (
    <div className="px-4 py-4 space-y-5">
      <Section
        title="Bot Type"
        subtitle="Switching type resets this bot's configuration to that type's defaults"
      >
        <select
          value={bot.bot_type}
          onChange={(e) => {
            if (e.target.value !== bot.bot_type) onTypeChange(e.target.value);
          }}
        >
          <option value="generic">Generic</option>
          <option value="hello">Hello Bot</option>
          <option value="echo">Echo Bot</option>
        </select>
      </Section>

      <Section title="Welcome Message" subtitle="Sent when a user starts the bot with /start">
        <textarea
          value={welcomeMessage}
          onChange={(e) => setWelcomeMessage(e.target.value)}
          placeholder="👋 Welcome to my bot!"
          rows={3}
        />
      </Section>

      {isHello && (
        <Section title="Greeting" subtitle="Custom greeting for your Hello bot">
          <textarea
            value={greeting}
            onChange={(e) => setGreeting(e.target.value)}
            placeholder="Hello there! 👋"
            rows={2}
          />
        </Section>
      )}

      {isEcho && (
        <Section title="Echo Prefix" subtitle="Text added before each echoed message">
          <input
            type="text"
            value={echoPrefix}
            onChange={(e) => setEchoPrefix(e.target.value)}
            placeholder="🔁 "
          />
        </Section>
      )}

      <Section title="Menu Buttons" subtitle="Inline buttons shown under the welcome message">
        <MenuButtonsEditor buttons={menuButtons} onChange={setMenuButtons} />
      </Section>

      <Section title="Active Modules" subtitle="Comma-separated feature flags enabled for this bot">
        <input
          type="text"
          value={activeModules}
          onChange={(e) => setActiveModules(e.target.value)}
          placeholder="module-a, module-b"
        />
      </Section>

      <Section
        title="Translations"
        subtitle="Users see the bot in their Telegram language — per field: user language → English → base"
      >
        <div className="flex flex-wrap gap-1.5 items-center">
          {Object.keys(translations).map((lang) => (
            <button
              key={lang}
              onClick={() => setActiveLang(lang)}
              className="text-xs px-2.5 py-1 rounded-full"
              style={{
                background: activeLang === lang ? "var(--tg-button-bg)" : "var(--tg-secondary-bg)",
                color: activeLang === lang ? "var(--tg-button-text)" : "var(--tg-text)",
              }}
            >
              {lang}
            </button>
          ))}
          <input
            type="text"
            value={newLang}
            onChange={(e) => setNewLang(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") addLang();
            }}
            onBlur={addLang}
            placeholder="+ code (e.g. fa)"
            className="w-28 text-xs"
          />
        </div>

        {activeLang && (
          <div
            className="mt-3 p-3 rounded-xl space-y-3"
            style={{ background: "var(--tg-secondary-bg)" }}
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium" style={{ color: "var(--tg-text)" }}>
                Editing: <b>{activeLang}</b>
              </span>
              <button
                className="text-xs"
                style={{ color: "var(--tg-hint)" }}
                onClick={removeLang}
              >
                ✕ Remove
              </button>
            </div>

            <label className="block">
              <span className="text-xs" style={{ color: "var(--tg-hint)" }}>
                Welcome message
              </span>
              <textarea
                rows={2}
                value={translations[activeLang]?.welcome_message || ""}
                onChange={(e) => setLangField("welcome_message", e.target.value)}
                placeholder={welcomeMessage}
              />
            </label>

            {isHello && (
              <label className="block">
                <span className="text-xs" style={{ color: "var(--tg-hint)" }}>
                  Greeting
                </span>
                <textarea
                  rows={2}
                  value={translations[activeLang]?.greeting || ""}
                  onChange={(e) => setLangField("greeting", e.target.value)}
                  placeholder={greeting}
                />
              </label>
            )}

            {isEcho && (
              <label className="block">
                <span className="text-xs" style={{ color: "var(--tg-hint)" }}>
                  Echo prefix
                </span>
                <input
                  type="text"
                  value={translations[activeLang]?.echo_prefix || ""}
                  onChange={(e) => setLangField("echo_prefix", e.target.value)}
                  placeholder={echoPrefix}
                />
              </label>
            )}

            <label className="block">
              <span className="text-xs" style={{ color: "var(--tg-hint)" }}>
                Fallback message
              </span>
              <textarea
                rows={2}
                value={translations[activeLang]?.fallback_message || ""}
                onChange={(e) => setLangField("fallback_message", e.target.value)}
                placeholder={fallbackMessage}
              />
            </label>

            <div>
              <span className="text-xs" style={{ color: "var(--tg-hint)" }}>
                Menu buttons
              </span>
              <MenuButtonsEditor
                buttons={translations[activeLang]?.menu_buttons || []}
                onChange={(b) => setLangField("menu_buttons", b)}
              />
            </div>

            <button className="btn-secondary w-full" onClick={copyFromBase}>
              📋 Copy from base
            </button>
          </div>
        )}
      </Section>

      <Section title="Fallback Message" subtitle="Reply when the bot doesn't understand a message">
        <textarea
          value={fallbackMessage}
          onChange={(e) => setFallbackMessage(e.target.value)}
          placeholder="🤖 Sorry, I didn't understand that."
          rows={2}
        />
      </Section>

      {!bot.is_active && (
        <div
          className="text-xs px-3 py-2 rounded-lg"
          style={{ background: "rgba(255,152,0,0.12)", color: "#ff9800" }}
        >
          ⏸ This bot is disabled — users' messages are not answered. Enable it in the header to
          resume.
        </div>
      )}

      <div className="pt-2">
        <button className="btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? "Saving..." : "Save Changes"}
        </button>
      </div>

      <div className="pt-2">
        <button className="btn-secondary" onClick={onShowBots}>
          🔄 Switch Bot
        </button>
      </div>
    </div>
  );
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2">
      <div>
        <h2 className="text-sm font-semibold" style={{ color: "var(--tg-text)" }}>
          {title}
        </h2>
        {subtitle && (
          <p className="text-xs mt-0.5" style={{ color: "var(--tg-hint)" }}>
            {subtitle}
          </p>
        )}
      </div>
      {children}
    </div>
  );
}
