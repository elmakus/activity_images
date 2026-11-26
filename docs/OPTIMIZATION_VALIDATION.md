# Activity Images: focused simplification and optimization

This is an isolated development candidate, not a deployed release. All matching decisions, including thresholds, token rules and tie order, remain unchanged. Existing assets, associations and entity IDs are preserved.

## Remaining audit improvements

- One native ConfigEntry runtime; remove the redundant runtime dictionary and shared cache singleton. Do not publish the runtime until setup succeeds.
- Reuse one cancellation-draining helper and one terminal/review queue transition. Keep locks required for serialization, durable rollback and same-title synchronous reuse. Do not introduce another scheduler or persistence backend.
- Avoid copying the whole cache on read-only recency hits. Copy only affected entries/operations for rollback; keep a detached durable Store snapshot. Commit generated provenance and a good automatic association together.
- Check live calendar demand immediately before every deletion replacement provider attempt, including explicit HTTP 429 retries. The independent read-only query avoids reversing calendar -> queue -> engine lock order. Reject changed settings or demand; retain originals.
- Store uncertain-background outputs for explicit review. Public generation and the AI Task wrapper report review rather than returning an image as a successful automatic assignment. Deletion replacements stop with the original intact; an operator can abandon the operation and explicitly link the saved candidate, or authorize another attempt. A recovery without metadata must demonstrate transparency before reuse.
- Keep 100 detailed terminal deletion journals. Older terminal journals retain compact idempotency receipts; active recovery evidence, all tombstones, existing assets and payment protections are retained. Archived receipts are not rendered in the default active history.
- Request true alpha-channel transparency in the default prompt, with a uniform chroma-key fallback. Automatically upgrade only the byte-identical former built-in default; never rewrite a custom prompt override.
- Share summary validation between normal calendar scans and replacement-demand verification. Reduce repeated options construction, title normalization and file metadata reads. Remove redundant image copies where output is unchanged.
- Preserve the native direct-request lock for synchronous same-title reuse. No unnecessary architecture rewrite.

## Reproducible synthetic benchmark

Run from the repository root:

```sh
python tests/benchmark_cache.py
```

Comparison: preceding candidate 8947f0c9961595f2e1ce3df5bfc1190391231960 versus this development tree, same script on the same workstation and Python 3.14.4 environment. 4,000 synthetic aliases share one local PNG. Median of five repetitions except the cold batch (one repetition), each measured in a separate process. No real HA stores or AI provider calls.

| Workload | Before (ms) | After (ms) |
| --- | ---: | ---: |
| 64 individual warm lookups | 926.41 | 37.06 |
| 64-title warm batch | 27.32 | 7.97 |
| 64 shared dimension lookups | 15.92 | 1.11 |
| Single fuzzy lookup | 71.14 | 71.60 |
| Cold 64-title batch | 71.08 | 58.49 |

Individual lookup CPU median: 926.03 -> 35.33 ms. Batch CPU: 26.31 -> 8.52 ms. Shared dimensions CPU: 14.93 -> 1.08 ms. Whole-process peak RSS 69,860 -> 67,476 KiB. Both runs performed zero warm writes and one cold Store write. Newly generated, automatically accepted image metadata and mapping now require one cache Store transaction instead of two (regression test). These are synthetic, not production load or long-running RAM measurements. The fuzzy scoring implementation is intentionally unchanged; small timing differences are not evidence of a regression or improvement.

See the audit regression tests for durable failure/rollback, original matching decisions, old default migration, candidate review, history receipts, unchanged Home Tasks contract for accepted images, and replacement authorization.
