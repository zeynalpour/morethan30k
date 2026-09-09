import type { BotRow } from "../lib/api";

const BOT_TYPE_LABELS: Record<string, string> = {
  generic: "Generic",
  hello: "Hello Bot",
  echo: "Echo Bot",
  bridge: "Bridge Bot",
  ai_gateway: "AI Gateway",
};

const BOT_TYPE_ICONS: Record<string, string> = {
  generic: "🤖",
  hello: "👋",
  echo: "🔁",
  bridge: "🌉",
  ai_gateway: "🧠",
};

interface BotHeaderProps {
  bot: BotRow;
  onToggleActive: () => void;
  toggling: boolean;
}

export function BotHeader({ bot, onToggleActive, toggling }: BotHeaderProps) {
  const icon = BOT_TYPE_ICONS[bot.bot_type] || "🤖";
  const typeLabel = BOT_TYPE_LABELS[bot.bot_type] || bot.bot_type;
  const displayName = bot.title || bot.username || `Bot #${bot.id}`;

  return (
    <div style={{ borderBottom: "1px solid var(--tg-hint)" }}>
      <div className="px-4 pt-5 pb-4 flex items-center gap-3">
        <div
          className="flex items-center justify-center rounded-full"
          style={{
            width: 52,
            height: 52,
            background: "var(--tg-secondary-bg)",
            fontSize: 26,
            flexShrink: 0,
          }}
        >
          {icon}
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold truncate" style={{ color: "var(--tg-text)" }}>
            {displayName}
          </h1>
          <div className="flex items-center gap-2 mt-0.5">
            <span
              className="text-xs px-2 py-0.5 rounded-full"
              style={{ background: "var(--tg-secondary-bg)", color: "var(--tg-hint)" }}
            >
              {typeLabel}
            </span>
            {bot.username && (
              <span className="text-xs" style={{ color: "var(--tg-hint)" }}>
                @{bot.username}
              </span>
            )}
          </div>
        </div>
        {/* Enable / disable switch */}
        <button
          onClick={onToggleActive}
          disabled={toggling}
          className="flex items-center gap-1.5 text-xs rounded-full px-3 py-1.5"
          style={{
            background: bot.is_active ? "rgba(76,175,80,0.15)" : "var(--tg-secondary-bg)",
            color: bot.is_active ? "#4caf50" : "var(--tg-hint)",
            border: `1px solid ${bot.is_active ? "rgba(76,175,80,0.4)" : "var(--tg-hint)"}`,
            flexShrink: 0,
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: bot.is_active ? "#4caf50" : "var(--tg-hint)",
            }}
          />
          {toggling ? "…" : bot.is_active ? "Active" : "Disabled"}
        </button>
      </div>

      <div
        className="px-4 pb-3 flex items-center gap-4 text-xs"
        style={{ color: "var(--tg-hint)" }}
      >
        <span>
          Webhook:{" "}
          <span style={{ color: bot.webhook_registered ? "#4caf50" : "var(--tg-text)" }}>
            {bot.webhook_registered ? "✓ registered" : "⚠ pending"}
          </span>
        </span>
        <span>Created: {new Date(bot.created_at).toLocaleDateString()}</span>
      </div>
    </div>
  );
}
