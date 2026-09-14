import { useState, useCallback } from "react";
import type {
  BotRow,
  BotConfigRow,
  BotConfigFlow,
  FlowStepData,
  MenuButtonData,
  StepTranslation,
  Translation,
} from "../lib/api";
import { LANGUAGE_OPTIONS, languageLabel } from "../lib/languages";
import { FlowStepsEditor, StepTranslationsEditor } from "./FlowStepsEditor";
import { MenuButtonsEditor } from "./MenuButtonsEditor";
import { TemplatePanel } from "./TemplatePanel";

interface ConfigEditorProps {
  config: BotConfigRow;
  bot: BotRow;
  onSave: (flow: BotConfigFlow) => void;
  onTypeChange: (botType: string) => void;
  onShowBots: () => void;
  onRecloned: () => void;
  onPanelError: (msg: string) => void;
}

// Blank = "unset" (the same rule the backend applies to copy): a cleared step
// prompt keeps the step's existing question rather than storing "" — the
// steps engine drops a step whose prompt is empty, which would silently
// delete the question for every user. Optional keys that carry no data are
// dropped instead of written back as []/"auto", so saving an untouched step
// never rewrites the flow the template shipped.
function cleanSteps(steps: FlowStepData[], original: FlowStepData[]): FlowStepData[] {
  const previous = new Map(original.map((step) => [step.id, step]));
  return steps
    .map((step) => {
      const before = previous.get(step.id);
      const prompt = (step.prompt || "").trim() || (before?.prompt || "").trim();
      // An option needs both halves: the label the user taps and the value
      // that is stored/scored. Either one alone completes the other instead
      // of saving an option the engine would reject.
      const options = (step.options || [])
        .map((opt) => ({
          label: (opt.label || "").trim() || (opt.value || "").trim(),
          value: (opt.value || "").trim() || (opt.label || "").trim(),
        }))
        .filter((opt) => opt.label && opt.value);
      const correct = (step.correct_answers || []).map((value) => value.trim()).filter(Boolean);
      const answerType = step.answer_type || "auto";

      const cleaned: FlowStepData = { ...step, prompt };
      if (options.length > 0) cleaned.options = options;
      else delete cleaned.options;
      if (correct.length > 0) cleaned.correct_answers = correct;
      else delete cleaned.correct_answers;
      if (answerType !== "auto" || before?.answer_type) cleaned.answer_type = answerType;
      else delete cleaned.answer_type;
      return cleaned;
    })
    .filter((step) => step.prompt);
}

// Per-language step copy. Blank prompts/values are dropped (never sent), and
// option labels keep their POSITION: a label is matched to the step's option
// at the same index, so interior blanks stay as placeholders while trailing
// blanks are trimmed.
function cleanStepTranslations(
  copy: Record<string, StepTranslation> | undefined
): Record<string, StepTranslation> {
  const cleaned: Record<string, StepTranslation> = {};
  if (!copy) return cleaned;

  for (const [stepId, entry] of Object.entries(copy)) {
    const next: StepTranslation = {};
    const prompt = (entry.prompt || "").trim();
    if (prompt) next.prompt = prompt;

    const labels = (entry.options || []).map((label) => (label || "").trim());
    while (labels.length > 0 && labels[labels.length - 1] === "") labels.pop();
    if (labels.length > 0) next.options = labels;

    if (Object.keys(next).length > 0) cleaned[stepId] = next;
  }
  return cleaned;
}

export function ConfigEditor({ config, bot, onSave, onTypeChange, onShowBots, onRecloned, onPanelError }: ConfigEditorProps) {
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
  const [singleLanguage, setSingleLanguage] = useState(!!flow.single_language);
  const [mainLanguage, setMainLanguage] = useState(flow.main_language || "");
  const [steps, setSteps] = useState<FlowStepData[]>(
    Array.isArray(flow.steps) ? flow.steps : []
  );
  const [menuButtons, setMenuButtons] = useState<MenuButtonData[]>(
    Array.isArray(flow.menu_buttons) ? flow.menu_buttons : []
  );
  const [saving, setSaving] = useState(false);

  const isHello = bot.bot_type === "hello";
  const isEcho = bot.bot_type === "echo";
  const hasSteps = steps.length > 0;
  // The base copy the steps engine will ship when a translation is absent.
  const baseSteps: FlowStepData[] = Array.isArray(flow.steps) ? flow.steps : [];

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
    (patch: Partial<Translation>) => {
      if (!activeLang) return;
      setTranslations((prev) => ({
        ...prev,
        [activeLang]: { ...(prev[activeLang] || {}), ...patch },
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
      // Steps too: prefill the language with the base prompts/labels so the
      // owner edits the text instead of retyping every question.
      if (!merged.steps || Object.keys(merged.steps).length === 0) {
        const stepCopy: Record<string, StepTranslation> = {};
        for (const step of steps) {
          const entry: StepTranslation = {};
          if (step.prompt?.trim()) entry.prompt = step.prompt;
          if (step.options?.length) entry.options = step.options.map((opt) => opt.label);
          if (Object.keys(entry).length > 0) stepCopy[step.id] = entry;
        }
        if (Object.keys(stepCopy).length > 0) merged.steps = stepCopy;
      }
      return { ...prev, [activeLang]: merged };
    });
  }, [activeLang, welcomeMessage, fallbackMessage, greeting, echoPrefix, menuButtons, steps, isHello, isEcho]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    // Drop empty languages and empty fields (empty string = "unset").
    const cleanedTranslations: Record<string, Translation> = {};
    for (const [lang, tr] of Object.entries(translations)) {
      const cleaned: Translation = {};
      for (const [field, value] of Object.entries(tr)) {
        if (field === "steps") continue; // nested copy, cleaned below
        if (Array.isArray(value) ? value.length > 0 : value !== undefined && value !== "") {
          (cleaned as Record<string, unknown>)[field] = value;
        }
      }
      const stepCopy = cleanStepTranslations(tr.steps);
      if (Object.keys(stepCopy).length > 0) cleaned.steps = stepCopy;
      if (Object.keys(cleaned).length > 0) cleanedTranslations[lang] = cleaned;
    }

    const newFlow: BotConfigFlow = {
      ...flow,
      bot_type: flow.bot_type || "generic",
      version: flow.version || 1,
      menu_buttons: menuButtons,
      active_modules: activeModules
        .split(",")
        .map((m) => m.trim())
        .filter(Boolean),
      translations: cleanedTranslations,
      single_language: singleLanguage,
    };
    // Blank copy = "unset", never an override: sending "" here would store an
    // empty message and make the bot reply with nothing (Telegram rejects
    // empty text outright). Deleting the key lets the config default apply.
    if (welcomeMessage.trim()) newFlow.welcome_message = welcomeMessage.trim();
    else delete newFlow.welcome_message;
    if (fallbackMessage.trim()) newFlow.fallback_message = fallbackMessage.trim();
    else delete newFlow.fallback_message;
    if (isHello) {
      if (greeting.trim()) newFlow.greeting = greeting.trim();
      else delete newFlow.greeting;
    }
    if (isEcho) newFlow.echo_prefix = echoPrefix;
    // Main language drives the middle layer of the fallback chain. Blank =
    // "unset" (English middle layer) — the key is removed, never written "".
    const main = mainLanguage.trim().toLowerCase();
    if (/^[a-z]{2}$/.test(main)) newFlow.main_language = main;
    else delete newFlow.main_language;
    // Only touch `steps` for a flow that has them: writing [] into a plain
    // welcome/menu bot would add a rider the owner never asked for.
    if (Array.isArray(flow.steps) || steps.length > 0) {
      newFlow.steps = cleanSteps(steps, baseSteps);
    }
    onSave(newFlow);
    setSaving(false);
  }, [flow, welcomeMessage, fallbackMessage, menuButtons, activeModules, translations, singleLanguage, greeting, echoPrefix, mainLanguage, steps, baseSteps, isHello, isEcho, onSave]);

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

      <TemplatePanel
        botId={bot.id}
        botType={bot.bot_type}
        onRecloned={onRecloned}
        onError={onPanelError}
      />

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

      {hasSteps && (
        <Section
          title={`Flow Steps (${steps.length})`}
          subtitle="Every step of this bot's multi-step flow — prompt, options, answer type and correct answers. Each step is translatable per language below."
        >
          <FlowStepsEditor steps={steps} onChange={setSteps} />
        </Section>
      )}

      <Section
        title="Main Language"
        subtitle="The language your bot's base copy is written in. It sets the middle layer of the fallback chain — user language → main language → base copy — so a user without a translation gets your base copy's language instead of English."
      >
        <select value={mainLanguage} onChange={(e) => setMainLanguage(e.target.value)}>
          <option value="">— none (English middle layer) —</option>
          {LANGUAGE_OPTIONS.map((lang) => (
            <option key={lang.code} value={lang.code}>
              {lang.label}
            </option>
          ))}
          {mainLanguage && !LANGUAGE_OPTIONS.some((l) => l.code === mainLanguage) && (
            <option value={mainLanguage}>🌐 {mainLanguage}</option>
          )}
        </select>
        <p className="text-xs mt-1" style={{ color: "var(--tg-hint)" }}>
          Base copy language:{" "}
          {mainLanguage
            ? languageLabel(mainLanguage)
            : "not set — English is the middle layer (fallback: user language → English → base copy)"}
        </p>
      </Section>

      <Section
        title="Translations"
        subtitle="Users see the bot in their Telegram language — per field: user language → main language → base"
      >
        <label
          className="flex items-center gap-2 text-sm mb-3"
          style={{ color: "var(--tg-text)" }}
        >
          <input
            type="checkbox"
            checked={singleLanguage}
            onChange={(e) => setSingleLanguage(e.target.checked)}
          />
          Single-language mode — ignore translations & user language (always use
          the base copy)
        </label>

        {singleLanguage ? (
          <p className="text-xs" style={{ color: "var(--tg-hint)" }}>
            🔒 Translations are saved but ignored while single-language mode is
            on. Users get the base copy and cannot switch languages.
          </p>
        ) : (
          <>
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
                onChange={(e) => setLangField({ welcome_message: e.target.value })}
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
                  onChange={(e) => setLangField({ greeting: e.target.value })}
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
                  onChange={(e) => setLangField({ echo_prefix: e.target.value })}
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
                onChange={(e) => setLangField({ fallback_message: e.target.value })}
                placeholder={fallbackMessage}
              />
            </label>

            <div>
              <span className="text-xs" style={{ color: "var(--tg-hint)" }}>
                Menu buttons
              </span>
              <MenuButtonsEditor
                buttons={translations[activeLang]?.menu_buttons || []}
                onChange={(b) => setLangField({ menu_buttons: b })}
              />
            </div>

            {hasSteps && (
              <div>
                <span className="text-xs" style={{ color: "var(--tg-hint)" }}>
                  Step copy — every question and option label in {activeLang}
                </span>
                <StepTranslationsEditor
                  steps={steps}
                  value={translations[activeLang]?.steps || {}}
                  onChange={(stepCopy) => setLangField({ steps: stepCopy })}
                />
              </div>
            )}

            <button className="btn-secondary w-full" onClick={copyFromBase}>
              📋 Copy from base
            </button>
          </div>
        )}
          </>
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
