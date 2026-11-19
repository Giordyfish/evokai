"""State shared by every instance: memory and Redis stores, and the limits that use them."""

from __future__ import annotations

import dataclasses
import time
from typing import Any

import fakeredis
import pytest

from evokai.core.config import Settings
from evokai.core.security import Keyring
from evokai.core.shared import MemoryState, RedisState, SharedState, build_shared
from evokai.db import models as m
from evokai.guard import Guard, GuardRefused
from evokai.identity.auth import Authenticator
from evokai.privacy import subject_key
from tests.conftest import Env


def _redis(server: fakeredis.FakeServer | None = None) -> RedisState:
    """A Redis store; two made on the same server are two instances sharing one Redis."""
    client = fakeredis.FakeAsyncRedis(
        server=server or fakeredis.FakeServer(), decode_responses=True
    )
    return RedisState("redis://test", client=client)


@pytest.fixture(params=["memory", "redis"])
def store(request: pytest.FixtureRequest) -> SharedState:
    return MemoryState() if request.param == "memory" else _redis()


async def test_counters_expire_and_never_go_below_zero(store: SharedState) -> None:
    assert await store.incr("a", 0.05) == 1
    assert await store.incr("a", 60) == 2
    time.sleep(0.08)
    assert await store.incr("a", 60) == 1  # the expiry was set once, when it was created
    assert await store.acquire("run", 60) == 1
    assert await store.acquire("run", 60) == 2
    assert await store.count("run") == 2
    assert await store.release("run") == 1
    assert await store.release("run") == 0
    assert await store.release("run") == 0
    assert await store.count("run") == 0


async def test_keys_set_once_and_taken_once(store: SharedState) -> None:
    assert await store.set_once("k", "1", 60) is True
    assert await store.set_once("k", "2", 60) is False
    await store.set("code", "login", 60)
    assert await store.take("code") == "login"
    assert await store.take("code") is None
    await store.set("gone", "x", 0.05)
    time.sleep(0.08)
    assert await store.get("gone") is None
    await store.delete("k")
    assert await store.get("k") is None


async def test_redis_keys_carry_the_prefix() -> None:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    s = RedisState("redis://test", prefix="dep1:", client=client)
    await s.incr("x", 60)
    assert await client.get("dep1:x") == "1"
    assert await s.ping() is True


class _Down:
    """A Redis client whose server never answers."""

    def __getattr__(self, name: str) -> Any:
        def fail(*_a: Any, **_k: Any) -> Any:
            raise ConnectionError("down")

        return fail


async def test_redis_down_fails_open_on_this_instances_memory(
    caplog: pytest.LogCaptureFixture,
) -> None:
    s = RedisState("redis://test", client=_Down())
    assert await s.incr("a", 60) == 1
    assert await s.incr("a", 60) == 2  # counted in memory meanwhile, never refused
    await s.set("code", "v", 60)
    assert await s.take("code") == "v"
    assert await s.ping() is False and s.last_error == "ConnectionError"
    assert [r for r in caplog.records if r.getMessage() == "shared_state_unavailable"]


def test_the_settings_choose_the_store() -> None:
    assert isinstance(build_shared(None), MemoryState)
    assert isinstance(build_shared("redis://localhost:6390/0"), RedisState)
    assert Settings(redis_url="redis://valkey:6379/0").redis_url == "redis://valkey:6379/0"


# limits across instances


def _App() -> m.Application:
    return m.Application(
        id="app1",
        slug="app1",
        name="App",
        allowed_origins=[],
        security={
            "modes": {"abuse.concurrency": "enforce", "abuse.rate": "enforce"},
            "abuse": {"concurrent_turns": 1, "requests_per_minute": 3},
        },
    )


class _Agent:
    name = "helper"
    instructions = ""
    security = None


class _Events:
    async def add_many(self, _rows: list[dict[str, Any]]) -> None:
        return None


class _Store:
    security_events = _Events()


def _guard(shared: SharedState) -> Guard:
    settings = Settings(signing_key="s" * 32)
    return Guard(_Store(), settings, Keyring(settings), shared)


async def test_two_instances_share_concurrency_and_rate_through_redis() -> None:
    server = fakeredis.FakeServer()
    one, two = _guard(_redis(server)), _guard(_redis(server))

    def turn(g: Guard) -> Any:
        return g.turn(
            _App(), _Agent(), subject="u1", session_id="s1", turn_id="t1", channel="browser"
        )

    first = turn(one)
    await one.admit(first)
    await one.started(first)
    second = turn(two)
    with pytest.raises(GuardRefused) as busy:
        await two.admit(second)
    assert busy.value.code == "too_busy"
    await one.finished(first)
    await two.admit(turn(two))  # the third request of the minute, on the other instance
    with pytest.raises(GuardRefused) as rate:
        await one.admit(turn(one))
    assert rate.value.code == "rate_limited"


async def test_separate_memory_does_not_share() -> None:
    one, two = _guard(MemoryState()), _guard(MemoryState())
    t = one.turn(_App(), _Agent(), subject="u1", session_id="s", turn_id="t", channel="browser")
    await one.started(t)
    await two.admit(
        two.turn(_App(), _Agent(), subject="u1", session_id="s", turn_id="t", channel="browser")
    )


async def test_tool_rate_counts_on_every_instance() -> None:
    server = fakeredis.FakeServer()
    one, two = _guard(_redis(server)), _guard(_redis(server))
    app = _App()
    app.security = {"modes": {"tools.rate": "enforce"}, "tools": {"rate_per_minute": {"*": 1}}}

    def turn(g: Guard) -> Any:
        return g.turn(app, _Agent(), subject="u1", session_id="s", turn_id="t", channel="browser")

    assert (await turn(one).check_call("orders_month", {}, None)).allowed
    assert not (await turn(two).check_call("orders_month", {}, None)).allowed


async def test_two_authenticators_share_revocations_and_rate_windows() -> None:
    server = fakeredis.FakeServer()
    settings = Settings(signing_key="s" * 32)
    keyring = Keyring(settings)
    one = Authenticator(None, keyring, _redis(server))  # ty: ignore[invalid-argument-type]
    two = Authenticator(None, keyring, _redis(server))  # ty: ignore[invalid-argument-type]
    assert await one.count("token:x", 2, 60) and await two.count("token:x", 2, 60)
    assert not await one.count("token:x", 2, 60)
    two._revoked.set(subject_key(keyring.signing_key, "app", "u1"), 0.0)  # cached: never
    await one.forget_revocation("app", "u1")
    assert await two._revoked_at("app", "u1") > time.time() - 5
    two._keys.set("key", "cached")  # ty: ignore[invalid-argument-type]
    await two._sync()
    await one.forget_cached_keys()
    two._epoch_read = 0.0
    await two._sync()
    assert two._keys.get("key") is None


async def test_a_login_code_issued_by_one_instance_works_on_another(env: Env) -> None:
    import httpx

    from evokai.main import create_app

    server = fakeredis.FakeServer()
    env.svc.shared = _redis(server)
    other_svc = dataclasses.replace(env.svc, shared=_redis(server))
    other = create_app(env.svc.settings, other_svc)

    r = await env.client.post(
        "/agent/v1/console/login-code", headers={"Authorization": f"Bearer {env.admin_key}"}
    )
    assert r.status_code == 200, r.text
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=other), base_url="http://evokai"
    ) as c:
        r2 = await c.post(
            "/agent/v1/console/login",
            json={"code": r.json()["code"]},
            headers={"X-Evokai-Console": "1"},
        )
        assert r2.status_code == 204, r2.text
        again = await c.post(
            "/agent/v1/console/login",
            json={"code": r.json()["code"]},
            headers={"X-Evokai-Console": "1"},
        )
        assert again.status_code == 401
