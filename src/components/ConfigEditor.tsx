import { useState, useCallback } from "react";
import type { BotRow, BotConfigRow, BotConfigFlow, MenuButtonData } from "../lib/api";
import { MenuButtonsEditor } from "./MenuButtonsEditor";

interface ConfigEditorProps {
  config: BotConfigRow;
  bot: BotRow;
  onSave: (flow: BotConfigFlow) => void;
  onShowBots: () => void;
}

export function ConfigEditor({ config, bot, onSave, onShowBots }: ConfigEditorProps) {
  const flow = config.flow as BotConfigFlow;
  const [welcomeMessage, setWelcomeMessage] = useState(flow.welcome_message || "");
  const [fallbackMessage, setFallbackMessage] = useState(flow.fallback_message || "");
  const [greeting, setGreeting] = useState(flow.greeting || "");
  const [echoPrefix, setEchoPrefix] = useState(flow.echo_prefix || "");
  const [menuButtons, setMenuButtons] = useState<MenuButtonData[]>(
    Array.isArray(flow.menu_buttons) ? flow.menu_buttons : []
  );
  const [saving, setSaving] = useState(false);

  const isHello = bot.bot_type === "hello";
  const isEcho = bot.bot_type === "echo";

  const handleSave = useCallback(async () => {
    setSaving(true);
    const newFlow: BotConfigFlow = {
      ...flow,
      bot_type: flow.bot_type || "generic",
      version: flow.version || 1,
      welcome_message: welcomeMessage,
      fallback_message: fallbackMessage,
      menu_buttons: menuButtons,
    };
    if (isHello) newFlow.greeting = greeting;
    if (isEcho) newFlow.echo_prefix = echoPrefix;
    onSave(newFlow);
    setSaving(false);
  }, [flow, welcomeMessage, fallbackMessage, menuButtons, greeting, echoPrefix, isHello, isEcho, onSave]);

  return (
    <div className="px-4 py-4 space-y-5">
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

      <Section title="Fallback Message" subtitle="Reply when the bot doesn't understand a message">
        <textarea
          value={fallbackMessage}
          onChange={(e) => setFallbackMessage(e.target.value)}
          placeholder="🤖 Sorry, I didn't understand that."
          rows={2}
        />
      </Section>

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
