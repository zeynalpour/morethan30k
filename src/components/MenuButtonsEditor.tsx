import { useCallback } from "react";
import type { MenuButtonData } from "../lib/api";

interface MenuButtonsEditorProps {
  buttons: MenuButtonData[];
  onChange: (buttons: MenuButtonData[]) => void;
}

export function MenuButtonsEditor({ buttons, onChange }: MenuButtonsEditorProps) {
  const addButton = useCallback(() => {
    onChange([...buttons, { text: "", callback: "" }]);
  }, [buttons, onChange]);

  const removeButton = useCallback(
    (index: number) => {
      onChange(buttons.filter((_, i) => i !== index));
    },
    [buttons, onChange]
  );

  const updateButton = useCallback(
    (index: number, field: keyof MenuButtonData, value: string) => {
      const updated = [...buttons];
      updated[index] = { ...updated[index], [field]: value || null };
      onChange(updated);
    },
    [buttons, onChange]
  );

  const moveButton = useCallback(
    (index: number, direction: -1 | 1) => {
      const newIndex = index + direction;
      if (newIndex < 0 || newIndex >= buttons.length) return;
      const updated = [...buttons];
      [updated[index], updated[newIndex]] = [updated[newIndex], updated[index]];
      onChange(updated);
    },
    [buttons, onChange]
  );

  return (
    <div className="space-y-3">
      {buttons.map((btn, i) => (
        <div
          key={i}
          className="rounded-xl p-3 space-y-2"
          style={{ background: "var(--tg-secondary-bg)" }}
        >
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={btn.text}
              onChange={(e) => updateButton(i, "text", e.target.value)}
              placeholder="Button text"
              style={{ flex: 1 }}
            />
            <div className="flex gap-1 flex-shrink-0">
              <button
                onClick={() => moveButton(i, -1)}
                disabled={i === 0}
                className="px-2.5 py-2 rounded-lg text-sm"
                style={{ background: "var(--tg-bg)", color: "var(--tg-text)" }}
              >
                ↑
              </button>
              <button
                onClick={() => moveButton(i, 1)}
                disabled={i === buttons.length - 1}
                className="px-2.5 py-2 rounded-lg text-sm"
                style={{ background: "var(--tg-bg)", color: "var(--tg-text)" }}
              >
                ↓
              </button>
              <button
                onClick={() => removeButton(i)}
                className="px-2.5 py-2 rounded-lg text-sm"
                style={{ background: "#e5393520", color: "#e53935" }}
              >
                ✕
              </button>
            </div>
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              value={btn.callback || ""}
              onChange={(e) => updateButton(i, "callback", e.target.value)}
              placeholder="Callback (optional)"
              style={{ flex: 1 }}
            />
            <input
              type="text"
              value={btn.url || ""}
              onChange={(e) => updateButton(i, "url", e.target.value)}
              placeholder="URL (optional)"
              style={{ flex: 1 }}
            />
          </div>
        </div>
      ))}
      <button
        onClick={addButton}
        className="btn-secondary"
        style={{ border: "1px dashed var(--tg-hint)" }}
      >
        ➕ Add Button
      </button>
    </div>
  );
}
