"""Verify native Store commits without trusting its logged-and-swallowed errors."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping
from typing import TYPE_CHECKING, TypeVar
import json
from pathlib import Path

from homeassistant.core import CoreState, HomeAssistant

if TYPE_CHECKING:
    from homeassistant.helpers.storage import Store

T = TypeVar("T")
SnapshotT = TypeVar("SnapshotT", bound=Mapping[str, object])


async def async_save_verified(hass: HomeAssistant, store: Store[SnapshotT], snapshot: SnapshotT) -> None:
    """Read the actual envelope off-loop, never Store's potentially cached load."""
    # Store defers stopping saves to final-write; reject before it retains a mutation.
    if hass.state is CoreState.stopping:
        raise OSError("Activity Images cannot persist mutations while Home Assistant is stopping")
    await store.async_save(snapshot)

    def verify() -> None:
        try:
            with Path(store.path).open(encoding="utf-8") as stream:
                envelope = json.load(stream)
            if not isinstance(envelope, dict) or envelope.get("data") != snapshot:
                raise ValueError("Stored data differs from requested snapshot")
        except (OSError, ValueError) as exc:
            raise OSError("Activity Images persistence verification failed") from exc

    await hass.async_add_executor_job(verify)



async def async_drain(awaitable: Awaitable[T]) -> tuple[T, bool]:
    """Complete an owned operation despite caller cancellation.

    Return the result and whether cancellation was requested, so the caller
    can publish its durable state before propagating cancellation.
    """
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return await task, cancelled
