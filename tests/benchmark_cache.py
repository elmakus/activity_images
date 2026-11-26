"""Synthetic cache workload; no provider or real HA data."""

import asyncio
import json
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests"))
from test_behavior import MockHA, cache_module
from PIL import Image


async def main():
    with tempfile.TemporaryDirectory() as directory:
        hass = MockHA(directory)
        cache = cache_module.ActivityImageCache(hass)
        await cache.async_setup()
        path = hass.base / "www/activity_images/fixture.png"
        path.parent.mkdir(parents=True)
        Image.new("RGBA", (32, 32), "red").save(path)
        url = "/local/activity_images/fixture.png"
        now = time.time()
        cache._entries = {
            f"activity number {i}": {
                "title": f"Activity number {i}",
                "image_url": url,
                "tokens": ["activity", "number"],
                "last_used_at": now,
            }
            for i in range(4000)
        }
        cache._rebuild_token_index()
        queries = [f"Activity number {i}" for i in range(64)]
        writes = 0
        original = cache._async_save

        async def counted():
            nonlocal writes
            writes += 1
            await original()

        cache._async_save = counted

        async def measure(fn, repeats=5):
            durations = []
            cpu = []
            for _ in range(repeats):
                start = time.perf_counter()
                p = time.process_time()
                await fn()
                durations.append(time.perf_counter() - start)
                cpu.append(time.process_time() - p)
            return {
                "median_ms": round(statistics.median(durations) * 1000, 2),
                "median_cpu_ms": round(statistics.median(cpu) * 1000, 2),
            }

        async def exact():
            for title in queries:
                assert await cache.async_find(title) == url

        async def batch():
            assert len(await cache.async_find_many(queries)) == 64

        async def fuzzy_lookup():
            assert await cache.async_find("Activity numbere 1001", touch=False) == url

        async def dimensions():
            assert (
                len(await cache.async_image_metadata({title: url for title in queries}))
                == 64
            )

        result = {
            "exact64": await measure(exact),
            "batch64": await measure(batch),
            "dimensions64": await measure(dimensions),
            "fuzzy": await measure(fuzzy_lookup),
            "warm_writes": writes,
        }
        for entry in cache._entries.values():
            entry["last_used_at"] = 0
        result["cold_batch"] = await measure(batch, 1)
        result["total_writes"] = writes
        result["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(json.dumps(result, sort_keys=True))


asyncio.run(main())
