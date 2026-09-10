"""Controller-bot (main bot) copy localization — Phase 1 S1.3.

The controller speaks the owner's language: stored ``/language`` preference →
Telegram UI ``language_code`` → English. Unlike tenant bots, whose copy lives
in the DB and is owner-editable, the controller's copy is built in here.
Add a language by extending :data:`MAIN_BOT_STRINGS` (keys must mirror the
``en`` table exactly); the controller's ``/language`` picker offers exactly
the languages present in this table.
"""

from __future__ import annotations

from tme.core.i18n import normalize_language

#: Controller copy per language: key → text. "en" is the fallback table and
#: must define EVERY key. ``{name}``/``{count}``/``{display}``/``{label}`` are
#: format slots filled by :func:`tr` callers.
MAIN_BOT_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "cmd.start": "Start",
        "cmd.mybots": "My bots & settings",
        "cmd.dashboard": "Open the bot dashboard",
        "cmd.language": "Change language",
        "btn.create_bot": "➕ Create a Managed Bot",
        "btn.my_bots": "🤖 My Bots",
        "btn.open_dashboard": "🚀 Open Dashboard",
        "btn.open_settings": "⚙️ Open Settings",
        "tmpl.hello_world": "👋 Hello World",
        "tmpl.echo": "🔁 Echo",
        "tmpl.feedback_collector": "📣 Feedback Collector",
        "tmpl.quiz": "🧠 Quiz",
        "tmpl.simple_form": "📝 Simple Form",
        "tmpl.scratch": "✨ Start from Scratch",
        "start.welcome": (
            "👋 Hi {name}! Welcome to TME.\n\nTap the button below to create your own Telegram bot."
        ),
        "create.intro": (
            "🛠️ <b>Let's create your bot.</b>\n\n"
            "Please authorise TME to create a managed bot for your account. "
            "As soon as you confirm, your new bot will be provisioned and go "
            "live automatically — I'll message you here when it's ready."
        ),
        "token_failed": (
            "⚠️ I couldn't fetch your new bot's token right now. "
            "Please try again in a few seconds — it may still be propagating "
            "on Telegram's side."
        ),
        "bot_ready": ("🤖 Your new bot is ready!\n\nPick its behaviour — you can change it later:"),
        "err.generic": "Something went wrong — please try again.",
        "err.unknown_template": "Unknown template — please pick again.",
        "err.expired": "That request expired — tap “Create a Managed Bot” again.",
        "err.provision": (
            "❌ Something went wrong while wiring up your bot. "
            "Tap your chosen type again to retry — or create it anew "
            "if it keeps failing."
        ),
        "bot_live": "✅ {display} is live! Try sending it /start.",
        "bot.fallback_name": "Bot #{id}",
        "no_bots": (
            "🤖 You don't have any bots yet — tap “➕ Create a Managed Bot” to make your first one."
        ),
        "bots_list": ("Your bots ({count}):\n\nTap a bot to open its ⚙️ settings."),
        "dashboard.intro": ("🚀 Your bot dashboard — manage all your bots in one place:"),
        "lang.prompt": "🌐 Choose your language:",
        "lang_name.en": "🇬🇧 English",
        "lang_name.fa": "🇮🇷 فارسی",
        "btn.lang_auto": "🔄 Auto (Telegram)",
        "lang.done": "Language updated ✅",
        "lang.auto": "Following your Telegram language ✅",
        "lang.choice": "🌐 Language: {label}",
    },
    "fa": {
        "cmd.start": "شروع",
        "cmd.mybots": "ربات‌های من و تنظیمات",
        "cmd.dashboard": "باز کردن داشبورد ربات",
        "cmd.language": "تغییر زبان",
        "btn.create_bot": "➕ ساخت ربات مدیریت‌شده",
        "btn.my_bots": "🤖 ربات‌های من",
        "btn.open_dashboard": "🚀 باز کردن داشبورد",
        "btn.open_settings": "⚙️ باز کردن تنظیمات",
        "tmpl.hello_world": "👋 سلام (Hello World)",
        "tmpl.echo": "🔁 تکرار (Echo)",
        "tmpl.feedback_collector": "📣 جمع‌آوری بازخورد",
        "tmpl.quiz": "🧠 کوییز",
        "tmpl.simple_form": "📝 فرم ساده",
        "tmpl.scratch": "✨ از صفر شروع کن",
        "start.welcome": (
            "👋 سلام {name}! به TME خوش آمدی.\n\nبرای ساخت ربات تلگرامی خودت روی دکمه زیر بزن."
        ),
        "create.intro": (
            "🛠️ <b>بیا رباتت را بسازیم.</b>\n\n"
            "لطفاً به TME اجازه بده برای حساب تو یک ربات مدیریت‌شده بسازد. "
            "به محض تأیید، ربات جدیدت ساخته و خودکار راه‌اندازی می‌شود — "
            "وقتی آماده شد همین‌جا خبرت می‌کنم."
        ),
        "token_failed": (
            "⚠️ الان نتوانستم توکن ربات جدیدت را بگیرم. "
            "چند ثانیه دیگر دوباره تلاش کن — شاید هنوز در حال همگام‌سازی "
            "در تلگرام باشد."
        ),
        "bot_ready": (
            "🤖 ربات جدیدت آماده است!\n\nنوع رفتارش را انتخاب کن — بعداً هم می‌توانی عوضش کنی:"
        ),
        "err.generic": "مشکلی پیش آمد — دوباره تلاش کن.",
        "err.unknown_template": "قالب ناشناخته — دوباره انتخاب کن.",
        "err.expired": ("آن درخواست منقضی شد — دوباره روی «ساخت ربات مدیریت‌شده» بزن."),
        "err.provision": (
            "❌ هنگام راه‌اندازی رباتت مشکلی پیش آمد. "
            "برای تلاش دوباره روی همان نوع بزن — یا اگر باز هم خطا داد، "
            "از نو بسازش."
        ),
        "bot_live": "✅ {display} ساخته شد! یک /start بهش بزن.",
        "bot.fallback_name": "ربات #{id}",
        "no_bots": (
            "🤖 هنوز رباتی نداری — برای ساخت اولین رباتت روی «➕ ساخت ربات مدیریت‌شده» بزن."
        ),
        "bots_list": ("ربات‌های تو ({count}):\n\nبرای باز کردن تنظیمات ⚙️ روی هر ربات بزن."),
        "dashboard.intro": "🚀 داشبورد ربات‌هایت — همه ربات‌هایت را یک‌جا مدیریت کن:",
        "lang.prompt": "🌐 زبانت را انتخاب کن:",
        "lang_name.en": "🇬🇧 English",
        "lang_name.fa": "🇮🇷 فارسی",
        "btn.lang_auto": "🔄 خودکار (تلگرام)",
        "lang.done": "زبان به‌روزرسانی شد ✅",
        "lang.auto": "پیروی از زبان تلگرامت ✅",
        "lang.choice": "🌐 زبان: {label}",
    },
}


def tr(language_code: str | None, key: str, **kwargs: object) -> str:
    """Resolve a controller string for a language (falls back to English).

    Unknown keys raise ``KeyError`` against the English table on purpose — a
    missing key is a programming error, not a runtime fallback.
    """
    lang = normalize_language(language_code)
    table = MAIN_BOT_STRINGS.get(lang, MAIN_BOT_STRINGS["en"])
    text = table.get(key) or MAIN_BOT_STRINGS["en"][key]
    return text.format(**kwargs) if kwargs else text


def supported_languages() -> list[str]:
    """Language codes the controller copy ships in (picker order)."""
    return list(MAIN_BOT_STRINGS)
