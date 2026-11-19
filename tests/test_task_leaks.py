"""The suite's own guard against tasks a test leaves running (tests/conftest.py)."""

from __future__ import annotations

import asyncio

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
