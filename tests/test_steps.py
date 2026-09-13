"""Tests for the ``steps`` primitive — multi-step flows (Phase 2, S2.2).

Covers the pieces that were previously missing entirely: parsing the flow
rider, per-user state in Redis, option/free-text routing, quiz scoring,
owner delivery, and the inert-when-unused guarantee.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from tme.schemas.bot_config import BotConfigSchema
from tme.services import steps as steps_module
from tme.services.steps import (
    FlowState,
    FlowStep,
    StepOption,
    cancel_flow,
    deliver_to_owner,
    get_state,
    handle_option_tap,
    handle_text_answer,
    parse_start_index,
    parse_steps,
    start_flow,
    start_flow_at,
)
from tme.templates import get_template


def _run(coro):
    """Run a coroutine (repo convention: no pytest-asyncio)."""
    return asyncio.run(coro)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.ttls: dict[str, int | None] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self.store[key] = value
        self.ttls[key] = ex

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


class _FakeBot:
    """Records outgoing messages; stands in for aiogram's Bot."""

    def __init__(self, bot_id: int = 555) -> None:
        self.id = bot_id
        self.sent: list[dict] = []
        self.fail_send = False

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        if self.fail_send:
            raise RuntimeError("bot was blocked by the user")
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})


@pytest.fixture
def fake_redis(monkeypatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(steps_module, "redis_client", fake)
    return fake


@pytest.fixture
def delivered(monkeypatch) -> list[tuple[int, str]]:
    """Capture owner deliveries instead of hitting the DB + Telegram."""
    calls: list[tuple[int, str]] = []

    async def _fake_deliver(bot, bot_id: int, body: str) -> bool:
        calls.append((bot_id, body))
        return True

    monkeypatch.setattr(steps_module, "deliver_to_owner", _fake_deliver)
    return calls


def _feedback():
    return get_template("feedback_collector").seed


def _quiz():
    return get_template("quiz").seed


def _form():
    return get_template("simple_form").seed


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
class TestParseSteps:
    def test_seed_flows_parse(self) -> None:
        assert [s.id for s in parse_steps(_feedback())] == ["rating", "comment"]
        assert [s.id for s in parse_steps(_quiz())] == ["q1", "q2", "q3", "result"]
        assert [s.id for s in parse_steps(_form())] == ["name", "contact", "message", "done"]

    def test_bot_without_the_module_is_inert(self) -> None:
        plain = BotConfigSchema.model_validate({"welcome_message": "hi"})
        assert parse_steps(plain) == []

    def test_module_without_steps_is_inert(self) -> None:
        cfg = BotConfigSchema.model_validate({"active_modules": ["steps"], "steps": []})
        assert parse_steps(cfg) == []

    def test_malformed_entries_are_dropped(self) -> None:
        cfg = BotConfigSchema.model_validate(
            {
                "active_modules": ["steps"],
                "steps": [
                    {"id": "ok", "prompt": "fine"},
                    {"nope": "missing required fields"},
                    {"id": "x", "prompt": ""},
                ],
            }
        )
        assert [s.id for s in parse_steps(cfg)] == ["ok"]

    def test_non_list_steps_is_ignored(self) -> None:
        cfg = BotConfigSchema.model_validate({"active_modules": ["steps"], "steps": "oops"})
        assert parse_steps(cfg) == []

    def test_start_index_parsing(self) -> None:
        assert parse_start_index("step:0") == 0
        assert parse_start_index("step:2") == 2
        assert parse_start_index("step:") == 0
        assert parse_start_index("garbage") == 0


class TestStepModel:
    def test_expects_text_semantics(self) -> None:
        assert FlowStep(id="a", prompt="p", answer_type="free_text").expects_text is True
        # `auto` with no options → free text; with options → a choice.
        assert FlowStep(id="a", prompt="p").expects_text is True
        choice = FlowStep(id="a", prompt="p", options=[StepOption(label="A", value="a")])
        assert choice.expects_text is False
        assert FlowStep(id="a", prompt="p", answer_type="none").is_terminal is True

    def test_score_counts_only_graded_steps(self) -> None:
        steps = [
            FlowStep(id="q1", prompt="p", correct_answers=["a"]),
            FlowStep(id="q2", prompt="p", correct_answers=["b"]),
            FlowStep(id="note", prompt="p", answer_type="none"),
        ]
        state = FlowState(values={"q1": "a", "q2": "zzz"})
        assert state.score(steps) == (1, 2)


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
class TestState:
    def test_roundtrip_and_clear(self, fake_redis) -> None:
        _run(steps_module.set_state(1, 2, FlowState(index=1, values={"a": "b"})))
        assert fake_redis.ttls["flowstate:1:2"] == steps_module.STATE_TTL

        loaded = _run(get_state(1, 2))
        assert loaded is not None and loaded.index == 1 and loaded.values == {"a": "b"}

        assert _run(cancel_flow(1, 2)) is True
        assert _run(get_state(1, 2)) is None

    def test_cancel_without_flow(self, fake_redis) -> None:
        assert _run(cancel_flow(9, 9)) is False

    def test_corrupt_state_is_discarded(self, fake_redis) -> None:
        fake_redis.store["flowstate:1:2"] = b"{not json"
        assert _run(get_state(1, 2)) is None


# --------------------------------------------------------------------------- #
# Flow orchestration
# --------------------------------------------------------------------------- #
class TestFlow:
    def test_start_sends_first_prompt_with_options(self, fake_redis, delivered) -> None:
        bot = _FakeBot()
        assert _run(start_flow_at(bot, _feedback(), chat_id=100, user_id=7, index=0)) is True

        assert bot.sent[0]["text"] == "How would you rate us? ⭐"
        keyboard = bot.sent[0]["reply_markup"]
        payloads = [b.callback_data for row in keyboard.inline_keyboard for b in row]
        assert payloads[:4] == ["stepopt:0:0", "stepopt:0:1", "stepopt:0:2", "stepopt:0:3"]
        assert "stepcancel" in payloads  # always offer a way out
        assert delivered == []  # nothing delivered yet

    def test_option_tap_advances_to_next_step(self, fake_redis, delivered) -> None:
        bot = _FakeBot()
        _run(start_flow_at(bot, _feedback(), chat_id=100, user_id=7, index=0))

        handled = _run(handle_option_tap(bot, _feedback(), 100, 7, "stepopt:0:0"))

        assert handled is True
        assert len(bot.sent) == 2
        assert bot.sent[1]["text"].startswith("Thanks!")
        state = _run(get_state(bot.id, 7))
        assert state is not None and state.values["rating"] == "excellent"
        assert state.labels["rating"] == "😍 Excellent"

    def test_free_text_completes_and_delivers(self, fake_redis, delivered) -> None:
        bot = _FakeBot()
        cfg = _feedback()
        _run(start_flow_at(bot, cfg, chat_id=100, user_id=7, index=0))
        _run(handle_option_tap(bot, cfg, 100, 7, "stepopt:0:1"))

        handled = _run(handle_text_answer(bot, cfg, 100, 7, "Great product!"))

        assert handled is True
        assert delivered and delivered[0][0] == bot.id
        assert "rating</b>: 🙂 Good" in delivered[0][1]
        assert "Great product!" in delivered[0][1]
        assert _run(get_state(bot.id, 7)) is None  # flow cleared

    def test_quiz_scores_and_reports_at_the_end(self, fake_redis, delivered) -> None:
        bot = _FakeBot()
        cfg = _quiz()
        _run(start_flow_at(bot, cfg, chat_id=100, user_id=7, index=0))

        _run(handle_option_tap(bot, cfg, 100, 7, "stepopt:0:0"))  # paris ✅
        _run(handle_option_tap(bot, cfg, 100, 7, "stepopt:1:0"))  # mars ✅
        assert delivered == []  # not finished yet

        _run(handle_option_tap(bot, cfg, 100, 7, "stepopt:2:1"))  # 7 ✅
        assert len(delivered) == 1
        body = delivered[0][1]
        assert "Score: <b>3/3</b>" in body
        assert bot.sent[-1]["text"].startswith("🎉")  # terminal prompt shown
        assert _run(get_state(bot.id, 7)) is None

    def test_simple_form_collects_three_free_text_answers(self, fake_redis, delivered) -> None:
        bot = _FakeBot()
        cfg = _form()
        _run(start_flow(bot, cfg, chat_id=100, user_id=7))

        _run(handle_text_answer(bot, cfg, 100, 7, "Ada"))
        _run(handle_text_answer(bot, cfg, 100, 7, "@ada"))
        assert delivered == []

        _run(handle_text_answer(bot, cfg, 100, 7, "Hello there"))
        assert len(delivered) == 1
        body = delivered[0][1]
        assert "name</b>: Ada" in body and "message</b>: Hello there" in body
        assert "Score" not in body  # not a graded flow

    def test_commands_are_not_answers(self, fake_redis, delivered) -> None:
        """on_fallback guards this, but the engine must ignore non-text steps too."""
        bot = _FakeBot()
        cfg = _feedback()
        _run(start_flow_at(bot, cfg, chat_id=100, user_id=7, index=0))

        # Step 0 is a choice → a stray message must not be recorded.
        assert _run(handle_text_answer(bot, cfg, 100, 7, "hello?")) is False

    def test_stale_option_tap_is_ignored(self, fake_redis, delivered) -> None:
        bot = _FakeBot()
        cfg = _feedback()
        _run(start_flow_at(bot, cfg, chat_id=100, user_id=7, index=0))

        # Button from step 1 while the flow is on step 0.
        assert _run(handle_option_tap(bot, cfg, 100, 7, "stepopt:1:0")) is False

    def test_option_tap_without_state_is_ignored(self, fake_redis, delivered) -> None:
        bot = _FakeBot()
        assert _run(handle_option_tap(bot, _feedback(), 100, 7, "stepopt:0:0")) is False

    def test_out_of_range_option_is_ignored(self, fake_redis, delivered) -> None:
        bot = _FakeBot()
        cfg = _feedback()
        _run(start_flow_at(bot, cfg, chat_id=100, user_id=7, index=0))
        assert _run(handle_option_tap(bot, cfg, 100, 7, "stepopt:0:99")) is False

    def test_malformed_option_payload_is_ignored(self, fake_redis, delivered) -> None:
        bot = _FakeBot()
        assert _run(handle_option_tap(bot, _feedback(), 100, 7, "stepopt:oops")) is False

    def test_index_is_clamped_and_terminal_start_finishes(self, fake_redis, delivered) -> None:
        bot = _FakeBot()
        cfg = _form()
        # Start at the terminal "done" step → prompt shown, nothing collected.
        assert _run(start_flow_at(bot, cfg, chat_id=100, user_id=7, index=99)) is True
        assert bot.sent[-1]["text"].startswith("✅")
        assert _run(get_state(bot.id, 7)) is None

    def test_non_step_bot_is_never_claimed(self, fake_redis, delivered) -> None:
        bot = _FakeBot()
        plain = BotConfigSchema.model_validate({"welcome_message": "hi"})
        assert _run(start_flow(bot, plain, chat_id=100, user_id=7)) is False
        assert _run(handle_text_answer(bot, plain, 100, 7, "hi")) is False
        assert bot.sent == []


# --------------------------------------------------------------------------- #
# Owner delivery
# --------------------------------------------------------------------------- #
class TestDelivery:
    def _install_session(self, monkeypatch, owner_telegram_id) -> None:
        class _FakeResult:
            def __init__(self, value) -> None:
                self._value = value

            def scalar_one_or_none(self):
                return self._value

        class _FakeSession:
            async def execute(self, _statement):
                return _FakeResult(owner_telegram_id)

        @asynccontextmanager
        async def scope():
            yield _FakeSession()

        monkeypatch.setattr(steps_module, "session_scope", scope)

    def test_delivers_to_owner(self, monkeypatch) -> None:
        self._install_session(monkeypatch, 4242)
        bot = _FakeBot()

        assert _run(deliver_to_owner(bot, bot.id, "body")) is True
        assert bot.sent[0]["chat_id"] == 4242
        assert "New responses" in bot.sent[0]["text"]

    def test_missing_owner_is_reported_not_raised(self, monkeypatch) -> None:
        self._install_session(monkeypatch, None)
        assert _run(deliver_to_owner(_FakeBot(), 1, "body")) is False

    def test_blocked_owner_never_breaks_the_guest_flow(self, monkeypatch) -> None:
        self._install_session(monkeypatch, 4242)
        bot = _FakeBot()
        bot.fail_send = True

        assert _run(deliver_to_owner(bot, bot.id, "body")) is False

    def test_empty_summary_says_so(self, fake_redis) -> None:
        assert steps_module._summary([], FlowState()) == "(no answers)"
