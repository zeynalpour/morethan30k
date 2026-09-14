import { useCallback } from "react";
import type { FlowStepData, StepOptionData, StepTranslation } from "../lib/api";

// Issue #23 — the flow editor renders EVERY step of a multi-step `steps` flow
// (not just the first), each one editable and translatable per language.
//
// The steps live in the flow as plain data (an `extra="allow"` rider on
// BotConfigBase), so this component writes through the same config API and the
// same cache invalidation as every other field — no per-template code.
//
// Language-independent on purpose: the step `id`, its `answer_type`, its
// `correct_answers` and each option `value` are NOT translatable (a
// translation may never re-route or re-score a flow). Only the prompt and the
// option labels have a per-language editor.

const ANSWER_TYPES: { value: string; label: string }[] = [
  { value: "auto", label: "Auto (choice when options exist, else free text)" },
  { value: "free_text", label: "Free text answer" },
  { value: "none", label: "None — result / terminal step" },
];

function answerTypeOf(step: FlowStepData): string {
  return step.answer_type || "auto";
}

function isTerminal(step: FlowStepData): boolean {
  return answerTypeOf(step) === "none";
}

interface FlowStepsEditorProps {
  steps: FlowStepData[];
  onChange: (steps: FlowStepData[]) => void;
}

export function FlowStepsEditor({ steps, onChange }: FlowStepsEditorProps) {
  const updateStep = useCallback(
    (index: number, patch: Partial<FlowStepData>) => {
      const updated = [...steps];
      updated[index] = { ...updated[index], ...patch };
      onChange(updated);
    },
    [steps, onChange]
  );

  const updateOption = useCallback(
    (stepIndex: number, optionIndex: number, patch: Partial<StepOptionData>) => {
      const options = [...(steps[stepIndex].options || [])];
      options[optionIndex] = { ...options[optionIndex], ...patch };
      updateStep(stepIndex, { options });
    },
    [steps, updateStep]
  );

  const addOption = useCallback(
    (stepIndex: number) => {
      const options = [...(steps[stepIndex].options || []), { label: "", value: "" }];
      updateStep(stepIndex, { options });
    },
    [steps, updateStep]
  );

  const removeOption = useCallback(
    (stepIndex: number, optionIndex: number) => {
      const options = (steps[stepIndex].options || []).filter((_, i) => i !== optionIndex);
      updateStep(stepIndex, { options });
    },
    [steps, updateStep]
  );

  return (
    <div className="space-y-3">
      {steps.map((step, i) => (
        <div
          key={`${step.id}-${i}`}
          className="rounded-xl p-3 space-y-2"
          style={{ background: "var(--tg-secondary-bg)" }}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium" style={{ color: "var(--tg-text)" }}>
              Step {i + 1} · <b>{step.id}</b>
            </span>
            {isTerminal(step) && (
              <span
                className="text-xs px-2 py-0.5 rounded-full flex-shrink-0"
                style={{ background: "var(--tg-bg)", color: "var(--tg-hint)" }}
              >
                🏁 Result step
              </span>
            )}
          </div>

          <label className="block">
            <span className="text-xs" style={{ color: "var(--tg-hint)" }}>
              Prompt
            </span>
            <textarea
              rows={2}
              value={step.prompt || ""}
              onChange={(e) => updateStep(i, { prompt: e.target.value })}
              placeholder="What should the bot ask?"
            />
          </label>

          <label className="block">
            <span className="text-xs" style={{ color: "var(--tg-hint)" }}>
              Answer type
            </span>
            <select
              value={answerTypeOf(step)}
              onChange={(e) => updateStep(i, { answer_type: e.target.value })}
            >
              {ANSWER_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>

          {!isTerminal(step) && (
            <div>
              <span className="text-xs" style={{ color: "var(--tg-hint)" }}>
                Options {step.options && step.options.length > 0 ? "" : "(none — free text)"}
              </span>
              <div className="space-y-2 mt-1">
                {(step.options || []).map((opt, j) => (
                  <div key={j} className="flex items-center gap-2">
                    <input
                      type="text"
                      value={opt.label}
                      onChange={(e) => updateOption(i, j, { label: e.target.value })}
                      placeholder="Label (what the user sees)"
                      style={{ flex: 2 }}
                    />
                    <input
                      type="text"
                      value={opt.value}
                      onChange={(e) => updateOption(i, j, { value: e.target.value })}
                      placeholder="Value (answer key)"
                      style={{ flex: 2 }}
                    />
                    <button
                      onClick={() => removeOption(i, j)}
                      className="px-2.5 py-2 rounded-lg text-sm flex-shrink-0"
                      style={{ background: "#e5393520", color: "#e53935" }}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
              <button
                onClick={() => addOption(i)}
                className="btn-secondary w-full mt-2"
                style={{ border: "1px dashed var(--tg-hint)" }}
              >
                ➕ Add option
              </button>
            </div>
          )}

          {!isTerminal(step) && (
            <label className="block">
              <span className="text-xs" style={{ color: "var(--tg-hint)" }}>
                Correct answers (comma-separated option values — leave empty when
                nothing is graded)
              </span>
              <input
                type="text"
                value={(step.correct_answers || []).join(", ")}
                onChange={(e) =>
                  updateStep(i, {
                    correct_answers: e.target.value
                      .split(",")
                      .map((v) => v.trim())
                      .filter(Boolean),
                  })
                }
                placeholder="paris, mars"
              />
            </label>
          )}
        </div>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- #
// Per-language step copy (the `translations.<code>.steps` map)
// --------------------------------------------------------------------------- #
interface StepTranslationsEditorProps {
  steps: FlowStepData[];
  value: Record<string, StepTranslation>;
  onChange: (translations: Record<string, StepTranslation>) => void;
}

export function StepTranslationsEditor({ steps, value, onChange }: StepTranslationsEditorProps) {
  const setPrompt = useCallback(
    (stepId: string, prompt: string) => {
      const entry = value[stepId] || {};
      onChange({ ...value, [stepId]: { ...entry, prompt } });
    },
    [value, onChange]
  );

  const setLabel = useCallback(
    (stepId: string, index: number, label: string) => {
      const entry = value[stepId] || {};
      const options = [...(entry.options || [])];
      // Keep the list positionally aligned with the step's options.
      while (options.length <= index) options.push("");
      options[index] = label;
      onChange({ ...value, [stepId]: { ...entry, options } });
    },
    [value, onChange]
  );

  const removeEntry = useCallback(
    (stepId: string) => {
      const next = { ...value };
      delete next[stepId];
      onChange(next);
    },
    [value, onChange]
  );

  const knownIds = new Set(steps.map((s) => s.id));
  // An override for a step the flow no longer has is kept (the backend stores
  // it too) — shown here so it can be reviewed or removed, never silently lost.
  const orphanIds = Object.keys(value).filter((id) => !knownIds.has(id));

  return (
    <div className="space-y-3">
      {steps.map((step) => {
        const entry = value[step.id] || {};
        return (
          <div key={step.id} className="space-y-2">
            <span className="text-xs font-medium" style={{ color: "var(--tg-text)" }}>
              Step · <b>{step.id}</b>
            </span>
            <textarea
              rows={2}
              value={entry.prompt || ""}
              onChange={(e) => setPrompt(step.id, e.target.value)}
              placeholder={step.prompt}
              aria-label={`${step.id} prompt`}
            />
            {(step.options || []).map((opt, j) => (
              <input
                key={j}
                type="text"
                value={(entry.options || [])[j] || ""}
                onChange={(e) => setLabel(step.id, j, e.target.value)}
                placeholder={opt.label}
                aria-label={`${step.id} option ${j + 1}`}
              />
            ))}
          </div>
        );
      })}

      {orphanIds.length > 0 && (
        <div className="space-y-2 pt-1">
          <span className="text-xs" style={{ color: "var(--tg-hint)" }}>
            Kept for steps no longer in this flow
          </span>
          {orphanIds.map((id) => (
            <div key={id} className="flex items-center gap-2">
              <span className="text-xs flex-1" style={{ color: "var(--tg-hint)" }}>
                {id} — {value[id]?.prompt || "(options only)"}
              </span>
              <button
                className="text-xs flex-shrink-0"
                style={{ color: "var(--tg-hint)" }}
                onClick={() => removeEntry(id)}
              >
                ✕ Remove
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
