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
  onRemove?: (bot: BotRow) => void;
}

function DisplayName({ bot }: { bot: BotRow }) {
  return <>{bot.title || bot.username || `Bot #${bot.id}`}</>;
}

export function BotList({ bots, currentBotId, onSelect, onRemove }: BotListProps) {
  // Bots Telegram no longer knows about (deleted in BotFather) leave the
  // working list and live in the archive below — visible, but not
  // masquerading as a live bot.
  const visible = bots.filter((bot) => bot.state !== "archived");
  const archived = bots.filter((bot) => bot.state === "archived");

  return (
    <div className="px-4 py-4 space-y-3 animate-fadeIn">
      <h2 className="text-lg font-semibold mb-2" style={{ color: "var(--tg-text)" }}>
        Your Bots
      </h2>
      {visible.length === 0 ? (
        <p className="text-sm" style={{ color: "var(--tg-hint)" }}>
          No bots found.
        </p>
      ) : (
        visible.map((bot) => {
          const icon = BOT_TYPE_ICONS[bot.bot_type] || "🤖";
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
                  <DisplayName bot={bot} />
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

      {archived.length > 0 && (
        <div className="pt-4 space-y-2">
          <h3 className="text-sm font-semibold" style={{ color: "var(--tg-hint)" }}>
            🗄 Archived ({archived.length})
          </h3>
          <p className="text-xs" style={{ color: "var(--tg-hint)" }}>
            These bots were deleted in BotFather, so Telegram no longer accepts their
            token. Remove them to clean up your dashboard.
          </p>
          {archived.map((bot) => (
            <div
              key={bot.id}
              className="rounded-xl p-3 flex items-center gap-3"
              style={{ background: "var(--tg-secondary-bg)", opacity: 0.75 }}
            >
              <div
                className="flex items-center justify-center rounded-full"
                style={{
                  width: 42,
                  height: 42,
                  background: "var(--tg-bg)",
                  fontSize: 22,
                  flexShrink: 0,
                }}
              >
                🗑
              </div>
              <div className="flex-1 min-w-0">
                <div className="font-medium truncate" style={{ color: "var(--tg-text)" }}>
                  <DisplayName bot={bot} />
                  <span
                    className="text-[10px] ml-2 px-1.5 py-0.5 rounded-full uppercase tracking-wide"
                    style={{ background: "rgba(244,67,54,0.18)", color: "#ef5350" }}
                  >
                    Deleted
                  </span>
                </div>
                <div className="text-xs mt-0.5" style={{ color: "var(--tg-hint)" }}>
                  {bot.username ? `@${bot.username}` : `ID: ${bot.id}`}
                </div>
              </div>
              {onRemove && (
                <button
                  onClick={() => onRemove(bot)}
                  className="flex-shrink-0 text-sm px-3 py-1.5 rounded-lg"
                  style={{ background: "rgba(244,67,54,0.15)", color: "#ef5350" }}
                >
                  Remove
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
