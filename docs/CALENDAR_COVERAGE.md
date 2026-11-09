# Calendar Coverage — development branch

This feature requires human review and a future release decision. It is not a deployment instruction or evidence of production installation. Stable v0.4.0 predates these changes. No theme, dashboard, Home Tasks source, or existing image collection is migrated or replaced.

## Select calendars and authorize costs

Open **Activity Images → Settings → General**. The calendar list comes from Home Assistant states and the entity registry, including saved selections that are currently missing. No calendar names are assumed. Native **Devices & services → Activity Images → Configure** retains HA's calendar selectors.

| Mode | Monitor | Auto-generate missing images | Effect |
| --- | --- | --- | --- |
| Disabled | Off | Off | No calendar demand or normal Coverage rows. |
| Monitored only | On | Off | Coverage and image matching, without calendar-origin paid work. |
| Automatic | On | On | A later normal scan may queue verified missing titles. |

Newly discovered calendars start disabled. Newly selected calendars do not inherit automatic authorization. Enabling automatic generation requires a provider and a separate positive paid confirmation in both editors. A sidebar submission includes `confirm_auto: true` only after that confirmation. Concurrent settings changes invalidate the revision; the server never silently overwrites another editor.

`off` means no current active event, not unavailable. Missing, unknown, unavailable, disabled, or identity-changed calendars remain visible. Automatic work requires a stable registry identity. Unregistered calendars support monitoring only. After an entity rename or ID reuse, remove Monitor, save, then explicitly select the intended calendar again. This two-save procedure also repairs a missing identity binding; changing unrelated settings never repairs it silently.

## Use Calendar Coverage

- **Search / filters / calendar / sort / pagination:** read current grouped titles, counts and scan health. Counts can overlap: an ignored title can also have an available image.
- **Refresh coverage:** join or perform a resolve-only scan. It does not enqueue images or call AI, including when the browser disconnects during an overlapping automatic scan.
- **Assign image:** select a verified library asset, inspect its preview, then assign without AI. An existing exact association requires separate replacement confirmation. A normalized association can affect other spelling variants; unrelated aliases and files remain unchanged.
- **Generate…:** explicitly confirm a potentially paid candidate request. The existing Queue & history review workflow handles Accept or Discard; generation does not silently replace the current mapping.
- **Ignore exact title:** preserve the exact HA summary across all monitored calendars. Nothing is deleted, unlinked, hidden, or removed from the calendar.
- **Remove ignore:** remove only the ignore entry. This does not start a scan or an AI request. A later normal scan can authorize paid work when the calendar has automatic generation enabled.

The Ignored filter includes titles no longer scheduled. The raw title is rendered as inert, whitespace-preserving text. The existing normalized/fuzzy image cache remains independent of ignore identity.

These are independent ignore keys:

```json
["Basen 9:15", "Basen 9:16", "basen 9:15", "Basen 9:15 ", " Basen 9:15", "Basen", "Café", "Cafe\u0301"]
```

Comparison uses Python string equality on `calendar.get_events`' original `summary`, before trimming or Unicode normalization. A different unignored raw variant may independently authorize a shared normalized image. An explicitly confirmed manual candidate remains allowed while ignored and does not clear the ignore.

## Billing boundary and independent actions

| Entry point | Calendar policy / raw-ignore guard | Billing |
| --- | --- | --- |
| Coverage/discovery/search/filter/refresh/ignore edits | Resolve-only; no generation path | No AI request. |
| Monitoring-only changes / automatic disable | No generation caused by saving | No AI request caused by saving. |
| Normal scheduled/state/public calendar refresh | Current calendar authorization required | May queue automatic work. |
| Calendar-origin queued job and every retry | Current policy, source, exact ignore, freshness, cache and deletion checks | May dispatch only while authorized. |
| Confirmed manual candidate / explicit retry | Independent of calendar policy | Potentially paid; review remains required. |
| Public `generate_image`, explicit `resolve_images(enqueue_missing=true)`, original source API and Home Tasks wrapper | Existing explicit non-calendar contracts remain independent | Potentially paid when explicitly requested. |
| Confirmed deletion replacement | Existing deletion authorization, reservations and journal | Potentially paid within the reviewed replacement plan. |

A successful disable or Ignore acknowledgement prevents subsequent *new* unauthorized calendar attempts. It cannot cancel an already dispatched provider request, reverse billing, or guarantee that a provider stops internal retries. Results already returned remain subject to the existing persistence/review workflow.

Activity Images retries only an explicit HTTP 429 rejection, at most three service attempts total. Timeout, connection failure and 5xx outcomes may already have billed work and are not automatically retried. Provider-internal retries and charges are not bounded by this count.

Both **Clear recorded errors** and the public `activity_images.clear_errors` action now retain retry barriers. This intentionally changes the old public clear-and-requeue behavior. Use an explicitly confirmed Retry or candidate request for another attempt. The public action signature is unchanged.

## Persistence, migration and restart

The existing ConfigEntry remains the configuration authority:

- `calendar_entity_ids`: monitored calendars, maximum 10.
- `auto_generate_calendar_entity_ids`: explicit subset of monitored calendars.
- `_calendar_registry_ids`: private stable selection bindings, not an editable frontend field.
- `generate_missing_calendar_images`: legacy migration input only, persisted false after migration. It is not a second paid switch; old-style writes attempting to enable it are rejected.

Legacy false keeps selected calendars monitored with automatic generation off. Legacy true preserves authorization only for previously selected calendars. Migration binds the registry identities visible at that upgrade; it cannot reconstruct identities predating the feature. Repeating setup does not recapture replacement identities.

HA's supported `async_update_entry` owns options persistence and live notifications. A successful save is the event-loop authorization boundary; this does not claim that HA's delayed ConfigEntry disk write has already been fsynced. Ignored titles use a separate HA Store with exact strings and integer revision. Writes are atomic, read-back verified and drained before publishing the new in-memory revision or acknowledging success.

A corrupt/unreadable ignore list blocks calendar automatic generation and ignore mutations, but keeps the library, monitoring and explicit manual workflows available. The panel surfaces the storage error. Repair the storage from a consistent recovery copy; do not replace it with an empty list merely to clear the warning.

The existing calendar snapshot keeps its original per-calendar title map and adds bounded raw occurrences and identity metadata for display. Restored data is stale and never authorizes paid work. Legacy trimmed snapshots do not invent original whitespace. Paid identity bindings come from ConfigEntry options, never from a missing/corrupt display snapshot.

The queue persists origin/source records and a running-attempt boundary before dispatch. Legacy unclassified pending jobs require explicit review/retry. Interrupted provider attempts never replay automatically. Untouched explicit pending jobs remain resumable. Provenance-aware calendar jobs restored before the first fresh scan may record a harmless suppression; a later complete normal scan can enqueue eligible demand again. This is conservative restart churn, not an additional AI request.

## Deletion and concurrency

Ignore affects billing, not image ownership. Ignored titles remain real consumers in fresh deletion plans. Coverage never edits the deletion journal, tombstones, quarantine, image files, or calendar events. Existing free-assignment conflict checks and deletion reservations remain authoritative on the server.

Lock order remains **calendar → queue → generator → cache**:

| Operation | Locks / boundary |
| --- | --- |
| Settings and ignore writes | Calendar first; publish policy only at the validated acknowledgement boundary. |
| Calendar scan | Calendar, then queue for enqueue; no generator lock is acquired by the scanner. |
| Coverage | Calendar for coherent raw groups/ignore revision; briefly queue for a job snapshot, released before bounded local cache lookups. Inventory runs before the calendar lock. |
| Queue worker | Queue for durable state changes, released before entering the generator. |
| Automatic provider guard | Reads current immutable/rebound calendar state without acquiring calendar after generator; rechecks after cache I/O and before each retry. |
| Permanent deletion | Existing calendar → queue → generator → cache hierarchy and fresh, complete demand verification. |

Coverage can briefly delay settings/scans while resolving at most 64 title groups. There is no duplicate event database, new queue, new frontend framework, or new theme dependency.

## Bounds and honest limits

Scans retain the existing 1–60 day lookahead, 15-minute polling plus state-change refreshes, 512 events per calendar, and 64 distinct raw/resolution title bounds. Cancelled records are omitted when reported; an absent UID/status is not treated as cancellation. Providers expand recurring events. Missing times remain unknown rather than invented.

Malformed, failed, stale or truncated demand cannot authorize automatic work or unsafe permanent deletion. The last display snapshot can remain visible with its error/freshness state. Image inventory retains the existing short cache; mutations and authenticated preview reads revalidate assets. Pagination reflects changing data, not an immutable collection.

## Human upgrade and rollback preparation

1. Review the Draft PR, validation record, billing changes and current deletion operations. No merge, release, installation or production restart is authorized by this development work.
2. Before a future installation, preserve a consistent copy of ConfigEntry data/options, image/cache/jobs/calendar/ignore Stores, all owned images and deletion quarantine. Record existing paid authorization.
3. Test legacy false/true migration and production-like calendar providers in a disposable environment. Confirm actual discovered names, unavailable selections and new-calendar defaults.
4. Review or cancel waiting automatic jobs before a planned downgrade. An older queue may resume its `pending` list without understanding calendar provenance, even when its legacy global auto flag is false.
5. Stop the integration before replacing code or restoring a consistent recovery copy. Do not restore an old cache/journal over a running instance. Never remove tombstones or confirmed deletion records to make a downgrade appear successful.
6. Older code may not understand new policy keys or operational metadata. Review code, options and queue compatibility together; copying only an old Python file is not a rollback plan. Verify paid work remains disabled before allowing normal operation.

## Research decisions and validation

Adopted: shared existing scanner/cache/library/queue, raw-summary identity, global exact ignore, admin-only WS, explicit paid candidates, and strict deletion consumers. Changed: two selector-friendly subset lists instead of a policy-object map; no optional live event subscription; no new event database; only explicit 429 retry; ConfigEntry-owned identity bindings instead of trusting the display snapshot. These choices reduce state duplication and prevent billing after stale consent.

Sources checked against installed HA Core 2026.11.0 and current official documentation:

- [Calendar entity API](https://developers.home-assistant.io/docs/core/entity/calendar/): recurrence expansion, state `off`, optional UID/status and update-listener behavior.
- [Config entries and migration](https://contributors.home-assistant.io/core/integrations/config-entries/), [Options Flow](https://contributors.home-assistant.io/core/integrations/options-flow/): supported options transactions and no-reload listeners.
- [Entity registry](https://contributors.home-assistant.io/core/integrations/entity-registry/): stable registry entries versus mutable entity IDs.
- [WebSocket commands](https://developers.home-assistant.io/docs/frontend/extending/websocket-api/): schema registration and `require_admin`.

See [the validation record](CALENDAR_COVERAGE_VALIDATION.md) for exact executed checks, review findings, remaining limitations and release prerequisites. Passing isolated tests does not establish production readiness or real-provider billing behavior.
