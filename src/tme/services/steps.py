"""Config-driven multi-step flows — the ``steps`` primitive (Phase 2, S2.2).

Templates seed a ``steps`` array into the flow; because ``BotConfigBase`` is
``extra="allow"``, that rider needs no migration and no per-template code. This
module interprets it:

* renders each step (prompt + optional option buttons),
* routes option taps (``stepopt:{step}:{option}``) and free-text answers,
* keeps exactly one active flow per ``(bot, user)`` in Redis (30 min TTL),
* scores quiz-style steps (``correct_answers``), and
* delivers the collected answers to the bot's owner on completion.

Everything is inert unless the flow opts in — ``active_modules`` must contain
``"steps"`` **and** ``steps`` must parse into at least one usable step. Bots
without that keep the plain welcome/menu/fallback behaviour untouched.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import orjson
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from tme.core.logging import get_logger
from tme.core.redis_client import redis_client
from tme.database.engine import session_scope
from tme.database.models import Bot as BotModel, User
from tme.schemas.bot_config import BotConfigUnion

logger = get_logger(__name__)

#: Flow module gate — a bot only runs steps when this is in ``active_modules``.
STEP_MODULE = "steps"

#: Menu buttons that start a flow: ``step:{index}``.
START_PREFIX = "step:"

#: Option taps: ``stepopt:{step_index}:{option_index}`` — indices keep the
#: payload inside Telegram's 64-byte callback_data cap regardless of content.
OPTION_PREFIX = "stepopt:"

#: Cancel button shown under every step prompt.
CANCEL_CALLBACK = "stepcancel"

#: Inactivity window after which an unfinished flow is abandoned.
STATE_TTL = 1800


class StepOption(BaseModel):
    """One selectable answer for a choice step."""

    label: str = Field(..., min_length=1)
    value: str = Field(..., min_length=1)


class FlowStep(BaseModel):
    """A single prompt in a multi-step flow.

    ``answer_type`` semantics:

    * ``auto`` (default) — a choice when ``options`` are present, else free text.
    * ``free_text`` — any non-command message is the answer.
    * ``none`` — informational terminal step; nothing is collected.
    """

    id: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)
    options: list[StepOption] = Field(default_factory=list)
    answer_type: str = Field(default="auto")
    correct_answers: list[str] = Field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.answer_type == "none"

    @property
    def expects_text(self) -> bool:
        """True when the user's next plain message is this step's answer."""
        if self.is_terminal:
            return False
        if self.answer_type == "free_text":
            return True
        return self.answer_type == "auto" and not self.options


class FlowState(BaseModel):
    """One user's progress through a bot's flow (stored in Redis)."""

    index: int = 0
    values: dict[str, str] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)

    def score(self, steps: list[FlowStep]) -> tuple[int, int]:
        """``(correct, graded)`` across the steps that declare answers."""
        graded = [s for s in steps if s.correct_answers]
        correct = sum(1 for step in graded if self.values.get(step.id) in set(step.correct_answers))
        return correct, len(graded)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def parse_steps(config: BotConfigUnion) -> list[FlowStep]:
    """Extract a usable flow from a bot config, or ``[]`` when it has none.

    Malformed entries are dropped with a warning instead of raising: a broken
    template must not break the tenant's other behaviour.
    """
    flow = config.model_dump()

    modules = flow.get("active_modules") or []
    if STEP_MODULE not in modules:
        return []

    raw_steps = flow.get("steps") or []
    if not isinstance(raw_steps, list):
        logger.warning("Flow has non-list 'steps' payload — ignoring")
        return []

    steps: list[FlowStep] = []
    for raw in raw_steps:
        try:
            steps.append(FlowStep.model_validate(raw))
        except ValidationError as exc:
            logger.warning("Dropping malformed flow step %r: %s", raw, exc)

    return steps


def is_step_bot(config: BotConfigUnion) -> bool:
    return bool(parse_steps(config))


# --------------------------------------------------------------------------- #
# State (Redis)
# --------------------------------------------------------------------------- #
def _state_key(bot_id: int, user_id: int) -> str:
    return f"flowstate:{bot_id}:{user_id}"


async def get_state(bot_id: int, user_id: int) -> FlowState | None:
    cached = await redis_client.get(_state_key(bot_id, user_id))
    if cached is None:
        return None
    try:
        return FlowState.model_validate(orjson.loads(cached))
    except (orjson.JSONDecodeError, ValidationError):
        logger.warning("Discarding unreadable flow state for bot=%s user=%s", bot_id, user_id)
        return None


async def set_state(bot_id: int, user_id: int, state: FlowState) -> None:
    await redis_client.set(
        _state_key(bot_id, user_id), orjson.dumps(state.model_dump()), ex=STATE_TTL
    )


async def clear_state(bot_id: int, user_id: int) -> None:
    await redis_client.delete(_state_key(bot_id, user_id))


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def option_keyboard(step_index: int, step: FlowStep) -> InlineKeyboardMarkup | None:
    """One button per option (with an index payload), plus a cancel row."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=opt.label, callback_data=f"{OPTION_PREFIX}{step_index}:{j}")]
        for j, opt in enumerate(step.options)
    ]
    rows.append([InlineKeyboardButton(text="✖ Cancel", callback_data="stepcancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _summary(steps: list[FlowStep], state: FlowState) -> str:
    """Human-readable recap of the collected answers (owner-facing)."""
    lines: list[str] = []
    for step in steps:
        if step.is_terminal:
            continue
        answer = state.labels.get(step.id)
        if answer:
            lines.append(f"▪️ <b>{step.id}</b>: {answer}")

    correct, graded = state.score(steps)
    if graded:
        lines.append(f"\n🎯 Score: <b>{correct}/{graded}</b>")

    return "\n".join(lines) or "(no answers)"


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #
async def deliver_to_owner(bot: Bot, bot_id: int, body: str) -> bool:
    """Send the collected answers to the bot's owner. Best-effort.

    The owner can only be messaged if they have an open chat with their own
    bot; a failure is logged, never surfaced to the end user.
    """
    async with session_scope() as session:
        result = await session.execute(
            select(User.telegram_id).join(BotModel.owner).where(BotModel.id == bot_id)
        )
        owner_telegram_id = result.scalar_one_or_none()

    if owner_telegram_id is None:
        logger.warning("No owner found for bot id=%s — dropping collected answers", bot_id)
        return False

    try:
        await bot.send_message(chat_id=owner_telegram_id, text=f"📥 <b>New responses</b>\n\n{body}")
        return True
    except Exception as exc:  # never break the user's flow
        logger.warning(
            "Could not deliver flow answers for bot id=%s to owner %s: %s",
            bot_id,
            owner_telegram_id,
            exc,
        )
        return False


# --------------------------------------------------------------------------- #
# Orchestration (used by the tenant router)
# --------------------------------------------------------------------------- #
async def _send_step(bot: Bot, chat_id: int, step_index: int, step: FlowStep) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=step.prompt,
        reply_markup=option_keyboard(step_index, step) if step.options else None,
    )


async def start_flow(bot: Bot, config: BotConfigUnion, chat_id: int, user_id: int) -> bool:
    """Begin the flow (optionally the step index embedded in the callback)."""
    steps = parse_steps(config)
    if not steps:
        return False

    state = FlowState(index=0)
    await set_state(bot.id, user_id, state)
    first = steps[0]
    await _send_step(bot, chat_id, 0, first)

    if first.is_terminal:
        # A one-step informational flow: nothing to collect.
        await clear_state(bot.id, user_id)
    return True


async def start_flow_at(
    bot: Bot, config: BotConfigUnion, chat_id: int, user_id: int, index: int
) -> bool:
    """Start the flow at ``index`` (menu buttons carry ``step:{index}``)."""
    steps = parse_steps(config)
    if not steps:
        return False

    index = max(0, min(index, len(steps) - 1))
    state = FlowState(index=index)
    await set_state(bot.id, user_id, state)
    await _send_step(bot, chat_id, index, steps[index])
    if steps[index].is_terminal:
        await clear_state(bot.id, user_id)
    return True


async def _finish(
    bot: Bot,
    steps: list[FlowStep],
    chat_id: int,
    user_id: int,
    state: FlowState,
) -> None:
    """Deliver answers, send the closing prompt (if any) and clear state."""
    await deliver_to_owner(bot, bot.id, _summary(steps, state))
    await clear_state(bot.id, user_id)


async def _advance(
    bot: Bot,
    steps: list[FlowStep],
    chat_id: int,
    user_id: int,
    state: FlowState,
    *,
    step: FlowStep,
    value: str,
    label: str,
) -> None:
    """Record an answer, then show the next step or finish the flow."""
    state.values[step.id] = value
    state.labels[step.id] = label
    state.index += 1

    if state.index >= len(steps):
        await _finish(bot, steps, chat_id, user_id, state)
        return

    upcoming = steps[state.index]
    await set_state(bot.id, user_id, state)
    await _send_step(bot, chat_id, state.index, upcoming)
    if upcoming.is_terminal:
        await _finish(bot, steps, chat_id, user_id, state)


async def handle_option_tap(
    bot: Bot, config: BotConfigUnion, chat_id: int, user_id: int, data: str
) -> bool:
    """Process ``stepopt:{step}:{option}``. False → nothing was handled."""
    steps = parse_steps(config)
    if not steps:
        return False

    payload = data.removeprefix(OPTION_PREFIX)
    try:
        step_index_str, option_index_str = payload.split(":", 1)
        step_index, option_index = int(step_index_str), int(option_index_str)
    except ValueError:
        logger.warning("Malformed option callback %r", data)
        return False

    state = await get_state(bot.id, user_id)
    if state is None or state.index != step_index:
        # Stale button (old prompt or a restarted flow) — ignore quietly.
        return False

    step = steps[step_index]
    if option_index < 0 or option_index >= len(step.options):
        logger.warning("Option index %s out of range for step %s", option_index, step_index)
        return False

    option = step.options[option_index]
    await _advance(
        bot,
        steps,
        chat_id,
        user_id,
        state,
        step=step,
        value=option.value,
        label=option.label,
    )
    return True


async def handle_text_answer(
    bot: Bot, config: BotConfigUnion, chat_id: int, user_id: int, text: str
) -> bool:
    """Capture a free-text answer for the active flow. False → not applicable."""
    steps = parse_steps(config)
    if not steps:
        return False

    state = await get_state(bot.id, user_id)
    if state is None or state.index >= len(steps):
        return False

    step = steps[state.index]
    if not step.expects_text:
        return False

    await _advance(
        bot,
        steps,
        chat_id,
        user_id,
        state,
        step=step,
        value=text,
        label=text,
    )
    return True


async def cancel_flow(bot_id: int, user_id: int) -> bool:
    """Abandon an active flow. True when there was one."""
    state = await get_state(bot_id, user_id)
    await clear_state(bot_id, user_id)
    return state is not None


def parse_start_index(data: str) -> int:
    """Index from a ``step:{index}`` menu callback (0 when malformed)."""
    try:
        return max(0, int(data.removeprefix(START_PREFIX)))
    except ValueError:
        return 0
