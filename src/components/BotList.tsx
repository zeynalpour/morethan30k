import type { BotRow } from "../lib/api";

const BOT_TYPE_ICONS: Record<string, string> = {
  generic: "🤖",
  hello: "👋",
  echo: "🔁",
  bridge: "🌉",
  ai_gateway: "🧠",
};

interface BotListProps {
  bots: BotRow[];
  currentBotId?: number;
  onSelect: (bot: BotRow) => void;
}

export function BotList({ bots, currentBotId, onSelect }: BotListProps) {
  return (
    <div className="px-4 py-4 space-y-3 animate-fadeIn">
      <h2 className="text-lg font-semibold mb-2" style={{ color: "var(--tg-text)" }}>
        Your Bots
      </h2>
      {bots.length === 0 ? (
        <p className="text-sm" style={{ color: "var(--tg-hint)" }}>
          No bots found.
        </p>
      ) : (
        bots.map((bot) => {
          const icon = BOT_TYPE_ICONS[bot.bot_type] || "🤖";
          const displayName = bot.title || bot.username || `Bot #${bot.id}`;
          const isCurrent = bot.id === currentBotId;

          return (
            <button
              key={bot.id}
              onClick={() => onSelect(bot)}
              className="w-full text-left rounded-xl p-3 flex items-center gap-3 transition-opacity"
              style={{
                background: isCurrent ? "var(--tg-button-bg)" : "var(--tg-secondary-bg)",
                color: isCurrent ? "var(--tg-button-text)" : "var(--tg-text)",
              }}
            >
              <div
                className="flex items-center justify-center rounded-full"
                style={{
                  width: 42,
                  height: 42,
                  background: isCurrent ? "rgba(255,255,255,0.15)" : "var(--tg-bg)",
                  fontSize: 22,
                  flexShrink: 0,
                }}
              >
                {icon}
              </div>
              <div className="flex-1 min-w-0">
                <div className="font-medium truncate">
                  {displayName}
                  {!bot.is_active && (
                    <span
                      className="text-[10px] ml-2 px-1.5 py-0.5 rounded-full uppercase tracking-wide"
                      style={{ background: "rgba(255,152,0,0.18)", color: "#ff9800" }}
                    >
                      Paused
                    </span>
                  )}
                </div>
                <div
                  className="text-xs mt-0.5"
                  style={{
                    color: isCurrent ? "rgba(255,255,255,0.7)" : "var(--tg-hint)",
                    opacity: bot.is_active ? 1 : 0.7,
                  }}
                >
                  {bot.username ? `@${bot.username}` : `ID: ${bot.id}`}
                  {isCurrent && " · Current"}
                </div>
              </div>
              <div className="flex-shrink-0 text-xl opacity-50">→</div>
            </button>
          );
        })
      )}
    </div>
  );
}
