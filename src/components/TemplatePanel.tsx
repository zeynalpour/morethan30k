// S2.3 — "Reset to template" panel: a bot's template lineage, an
// "update available" badge when the registry is ahead of the clone's pinned
// version, and the explicit, confirm-guarded re-clone action. No silent
// auto-updates: the flow only changes when the owner confirms here.
//
// Adoption: a bot with NO provenance (scratch / pre-Phase-2) shows the
// picker instead of the lineage and can adopt any template of its own
// bot_type through the same POST /api/bots/{id}/reclone path.
import { useState, useCallback, useEffect, useRef } from "react";
import { api } from "../lib/api";
import type { TemplateProvenance, TemplateSummary } from "../lib/api";

interface TemplatePanelProps {
  botId: number;
  botType: string;
  onRecloned: () => void;
  onError: (msg: string) => void;
}

export function TemplatePanel({ botId, botType, onRecloned, onError }: TemplatePanelProps) {
  const [prov, setProv] = useState<TemplateProvenance | null>(null);
  const [templates, setTemplates] = useState<TemplateSummary[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const mounted = useRef(false);

  const load = useCallback(async () => {
    try {
      const [p, t] = await Promise.all([
        api.getBotTemplate(botId),
        api.listTemplates(),
      ]);
      if (!mounted.current) return;
      setProv(p);
      setTemplates(t);
    } catch (e) {
      // The panel degrades silently on read errors — lineage is metadata,
      // never a reason to block the editor around it.
      if (mounted.current) setProv(null);
    }
  }, [botId]);

  useEffect(() => {
    mounted.current = true;
    load();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  const reclone = useCallback(
    async (templateId: string, version?: number) => {
      setBusy(true);
      try {
        await api.recloneTemplate(botId, {
          template_id: templateId,
          version,
        });
        setConfirmId(null);
        setShowPicker(false);
        await load();
        onRecloned();
      } catch (e) {
        onError(e instanceof Error ? e.message : "Re-clone failed");
      } finally {
        setBusy(false);
      }
    },
    [botId, load, onRecloned, onError]
  );

  if (prov === null || templates === null) return null;

  const current = prov.current;
  const currentTemplate = current
    ? templates.find((t) => t.id === current.id)
    : null;
  const sameTypeTemplates = templates.filter((t) => t.bot_type === botType);

  return (
    <Section
      title="Template"
      subtitle={
        current
          ? `This bot was created from the “${currentTemplate?.display_name || current.id}” template`
          : "This bot has no template yet — adopt one to get a ready-made flow you can then customize"
      }
    >
      {current ? (
        <>
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className="text-xs px-2 py-0.5 rounded-full"
              style={{ background: "var(--tg-secondary-bg)", color: "var(--tg-text)" }}
            >
              📦 {currentTemplate?.display_name || current.id} · v{current.version}
            </span>
            {prov.update_available && prov.latest_version !== null && (
              <span
                className="text-xs px-2 py-0.5 rounded-full"
                style={{
                  background: "rgba(33,150,243,0.15)",
                  color: "#2196f3",
                }}
              >
                ✨ Update available — v{prov.latest_version}
              </span>
            )}
          </div>

          {prov.update_available ? (
            <button
              className="btn-secondary w-full"
              disabled={busy}
              onClick={() => setConfirmId(current.id)}
            >
              🔄 Update to the latest version
            </button>
          ) : (
            <p className="text-xs" style={{ color: "var(--tg-hint)" }}>
              ✅ You're on the latest version of this template.
            </p>
          )}
        </>
      ) : (
        <>
          {sameTypeTemplates.length === 0 ? (
            <p className="text-xs" style={{ color: "var(--tg-hint)" }}>
              No templates available for this bot type yet.
            </p>
          ) : showPicker ? (
          <div className="space-y-2">
            {sameTypeTemplates.map((t) => (
              <button
                key={t.id}
                className="btn-secondary w-full text-left"
                onClick={() => setConfirmId(t.id)}
              >
                <b>{t.display_name}</b>
                <span className="block text-xs" style={{ color: "var(--tg-hint)" }}>
                  {t.description}
                </span>
              </button>
            ))}
          </div>
          ) : (
            <button className="btn-secondary w-full" onClick={() => setShowPicker(true)}>
              📦 Adopt a template
            </button>
          )}
        </>
      )}

      {confirmId !== null && (
        <div
          className="p-3 rounded-xl space-y-3"
          style={{ background: "rgba(244,67,54,0.08)" }}
        >
          <p className="text-xs" style={{ color: "var(--tg-text)" }}>
            ⚠️ <b>Reset to template</b> — this bot's messages, menu and flow
            will be replaced by the template's copy. Your translations and
            single-language setting are kept. This cannot be undone.
          </p>
          <div className="flex gap-2">
            <button
              className="btn-primary flex-1"
              style={{ background: "#f44336" }}
              disabled={busy}
              onClick={() => reclone(confirmId)}
            >
              {busy ? "Re-cloning…" : "Reset to template"}
            </button>
            <button
              className="btn-secondary flex-1"
              disabled={busy}
              onClick={() => setConfirmId(null)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
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
