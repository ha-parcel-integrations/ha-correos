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
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| ship anything while below 1.0.0 (unverified against a real parcel) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client) | *Deliberate skill divergences* |
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

**Status: unverified against a real parcel.** The endpoint + error envelope were
verified live (2026-07-25), but the *success* payload shape is reconstructed from
the dormant community integration `rikman122/homeassistant-correos_spain` (2021,
still functional). Treat `parcels.py`, `api.py`, `tests/payloads.py` as
best-effort until a real ES parcel confirms them — open items flagged
`TODO(carrier)`.

### Endpoint & auth
- **Keyless** — the tracking code alone keys the lookup (no account, key, header,
  postcode). `TRACKING_API_URL` is the `localizador` events service; fixed params
  (`codAplicacion=60` `codCanal=3` `codIdioma=ES` `indUltEvento=N`) are the public
  web channel with full Spanish event history.
- **Response is a JSON array**; element `[0]` is the shipment envelope. Served
  without a reliable JSON content-type → `response.json(content_type=None)`.
- **Transport status is always HTTP 200.** The real result is
  `[0].error.codError` as a **string**: `"0"` = found, anything else = no
  traceability. `async_get_parcel` returns the envelope on `"0"`, `None` on any
  other code (unknown / not-yet-scanned — normal, not an error), raises only on a
  malformed body. **Branch on the body, never the HTTP status.**
- **Rate limiting unmeasured** — generated with `--interval configurable`; switch
  to `fixed` if throttling turns up.

### Status vocabulary (`codEvento` → `ParcelStatus`)
Current status is the **last** entry in `eventos` (oldest → newest). `_STATUS_MAP`
serves both the parcel status and each history entry. Verified (2021 integration):
`A010000V`/`A090000V` → registered, `P040000V`/`G01L010V` → in transit,
`H020000V` → out for delivery, `I010000V` → delivered. `L010000V` → at pickup
point is a **plausible reconstruction** (Lista de Correos), unconfirmed. No
verified `returning`/`problem` codes yet — unmapped → `unknown` + one-shot warning.

### Timestamps & fields
- **Timestamps are split** across `fecEvento` (`DD/MM/YYYY`) + `horEvento`
  (`HH:MM:SS`) in **Europe/Madrid**. `event_datetime()` combines and localises;
  an unparseable date is dropped rather than mis-sorted.
- **No ETA** — `planned_from`/`planned_to` always `None`, so the next-delivery
  sensor and calendar stay empty and `correos_parcel_delivery_time_changed` never
  fires. The ETA machinery stays (suite parity), exercised white-box.
- **`weight`/`dimensions` deliberately `None`** — the envelope exposes `peso` and
  `largo`/`alto`/`ancho` but their units are unconfirmed (a wrong-unit value is
  worse than none). `TODO(carrier)` marks where to wire them via
  `format_dimensions()` once a real parcel pins units down.
- **`sender` is `None`**; **`receiver`** is best-effort from `nombre_cliente`
  (unconfirmed which party). `barcode` from `codEnvio` (fallback: the requested
  code). `TO_REDACT`: `codEnvio`, `nombre_cliente`, `numReferencia1..3`,
  `observaciones`, `nom_codired`.
- **Tracking-code regex stays generous** (`^[A-Z0-9]{6,30}$`) — codes vary (UPU
  S10 `[A-Z]{2}\d{9}ES`, plus `PQ`/`PK`/`CP`/`DS` prefixes); a false negative is
  worse than a bad code that returns "no traceability" next poll.

## Options and reloads — account-less model

The options flow is one sectioned form; changes apply without a restart.
Account-less carriers (this one) use the **update-listener** model (retunes
`coordinator.update_interval` + `async_request_refresh()`, so added/removed
sensors appear immediately). Account-based carriers instead call
`async_schedule_reload` with **no** listener (combining the two is deprecated,
error in HA 2026.12+). The user-tunable poll interval is a deliberate HACS
divergence (see CONVENTIONS.md).

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
| `services.py` (`track_parcel` / `untrack_parcel`) | no |

`parcels.py` is free of I/O and HA objects so the per-carrier part stays
unit-testable. Config: `ConfigEntry.runtime_data` (typed, no `hass.data`),
`PARALLEL_UPDATES = 0`, coordinator takes `config_entry=entry`.
`aiohttp.ClientError` is caught **per parcel** in the gather loop (one bad parcel
doesn't fail the poll) but **not** around the whole update (coordinator wraps
that). Entities: `has_entity_name` + `translation_key`, `icons.json`, translated
units, `_attr_attribution`, `_unrecorded_attributes` on anything with a parcel
list or `raw`. Over-redact diagnostics.

## Tests on Windows

`tests/conftest.py` carries two Windows-only shims (no-ops elsewhere):
`disable_socket` is neutralised (Windows event loops need AF_INET socketpairs;
the 127.0.0.1 allowlist stays) and HA's `AsyncResolver` is swapped for
`ThreadedResolver` (aiodns refuses the Proactor loop). Do not remove them
"because CI passes" — CI is Linux, development is Windows.

## Running tests

```
python -m pytest tests/ --cov=custom_components.correos
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; `docs/api/` is gitignored (local reverse-engineering notes).
