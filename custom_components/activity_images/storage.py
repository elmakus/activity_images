"""Verify native Store commits without trusting its logged-and-swallowed errors."""
from __future__ import annotations

import json
from pathlib import Path


async def async_save_verified(hass, store, snapshot: dict) -> None:
    """Read the actual envelope off-loop, never Store's potentially cached load."""
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
