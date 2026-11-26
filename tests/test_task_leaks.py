"""The suite's own guard against tasks a test leaves running (tests/conftest.py)."""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import _cancel_hard


async def test_a_task_that_swallows_one_cancellation_is_cancelled_again_and_ends() -> None:
    async def cleanup_without_timeout() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(3600)  # a close that would never come back

    task = asyncio.create_task(cleanup_without_timeout())
    await asyncio.sleep(0)
    assert await _cancel_hard([task], 2.0) == set()
    assert task.done()


async def test_a_task_that_ignores_every_cancellation_is_reported_as_stuck() -> None:
    stop = asyncio.Event()

    async def stubborn() -> None:
        while not stop.is_set():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue

    task = asyncio.create_task(stubborn())
    await asyncio.sleep(0)
    stuck = await _cancel_hard([task], 0.5)
    assert stuck == {task}
    stop.set()  # let it end, or this test's own loop could not close
    await asyncio.wait({task}, timeout=2)
    assert task.done()


async def test_a_task_started_by_a_cancellation_is_cancelled_too() -> None:
    from tests.conftest import _cancel_until_quiet

    started: list[asyncio.Task[None]] = []

    async def cleanup() -> None:  # a close in a task of its own, like SQLAlchemy's
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(3600)  # swallows the first cancellation

    async def turn() -> None:
        try:
            await asyncio.sleep(3600)
        finally:
            inner = asyncio.ensure_future(cleanup())
            started.append(inner)
            await asyncio.shield(inner)

    me = asyncio.current_task()
    before = set(asyncio.all_tasks())
    task = asyncio.create_task(turn())
    await asyncio.sleep(0)

    def running() -> set[asyncio.Task[object]]:
        return {t for t in asyncio.all_tasks() if t is not me and t not in before and not t.done()}

    # Cancelling only the tasks the test left ends them, and leaves the cleanup they started: the
    # loop's closing then cancels it once, it swallows that, and the closing waits for good.
    assert await _cancel_hard([task], 1.0) == set()
    assert task.done() and started and not started[0].done()
    assert await _cancel_until_quiet(running, 3.0) == set()
    assert started[0].done()


def test_the_loops_closing_names_a_task_that_ignores_it(monkeypatch: pytest.MonkeyPatch) -> None:
    import tests.conftest as conftest

    monkeypatch.setattr(conftest, "CLOSE_WAIT_SECONDS", 0.2)

    async def deaf() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(1)  # ignores the closing's one cancellation for a while

    left: list[asyncio.Task[None]] = []

    async def leave() -> None:
        left.append(asyncio.get_running_loop().create_task(deaf(), name="deaf-task"))
        await asyncio.sleep(0)

    runner = asyncio.Runner()
    runner.run(leave())
    with pytest.raises(pytest.fail.Exception, match="deaf-task"):
        runner.close()  # without the bound, this waited for the task (forever, for a deaf one)
    left[0]._log_destroy_pending = False  # ty: ignore[missing-attribute]  # expected, not a leak
