// IDEAS N step 0 — the Modules panel.
//
// Replaces the old free-text "Active Modules" input, which let a bot ADVERTISE
// a module (e.g. `steps`) its flow did not contain. What the owner sees now
// comes from the in-code module registry (GET /api/bots/{id}/modules) and the
// toggle state is DERIVED by the backend from the flow the engine runs:
//
//   * registry entry `active: true`  → the flow contains the capability's
//     config (for `steps`: the flow has steps), so the toggle reads "on";
//   * no such config                 → "off".
//
// The toggles are deliberately read-only. Typing a module name was the defect;
// a capability is switched on/off by editing the flow it needs (add/remove the
// steps below), never by claiming it. Modules are not editable state.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { ModuleSummary } from "../lib/api";

interface ModulesPanelProps {
  botId: number;
}

export function ModulesPanel({ botId }: ModulesPanelProps) {
  const [modules, setModules] = useState<ModuleSummary[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const mounted = useRef(false);

  const load = useCallback(async () => {
    try {
      const list = await api.listModules(botId);
      if (!mounted.current) return;
      setModules(list);
      setLoadFailed(false);
    } catch {
      // A panel that silently disappears reads as "the feature is gone" —
      // surface an explicit retry instead of nulling the state.
      if (mounted.current) setLoadFailed(true);
    }
  }, [botId]);

  useEffect(() => {
    mounted.current = true;
    load();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  if (loadFailed) {
    return (
      <Section title="Modules" subtitle="The module list is unavailable right now.">
        <button
          className="btn-secondary w-full"
          onClick={() => {
            setLoadFailed(false);
            load();
          }}
        >
          ↻ Retry
        </button>
      </Section>
    );
  }
  if (modules === null) return null;

  if (modules.length === 0) {
    return (
      <Section title="Modules" subtitle="Capabilities this bot's flow uses.">
        <p className="text-xs" style={{ color: "var(--tg-hint)" }}>
          No modules are available yet.
        </p>
      </Section>
    );
  }

  return (
    <Section
      title="Modules"
      subtitle="Capabilities your bot's flow actually contains — derived from the flow, never typed"
    >
      {modules.map((mod) => (
        <div
          key={mod.id}
          className="p-3 rounded-xl space-y-1"
          style={{ background: "var(--tg-secondary-bg)" }}
        >
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={mod.active} disabled readOnly />
            <span className="text-sm font-medium" style={{ color: "var(--tg-text)" }}>
              {mod.display_name}
            </span>
            <span
              className="text-xs px-2 py-0.5 rounded-full ml-auto"
              style={
                mod.active
                  ? { background: "rgba(76,175,80,0.18)", color: "#4caf50" }
                  : { background: "var(--tg-secondary-bg)", color: "var(--tg-hint)" }
              }
            >
              {mod.active ? "on" : "off"}
            </span>
          </label>
          <p className="text-xs" style={{ color: "var(--tg-hint)" }}>
            {mod.description}
          </p>
          <p className="text-xs" style={{ color: "var(--tg-hint)" }}>
            {mod.active
              ? `On because this bot's flow contains ${mod.config_keys.join(", ")}. Remove ${
                  mod.config_keys.length > 1 ? "them" : "it"
                } below to turn it off.`
              : `Off — this bot's flow has no ${mod.config_keys.join(", ")} yet.`}
            {mod.dependencies.length > 0 && ` Requires: ${mod.dependencies.join(", ")}.`}
          </p>
        </div>
      ))}
    </Section>
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
