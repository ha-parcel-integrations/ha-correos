# Correos — still to do

Generated from ha-carrier-template (`--auth none --interval configurable`) and
then filled in from research: the endpoint and error envelope are **verified
live**, the success payload is **reconstructed** from the 2021 community
integration `rikman122/homeassistant-correos_spain`. The integration runs and
its full test suite passes at 98% coverage.

## Blocked on a real ES parcel (the mapping is best-effort until then)

- [ ] `tests/payloads.py` — replace the reconstructed success envelope with a
      real, redacted `localizador` response.
- [ ] Confirm the `eventos` field names (`codEvento`, `desTextoResumen`,
      `fecEvento`/`horEvento`, `unidad`) against live data.
- [ ] `parcels.py` `_STATUS_MAP` — confirm `L010000V` → `at_pickup_point`, and
      collect the real codes for held-at-office / returned / exception so
      `returning` and `problem` can be mapped (currently unmapped → `unknown`).
- [ ] `parcels.py` `normalize_parcel` — the envelope exposes `peso` and
      `largo`/`alto`/`ancho`; confirm their **units** and then wire `weight` /
      `dimensions` via `format_dimensions()`. Left `None` on purpose for now.
- [ ] Confirm what `nombre_cliente` names (used as `receiver`), and whether any
      sender field exists.
- [ ] Measure rate-limiting: if Correos throttles, regenerate with
      `--interval fixed`.

## Before release

- [ ] `custom_components/correos/brand/icon.png` — currently a stopgap in the
      Correos brand yellow with a neutral parcel glyph (not the official
      logomark); swap in the official Correos brand asset before release.
- [ ] Install in a real Home Assistant and track one real parcel through at
      least two status changes.
- [ ] Add `correos` to the aggregator's `KNOWN_CARRIERS` and
      `CARRIER_EVENT_PREFIXES`.
- [ ] Create the GitHub repo under `ha-parcel-integrations` and push.

## Notes

- Correos exposes **no ETA**, so `planned_from`/`planned_to` are always `None`;
  the next-delivery sensor, the calendar and `delivery_time_changed` stay empty
  by design (documented in README and CLAUDE.md).

The full run-through lives in the template's `docs/checklist.md`.
Research notes: `carrier-research/correos.md`.

Delete this file once it is empty.
