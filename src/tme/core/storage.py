"""aiogram FSM storage backed by Redis, with multi-tenant key isolation.

The critical requirement (spec §3.3): with millions of users spread across
thousands of bots, no two conversations may ever collide in the FSM keyspace.

We achieve that with aiogram's :class:`DefaultKeyBuilder` configured with
``with_bot_id=True``. Every storage key it produces embeds **both** the bot id
and the user/chat id, e.g.::

    state:<bot_id>:<chat_id>:<user_id>          # aiogram >= 3.x layout

So bot A's user 42 and bot B's user 42 are stored under different keys — exactly
the ``state:{bot_id}:{user_id}`` isolation the architecture calls for, at a finer
(per-chat) granularity.
"""

from __future__ import annotations

from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage

from tme.core.redis_client import redis_client

# `with_bot_id=True` is what guarantees cross-tenant isolation.
# `with_destiny=True` keeps aiogram's scene/destiny feature namespaced too.
_key_builder = DefaultKeyBuilder(
    prefix="state",
    separator=":",
    with_bot_id=True,
    with_destiny=True,
)

#: Single shared FSM storage used by the tenant dispatcher (and main dispatcher).
storage: RedisStorage = RedisStorage(
    redis=redis_client,
    key_builder=_key_builder,
)
