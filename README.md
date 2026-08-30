# Correos Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-correos.svg)](https://github.com/ha-parcel-integrations/ha-correos/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [Correos](https://www.correos.es) parcels — Spain's national postal operator. No account is needed: you enter the tracking code yourself, just like on the Correos website.

Part of the [ha-parcel-integrations](https://github.com/ha-parcel-integrations) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

> ### ℹ️ Happy path confirmed against a real parcel
>
> The endpoint is live and keyless, and unknown or not-yet-scanned numbers are
> handled correctly. The success payload — event fields, status codes, weight
> and dimensions — has been confirmed against a real Spanish parcel, including
> a failed delivery attempt and a pickup-office hold. A status code outside
> that confirmed set still reports **`unknown`** (never a wrong status) and
> logs a one-shot warning with a ready-made issue link — please
> [report it](https://github.com/ha-parcel-integrations/ha-correos/issues/new?template=unrecognised_status.yml)
> so the mapping can keep growing.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
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

- Track any number of Correos parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), the carrier's own Spanish status text and a tracking deep-link
- Summary sensors: incoming parcels, recently delivered parcels
- `correos.track_parcel` / `correos.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

> **Note:** Correos' public tracking does not expose an expected delivery time. The next-delivery sensor and the Deliveries calendar are still present (for parity with the other carriers) but stay empty, and the `delivery_time_changed` event never fires.

## Requirements

- Home Assistant 2024.12 or newer
- A Correos parcel and its tracking code (from the shipping
  confirmation email or the missed-delivery card) — no account needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-correos` as an **Integration**.
3. Install **Correos** and restart Home Assistant.

### Manual

Copy `custom_components/correos` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Correos**. There is nothing to fill in: the hub is created immediately (Correos tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`correos.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today, starting an hour before its delivery window opens (or immediately if
  no window is known yet — this is the fallback that fires in practice for
  Correos, whose consumer endpoint reports no delivery window at all).
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Fully paused** — once every tracked parcel has been delivered, or nothing
  is tracked at all, polling stops until you add a parcel back (adding one
  always triggers an immediate check, regardless of the pause).
- A small, fixed per-hub offset is added on top, so not every Correos hub out
  there polls at exactly the same second.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Correos → ⋮ → Delete**. Nothing is stored on Correos's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.correos_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.correos_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.correos_next_delivery` | Earliest expected delivery moment across all active parcels (stays empty — Correos exposes no ETA) |
| `sensor.correos_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.correos_last_successful_update` | Diagnostic: when Correos was last polled successfully |
| `calendar.correos_deliveries` | Expected delivery dates for active parcels, read-only, no extra API calls |
| `button.correos_refresh` | Forces an immediate poll without waiting for the next scheduled interval |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family. The statuses Correos currently maps:

| Status | Meaning |
|---|---|
| `registered` | Admitted / received by Correos |
| `in_transit` | Classified / in the sorting network |
| `out_for_delivery` | With the courier today (*en reparto*) |
| `at_pickup_point` | Waiting for you at a Correos office or PUDO |
| `delivered` | Delivered (*entregado*) |
| `returning` | A return has been requested (*solicitada devolución*) |
| `problem` | A delivery attempt failed, a retry is pending, or the shipment is held (*realizado intento de entrega* / *en proceso de entrega* / *estacionado*) |
| `unknown` | Not yet scanned, or a status we have not mapped yet |

The carrier's own Spanish text is always available as `raw_status`. A status the integration has not mapped yet surfaces as `unknown` and asks you to [report it](https://github.com/ha-parcel-integrations/ha-correos/issues/new?template=unrecognised_status.yml) — that is how the map grows.

## Events

The integration fires these on the event bus (also available as device triggers on the Correos device):

| Event | When |
|---|---|
| `correos_parcel_registered` | A new parcel appears in the active list |
| `correos_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `correos_parcel_delivered` | A parcel is delivered |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up. (The family's `delivery_time_changed` event is not listed: Correos exposes no ETA, so it never fires here.)

## Services

| Service | Fields | Description |
|---|---|---|
| `correos.track_parcel` | `tracking_code` | Start tracking a parcel |
| `correos.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

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
    custom_components.correos: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Correos has no traceability for it yet (their API answers *"Sin Trazabilidad"* until the first scan), or the code is wrong. It will pick up automatically once scanned.
- **A status logs "Unrecognised Correos status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-correos/issues/new) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://github.com/ha-parcel-integrations) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://github.com/ha-parcel-integrations) for the current list of supported carriers.

## Disclaimer

This integration uses the same public tracking endpoint as the Correos consumer website. It is not affiliated with, endorsed by, or supported by Correos. Be gentle with the polling interval.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
