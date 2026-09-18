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
//
// AUTHORING-GATE RULE (IDEAS N step 0) — the reason this panel is not a
// one-way door either. A module's DERIVED enabled-state may gate EXECUTION and
// may render this read-only status; it must NEVER gate AUTHORING. So a module
// reading "off" still shows the control that authors its data (for `steps`: the
// ➕ Add step button wired to the Flow Steps editor), and a module reading "on"
// keeps it too (you can always add another step). Never condition an authoring
// control on `mod.active` (or on any other derived state) — that is the same
// circular mistake as gating the steps editor on the `steps` flag: the
// capability has no data, so the flag is off, so the only way to create that
// data is hidden. The registry can't know how this frontend wires a control, so
// it declares WHERE the authoring lives (`authoring_hint`) and the panel maps
// the module id to its control below.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { ModuleSummary } from "../lib/api";
import { FLOW_STEPS_ANCHOR_ID } from "../lib/ui-anchors";

interface ModulesPanelProps {
  botId: number;
  /** The steps module's authoring action (append a step), supplied by the
   * config editor that owns the flow state. Optional only so the panel can be
   * rendered without an editor host; when absent the panel falls back to the
   * anchor jump so the affordance is never missing. */
  onAuthorStep?: () => void;
}

/** Module id → the authoring control that creates its data.
 *
 * This is deliberately NOT keyed on the module's state: an entry exists for a
 * module that reads "off" exactly as much as for one that reads "on". A new
 * module with its own config form adds one entry here and its form stays
 * reachable while the module is off (AUTHORING-GATE RULE). */
const MODULE_AUTHORING: Record<
  string,
  { anchorId: string; label: string; hint: string }
> = {
  steps: {
    anchorId: FLOW_STEPS_ANCHOR_ID,
    label: "➕ Add a step",
    hint: "Adding a step is what turns this module on — the state above follows the flow.",
  },
};

export function ModulesPanel({ botId, onAuthorStep }: ModulesPanelProps) {
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
      {modules.map((mod) => {
        // Wired authoring control for this module (undefined → no control is
        // wired in this frontend yet, e.g. a registry entry added ahead of its
        // editor). NOTE: an authoring control is only ever resolved by module
        // ID — never by `mod.active`, so being off can never remove it.
        const authoring = MODULE_AUTHORING[mod.id];
        return (
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
            {/* AUTHORING-GATE RULE: this block renders in BOTH states — it is
                deliberately NOT behind `mod.active` (or any other derived
                condition). "Off" is exactly when the owner needs it most:
                with no steps in the flow the panel used to describe the
                module and offer no way to create one, which made deleting the
                last step a one-way door. Adding the data is what turns the
                module on. */}
            {authoring && (
              <div className="space-y-1 pt-1">
                <p className="text-xs" style={{ color: "var(--tg-hint)" }}>
                  {mod.authoring_hint || authoring.hint}
                </p>
                <button
                  className="btn-secondary w-full"
                  data-testid={`module-${mod.id}-author`}
                  style={{ border: "1px dashed var(--tg-hint)" }}
                  onClick={() => {
                    // Author the module FIRST (a real state change, not a
                    // scroll), then bring its editor into view.
                    onAuthorStep?.();
                    document
                      .getElementById(authoring.anchorId)
                      ?.scrollIntoView({ behavior: "smooth", block: "start" });
                  }}
                >
                  {authoring.label}
                </button>
              </div>
            )}
          </div>
        );
      })}
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
