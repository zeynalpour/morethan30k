// Languages offered by the dashboard's main-language selector (issue #23).
//
// Mirrors the tenant bot's `/language` picker list in
// `src/tme/routers/dynamic.py` (`_LANGUAGE_FLAGS`) so the owner picks from the
// same set of languages their users can choose. Any other ISO-639-1 code
// already stored on a flow is preserved and offered as its own option — the
// backend accepts any two-letter code.

export interface LanguageOption {
  code: string;
  label: string;
}

export const LANGUAGE_OPTIONS: LanguageOption[] = [
  { code: "en", label: "🇬🇧 English" },
  { code: "fa", label: "🇮🇷 فارسی (Persian)" },
  { code: "de", label: "🇩🇪 Deutsch (German)" },
  { code: "ru", label: "🇷🇺 Русский (Russian)" },
  { code: "ar", label: "🇸🇦 العربية (Arabic)" },
  { code: "es", label: "🇪🇸 Español (Spanish)" },
  { code: "fr", label: "🇫🇷 Français (French)" },
  { code: "tr", label: "🇹🇷 Türkçe (Turkish)" },
  { code: "zh", label: "🇨🇳 中文 (Chinese)" },
  { code: "hi", label: "🇮🇳 हिन्दी (Hindi)" },
  { code: "id", label: "🇮🇩 Bahasa Indonesia" },
  { code: "pt", label: "🇧🇷 Português (Portuguese)" },
];

export function languageLabel(code: string | null | undefined): string {
  if (!code) return "";
  return LANGUAGE_OPTIONS.find((l) => l.code === code)?.label ?? `🌐 ${code}`;
}
