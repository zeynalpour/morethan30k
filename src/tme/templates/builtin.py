"""Built-in template seeds — pure data, no per-template code (Phase 2 S2.1).

One module-level constant per shipped template. Every seed is an ordinary
:class:`~tme.schemas.bot_config.BotConfigUnion` instance: Hello/Echo reuse the
existing variants, the conversational three (feedback / quiz / form) are
**generic** flows whose ``steps``-style data the shared engine consumes in
S2.2 — expressible with today's fields (``menu_buttons``, ``greeting``,
``active_modules``, ``fallback``) via ``extra="allow"``. No new union
variants, no new ``BotType`` members, no router changes.

Provenance is stamped **inside the flow** as ``{"template": {"id", "version"}}``
(the Architect's S2.1 recommendation — see issue #7): ``BotConfigBase``'s
``extra="allow"`` lets the rider round-trip through ``BotConfigUnion``
validation on every write path, the dashboard's ``...flow`` spread preserves
it across owner edits, and ``parse_bot_config``'s tolerant fallback keeps
legacy rows without a stamp routable. The stamp lives on the *seed* (not
added at provision time) so this module's import-time validation loop proves
the stamped flow parses before anything ships.

Seeds must NOT set ``single_language=True`` — that's an owner decision and
it would hide the translation editor in the dashboard. Partial
``translations`` are fine: the Phase 1 overlay resolves per field (user
language → ``en`` → base), so a partial seed degrades exactly like an
owner-authored partial translation.
"""

from __future__ import annotations

from pydantic import TypeAdapter

from tme.database.models import BotType
from tme.schemas.bot_config import (
    BotConfigSchema,
    BotConfigUnion,
    EchoBotConfig,
    HelloBotConfig,
    Translation,
)
from tme.templates.registry import (
    _DEFAULT_SEED_FOR_TYPE,
    REGISTRY,
    STAMP_KEY,
    TemplateSpec,
    register,
    set_default_seed,
)

#: The flow rider carrying every template's provenance — S2.3 reads it to
#: know which seed a bot came from ("update available" when the registry is
#: ahead of the row's stamped version).
_STAMP = STAMP_KEY

_union_adapter: TypeAdapter[BotConfigUnion] = TypeAdapter(BotConfigUnion)


def _stamped(
    seed: BotConfigUnion,
    template_id: str,
    version: int,
) -> BotConfigUnion:
    """Return ``seed`` carrying its provenance stamp (``extra="allow"`` rider).

    Round-trips through :class:`BotConfigUnion` so the stamped seed is
    exactly what persists to ``bot_configs.flow`` and what the S2.1 tests
    validate — no unstamped copy exists to drift from the stamped one.
    """
    stamped = seed.model_dump()
    stamped[_STAMP] = {"id": template_id, "version": version}
    return _union_adapter.validate_python(stamped)


# ----------------------------------------------------------------- hello ----
HELLO_WORLD_SPEC = TemplateSpec(
    id="hello_world",
    version=1,
    bot_type=BotType.HELLO,
    display_name="Hello World",
    description="A friendly greeter that says hi on /start and to every message.",
    seed=_stamped(
        HelloBotConfig(
            greeting="Hello there! 👋",
            welcome_message="👋 Welcome!",
            fallback_message="Hello again! 👋",
            translations={
                "en": Translation(greeting="Hello there! 👋"),
                "fa": Translation(
                    greeting="سلام! 👋",
                    welcome_message="👋 خوش آمدید!",
                    fallback_message="سلام دوباره! 👋",
                ),
            },
        ),
        "hello_world",
        1,
    ),
)

# ------------------------------------------------------------------ echo ----
ECHO_SPEC = TemplateSpec(
    id="echo",
    version=1,
    bot_type=BotType.ECHO,
    display_name="Echo",
    description="Bounces every message back to the sender, ↩️ prefix included.",
    seed=_stamped(
        EchoBotConfig(
            echo_prefix="🔁 ",
            translations={
                "en": Translation(echo_prefix="🔁 "),
                "fa": Translation(echo_prefix="🔁 "),
            },
        ),
        "echo",
        1,
    ),
)

# ------------------------------------------------------------- feedback ----
#: S2.2's shared steps primitive consumes this ``steps`` array; today the
#: generic engine already renders the greeting, menu and fallback below.
#: ``steps`` is an ``extra="allow"`` flow rider (plain data the shared engine
#: interprets) — NOT a schema field, keeping S2.1 migration-free.
FEEDBACK_COLLECTOR_SPEC = TemplateSpec(
    id="feedback_collector",
    version=1,
    bot_type=BotType.GENERIC,
    display_name="Feedback Collector",
    description="Ask a rating question, then collect a free-text comment.",
    seed=_stamped(
        BotConfigSchema.model_validate(
            {
                "welcome_message": "📣 Tell us what you think!",
                "fallback_message": "Please use the buttons to rate us, or type your feedback 🙏",
                "menu_buttons": [
                    {"text": "⭐ Rate us", "callback": "step:0"},
                    {"text": "💬 Send feedback", "callback": "step:0"},
                ],
                "active_modules": ["steps"],
                "steps": [
                    {
                        "id": "rating",
                        "prompt": "How would you rate us? ⭐",
                        "options": [
                            {"label": "😍 Excellent", "value": "excellent"},
                            {"label": "🙂 Good", "value": "good"},
                            {"label": "😐 Okay", "value": "okay"},
                            {"label": "🙁 Poor", "value": "poor"},
                        ],
                    },
                    {
                        "id": "comment",
                        "prompt": "Thanks! Anything you'd like to add? Type your thoughts 💬",
                        "answer_type": "free_text",
                    },
                ],
                "translations": {
                    "en": {},
                    "fa": {
                        "fallback_message": (
                            "لطفاً با دکمه‌ها به ما امتیاز دهید یا بازخورد خود را بنویسید 🙏"
                        )
                    },
                },
            }
        ),
        "feedback_collector",
        1,
    ),
)

# ------------------------------------------------------------------ quiz ----
QUIZ_SPEC = TemplateSpec(
    id="quiz",
    version=1,
    bot_type=BotType.GENERIC,
    display_name="Quiz",
    description="A three-question quiz with option buttons and a final score.",
    seed=_stamped(
        BotConfigSchema.model_validate(
            {
                "welcome_message": "🧠 Ready to play? Tap below to start the quiz!",
                "fallback_message": "Pick an option from the quiz, or send /start to retry 🧠",
                "menu_buttons": [
                    {"text": "🧠 Start the quiz", "callback": "step:0"},
                    {"text": "🔁 Play again", "callback": "step:0"},
                ],
                "active_modules": ["steps"],
                "steps": [
                    {
                        "id": "q1",
                        "prompt": "Question 1/3 — What is the capital of France?",
                        "options": [
                            {"label": "Paris", "value": "paris"},
                            {"label": "London", "value": "london"},
                            {"label": "Berlin", "value": "berlin"},
                        ],
                        "correct_answers": ["paris"],
                    },
                    {
                        "id": "q2",
                        "prompt": "Question 2/3 — Which planet is the Red Planet?",
                        "options": [
                            {"label": "Mars", "value": "mars"},
                            {"label": "Venus", "value": "venus"},
                            {"label": "Jupiter", "value": "jupiter"},
                        ],
                        "correct_answers": ["mars"],
                    },
                    {
                        "id": "q3",
                        "prompt": "Question 3/3 — How many continents are there?",
                        "options": [
                            {"label": "5", "value": "5"},
                            {"label": "7", "value": "7"},
                            {"label": "9", "value": "9"},
                        ],
                        "correct_answers": ["7"],
                    },
                    {
                        "id": "result",
                        "prompt": "🎉 That's it! Send /start to play again.",
                        "answer_type": "none",
                    },
                ],
                "translations": {
                    "en": {},
                    "fa": {
                        "fallback_message": (
                            "لطفاً یک گزینه از کوییز را انتخاب کنید یا /start را بفرستید 🧠"
                        )
                    },
                },
            }
        ),
        "quiz",
        1,
    ),
)

# ----------------------------------------------------------- simple form ----
SIMPLE_FORM_SPEC = TemplateSpec(
    id="simple_form",
    version=1,
    bot_type=BotType.GENERIC,
    display_name="Simple Form",
    description="Collect name, contact and a message in three guided steps.",
    seed=_stamped(
        BotConfigSchema.model_validate(
            {
                "welcome_message": "📝 A short form — tap below to begin!",
                "fallback_message": "This bot collects form answers — send /start to begin 📝",
                "menu_buttons": [
                    {"text": "📝 Start the form", "callback": "step:0"},
                    {"text": "🔄 Start over", "callback": "step:0"},
                ],
                "active_modules": ["steps"],
                "steps": [
                    {
                        "id": "name",
                        "prompt": "Step 1/3 — What's your name?",
                        "answer_type": "free_text",
                    },
                    {
                        "id": "contact",
                        "prompt": "Step 2/3 — How can we reach you? (phone, @username or email)",
                        "answer_type": "free_text",
                    },
                    {
                        "id": "message",
                        "prompt": "Step 3/3 — Your message:",
                        "answer_type": "free_text",
                    },
                    {
                        "id": "done",
                        "prompt": "✅ Thanks! Your answers were recorded. Send /start to resubmit.",
                        "answer_type": "none",
                    },
                ],
                "translations": {
                    "en": {},
                    "fa": {
                        "fallback_message": (
                            "این ربات پاسخ‌های فرم را جمع می‌کند — برای شروع /start را بفرستید 📝"
                        )
                    },
                },
            }
        ),
        "simple_form",
        1,
    ),
)

# --------------------------------------------------------------- register ---
# Registration order = picker card order (S2.2). Only the launched set is
# registered; per-type "start from scratch" defaults are pinned separately
# below so listing templates can never pick up a scratch entry.
for _spec in (
    HELLO_WORLD_SPEC,
    ECHO_SPEC,
    FEEDBACK_COLLECTOR_SPEC,
    QUIZ_SPEC,
    SIMPLE_FORM_SPEC,
):
    register(_spec)

# Per-type "start from scratch" seeds. hello/echo pin the template seeds
# (each equal-or-superset of _default_config_for's output — pinned by test);
# generic pins today's bare starter (BotConfigSchema.default()) UNSTAMPED —
# a scratch bot has no provenance to record (S2.3's adoption path covers it).
set_default_seed(BotType.HELLO, HELLO_WORLD_SPEC.seed)
set_default_seed(BotType.ECHO, ECHO_SPEC.seed)
set_default_seed(BotType.GENERIC, BotConfigSchema.default())


# ------------------------------------------------- import-time validation ---
def _validate_registry() -> None:
    """Prove every registered/default seed survives a union round-trip.

    A broken template fails HERE, at import, never at a live user's
    provision (S2.1 acceptance criterion: "a malformed template fails its
    own test, never a live user").
    """
    for versions in REGISTRY.values():
        for spec in versions:
            _union_adapter.validate_python(spec.seed.model_dump())
    for seed in _DEFAULT_SEED_FOR_TYPE.values():
        _union_adapter.validate_python(seed.model_dump())


_validate_registry()
