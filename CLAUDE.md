# Working in this repository

Home Assistant custom integration for **Quickpac** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

## Carrier-specific notes

**API mechanics live in `carrier-research/api/quickpac/` (private research repo)** — the
endpoint, the OpenAPI-pinned payload, the status-code table and probe results.
Do not duplicate them here; this file is HA-integration decisions only.

Closest sibling: **`ha-planzer`** — the other Swiss keyless code-based carrier,
`model: code`, `auth: none`, number alone, no postcode second factor, naive
timestamps read as `Europe/Zurich`. Mirror it when in doubt.

### No real parcel has ever been seen — this ships pre-1.0 on purpose

`carrier-research/quickpac.md` carries `evidence: "semantic-404"`: every probe
against `GetPublicTracking` has returned `400`, never a populated `200`. The
payload shape and status-code table are **schema-confirmed** (Quickpac
publishes its own OpenAPI document — `swagger-v1.json`) but **not
carrier-confirmed** — nobody has watched a real shipment move through them.
Per CONVENTIONS.md's *Pre-1.0 releases* rule, this is shipped anyway at
`0.9.0`, with one-shot `WARNING`s on every unconfirmed shape (see
`parcels.py`'s `_check_schema_drift`, `_check_token_infos`,
`_check_protocol_order`, `_warn_unmapped_status`, `_log_status_group` and
`parse_quickpac_timestamp`'s format warning) so the first real user closes the
gap. **Do not remove or "simplify away" any of these warnings** — they are the
only channel by which this integration ever learns whether its guesses were
right.

### The status map keys on an **int code with band logic**, not a lookup table

`LastStatusCode` (and `Protocol[].Status`, same vocabulary) is a number, not a
string enum — `_bucket()` in `parcels.py` implements the seven-bucket table
reverse-engineered from the tracking SPA's own icon-selection JS (deobfuscated
in `carrier-research/api/quickpac/tracking.md`), not from any Quickpac
documentation.

**`2600` / `2601` / `2604` must resolve to three different canonical statuses**
(`at_pickup_point` / `in_transit` / `in_transit`-as-detour) even though they
all sit in the `26xx` hundred — `_bucket()` matches these three exact codes
*before* any `r = code // 100` banding, and there is no generic `r == 26`
branch at all. `test_2600_2601_2604_resolve_to_three_different_statuses` and
`test_map_parcel_status_band_ordering` guard this; a refactor toward a plain
`{hundred: status}` dict would silently misfile a pickup-point parcel as
in-transit. A superseded third-party CLI made exactly that mistake plus two
more (`>=3000 -> delivered`, `>=4000 -> exception`) — both `3xxx`/`4xxx` are
**not cases in Quickpac's own front-end at all** and correctly fall to
`unknown`.

### Deliberate `None`s and other decisions

- **`sender`, `receiver`, `pickup_point`, `planned_from`, `planned_to` are
  always `None`.** All of them sit behind `POST GetToken` (surname + postcode
  as a second factor) — this build ships the public path only. See
  BUILD_PLAN.md's "one real decision" in the (now-deleted) build plan, folded
  here: the cost is a surname *and* postcode per parcel stored in the config
  entry, `pickup_point` is the only field it would actually fill (the rest is
  free-text or gated fields already `None` on shipped carriers), and
  `at_pickup_point` still works without it. Revisit only if a Swiss user asks
  for the Abholstelle address specifically — additive later as an optional
  per-parcel `{zip, lastname}`, nothing about the public path changes.
- **`pickup` still reflects the real state** (`True` exactly when `status` is
  `at_pickup_point`, i.e. `LastStatusCode == 2600`) even though `pickup_point`
  stays `None` — "code 2600 carries the state, just not the address." This is
  this build's own call, not copied verbatim from a plan table that read
  `False`/`None` for both — a dashboard showing "ready for pickup" should not
  go dark just because the address is unavailable.
  **Unlike GLS/Dragonfly**, where `pickup` is an independent delivery-method
  signal derived from raw fields (GLS: `isPickup`/`isParcelShop`; Dragonfly:
  `task_type`) that can be `True` *before* arrival — which is what lets GLS
  ship a dedicated "en route to parcel shop" sensor distinct from "awaiting
  pickup" — Quickpac's public payload has no such independent signal. Here
  `pickup` can never lead `status`; it only flips true the same poll `status`
  already reports `at_pickup_point`. Don't port a GLS-style pre-arrival
  pickup sensor to this carrier without first getting that signal via the
  token (see the "one real decision" above).
- **`weight`/`dimensions` are always `None`** — not in the schema.
- **A `400` is never trusted at face value.** It is Quickpac's only
  "unknown/not-yet-registered code" signal, but it is *also* what a broken
  route returns (no `Detail` field to tell them apart) — `api.py` pairs every
  `400` with a `GetTrackingStartup` liveness canary: canary OK → `None`
  (normal pending state); canary fails → raise, logged once per failure
  streak so a sustained outage doesn't spam the log every poll.
- **Naive `Time` values are read as `Europe/Zurich`, not UTC** — same
  reasoning as `ha-planzer`: UTC would shift every event by 1-2h depending on
  DST. Unverified; the first unparseable value warns once.
- **`Protocol[]` order is unverified** — `build_history` always sorts on
  `Time` itself rather than trusting the wire order, and warns once (not per
  poll) if the wire order was not already ascending.
- **Tracking-code format is digits only, no length bound.** Confirmed via the
  OpenAPI document: the same identifier is `string` on `GetPublicTracking` but
  `integer($int64)` on `GetLiveTracking`/`GetToken`, which rules out a letter
  prefix. The `"QP12345678"` a third-party CLI's README uses is a synthetic
  placeholder that never resolved — do not build a `QP`-prefix regex from it.
- **`GetToken`, `SaveOption`, `GetLiveTracking` and
  `buildingapi.quickpac.ch` are deliberately not called anywhere** — the first
  three are out of scope (writes, or token-gated data this build skips by
  choice), and the fourth ships a hardcoded Basic credential (the suite's
  refused class). Do not add them without revisiting the "one real decision"
  above.

## Options and reloads

The options flow is one sectioned form (`data_entry_flow.section`); changes apply
without a restart. Two models, **do not mix them**:
- **Account-less carriers** (the default) apply changes live: an update listener
  retunes `coordinator.update_interval` and calls `async_request_refresh()`, so
  added/removed parcel sensors appear immediately.
- **Account-based carriers** call `async_schedule_reload` on submit and register
  **no** update listener. Combining a listener with a reload-on-update flow is
  deprecated, an error in HA 2026.12+.

The user-tunable poll interval is a deliberate HACS divergence (see
CONVENTIONS.md); a carrier that throttles is generated with a fixed cadence and no
polling option at all.

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (HTTP client, error types) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure, no I/O) | partly (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` (fetch, cache, event firing) | mostly not |
| `config_flow.py` | partly (code validation) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`, account-less only) | no |

`parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. `aiohttp.ClientError` is caught **per parcel** in the gather
loop (one bad parcel doesn't fail the poll) but **not** around the whole update
(the coordinator wraps that). Entities: `has_entity_name` + `translation_key`,
`icons.json`, translated units, `_attr_attribution`, `_unrecorded_attributes` on
anything with a parcel list or `raw`. Over-redact diagnostics — they get pasted
into public issues.

## Tests on Windows

`tests/conftest.py` carries two Windows-only shims (no-ops elsewhere):
`disable_socket` is neutralised (Windows event loops need AF_INET socketpairs;
the 127.0.0.1 allowlist stays) and HA's `AsyncResolver` is swapped for
`ThreadedResolver` (aiodns refuses the Proactor loop). Do not remove them
"because CI passes" — CI is Linux, development is Windows.

## Running tests

```
python -m pytest tests/ --cov=custom_components.quickpac
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in this carrier's directory under the private
`carrier-research/api/`, never in this repo.
