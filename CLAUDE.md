# Working in this repository

Home Assistant custom integration for **Correos** (Spain) parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
Account-less (`track_parcel` / `untrack_parcel` services). No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| ship anything while below 1.0.0 (unverified against a real parcel) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client) | *Deliberate skill divergences* |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**API mechanics live in `carrier-research/correos/api/` (private research repo)** — the keyless
`localizador` endpoint, the `codError`-in-body signalling, the `codEvento` status
vocabulary and the split `fecEvento`/`horEvento` timestamps. Do not duplicate them
here.

**Structure, options flow, dynamic polling and module layout are suite-wide**
and identical in every carrier — the authoritative spec is
[`ha-carrier-template/scaffold/CLAUDE.md`](https://github.com/ha-parcel-integrations/ha-carrier-template/blob/main/scaffold/CLAUDE.md).
This repo follows it exactly.

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

## Carrier-specific decisions (integration only)

**Status: happy path confirmed against a real parcel (2026-08-24)** — a
mailbox-oversized item (Admitido → Clasificado → En reparto → failed attempt →
held at an office → Entregado) confirmed the event fields, `peso` (grams) /
`largo`-`ancho`-`alto` (cm) units, and that the pickup office name lives on the
envelope's `nom_codired`, not on the event (the original community-integration
guess assumed the latter). See `carrier-research/correos/api/`. Still no
verified genuine return-to-sender code — an unmapped status keeps reporting
`unknown` + one-shot warning.

- **No ETA** — `planned_from`/`planned_to` always `None`, so the next-delivery
  sensor and calendar stay inert and `correos_parcel_delivery_time_changed` never
  fires (the machinery stays for suite parity, exercised white-box).
- **`weight`/`dimensions` wired** via `format_dimensions()` — `peso` is grams
  (÷1000 for the kg contract), `largo`/`ancho`/`alto` are centimetres, both
  confirmed live. Reflected in `const.py`'s `CAPABILITIES` (feeds the docs
  site's comparison table) — keep the two in agreement if that ever changes.
- **`sender` is `None`**; **`receiver`** is best-effort. Unmapped status →
  `unknown` + one-shot warning.

## Running tests

```
python -m pytest tests/ --cov=custom_components.correos
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file in the same commit;
the API reference now lives in the private `carrier-research/correos/api/`,
not in this repo.
