# Quickpac Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-quickpac.svg)](https://github.com/ha-parcel-integrations/ha-quickpac/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

> ⚠️ **Pre-1.0 release.** No real Quickpac tracking number has ever been run through this API yet, so the status mapping below is reverse-engineered from the tracking website's own code, not confirmed against live data. Unknown statuses log a one-shot `WARNING` with a link to report them — see [Troubleshooting](#troubleshooting). If you get a real Quickpac parcel, a diagnostics dump after tracking it is the single most useful thing you can contribute.

A custom Home Assistant integration that tracks your [Quickpac](https://quickpac.ch) (Switzerland) parcels. No account is needed — you enter the tracking code yourself, just like on the Quickpac website.

Part of the [ha-parcel-integrations](https://github.com/ha-parcel-integrations) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Dynamic polling](#dynamic-polling)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of Quickpac parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), the carrier's own status text, the expected delivery window and a tracking deep-link
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- Read-only **Deliveries** calendar with the expected delivery windows
- `quickpac.track_parcel` / `quickpac.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered, delivery time changed)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- A Quickpac parcel and its tracking code (a string of digits, no letter
  prefix — from the shipping confirmation e-mail) — no account needed

Sender, receiver, pickup-point address and the expected-delivery window are
**not available** on Quickpac's public tracking API — they sit behind a
surname+postcode lookup this integration deliberately does not use (see
CLAUDE.md). A parcel waiting at a Coop Pick-up point still reports
`at_pickup_point`; only the pickup location's address is unavailable.

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-quickpac` as an **Integration**.
3. Install **Quickpac** and restart Home Assistant.

### Manual

Copy `custom_components/quickpac` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Quickpac**. There is nothing to fill in: the hub is created immediately (Quickpac tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`quickpac.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

## Dynamic polling

Instead of polling Quickpac at the same rate around the clock, the
integration adjusts its own cadence to what your tracked parcels are
actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM).
- **Hot (every 15 minutes)** — as soon as a tracked parcel is
  `out_for_delivery`, starting an hour before its expected delivery time (or
  immediately if no time is known — which, on Quickpac's public API, is
  always the case, since it never exposes a delivery window).
- **Mid (every 45 minutes)** — any other in-progress parcel.
- **Fully stopped** — nothing is tracked, or every tracked parcel has been
  delivered. Adding a parcel back (via the options dialog, the
  `quickpac.track_parcel` service, or a dashboard button) resumes polling
  immediately.
- A small, fixed per-hub offset is added on top, so not every Quickpac hub
  out there polls at exactly the same second.

This is not user-configurable — it is the only polling behaviour this
integration has.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Quickpac → ⋮ → Delete**. Nothing is stored on Quickpac's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.quickpac_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.quickpac_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.quickpac_next_delivery` | Earliest expected delivery moment across all active parcels |
| `sensor.quickpac_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.quickpac_last_successful_update` | Diagnostic: when Quickpac was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

A **`button.quickpac_refresh`** entity triggers an immediate poll outside the
regular interval, and a **`calendar.quickpac_deliveries`** entity shows
expected delivery dates for active parcels — read-only, no extra API calls.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family. Quickpac has no status code that maps to `problem` — a failed handover reports through `unknown` (with a `WARNING`) until it is captured and mapped, per [Troubleshooting](#troubleshooting):

| Status | Meaning |
|---|---|
| `registered` | Announced / received by Quickpac |
| `in_transit` | In the sorting network, including a redirect ("Detour") |
| `out_for_delivery` | With the courier today |
| `at_pickup_point` | Waiting for you at a Coop Pick-up point (address not exposed — see [Requirements](#requirements)) |
| `delivered` | Delivered, including a confirmed safe drop-off |
| `returning` | Going back to the sender |
| `unknown` | Not yet scanned, or a status we have not mapped yet |

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the Quickpac device):

| Event | When |
|---|---|
| `quickpac_parcel_registered` | A new parcel appears in the active list |
| `quickpac_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `quickpac_parcel_delivered` | A parcel is delivered |
| `quickpac_parcel_delivery_time_changed` | The expected delivery window changes |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `quickpac.track_parcel` | `tracking_code` | Start tracking a parcel |
| `quickpac.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.quickpac: debug
```

## Troubleshooting

- **A parcel shows `unknown` and stays pending** — Quickpac answers the same `400` for "not yet scanned" and "unknown/mistyped code", so this integration cannot always tell them apart. It will pick up automatically once scanned; double-check the code (digits only, no letters) if it never does.
- **A "liveness canary" WARNING appears** — every `400` is paired with an unrelated, parameter-free health check; if that also fails, Quickpac's API likely moved. [Open an issue](https://github.com/ha-parcel-integrations/ha-quickpac/issues/new) — this is a different problem from an individual parcel not resolving.
- **A status logs "Unrecognised Quickpac status" or "StatusGroup value seen for the first time"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-quickpac/issues/new) with the logged line so the mapping can be extended. No real Quickpac response has ever been captured for this integration, so every report genuinely helps.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://github.com/ha-parcel-integrations) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://github.com/ha-parcel-integrations) for the current list of supported carriers.

## Disclaimer

This integration uses the same public tracking endpoint as the Quickpac consumer website. It is not affiliated with, endorsed by, or supported by Quickpac.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
