"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

The carrier-specific parts are :data:`_STATUS_MAP`, :func:`event_datetime`,
:func:`build_history` and :func:`normalize_parcel` (the Correos ``localizador``
field lookups). Everything else — the sort contract, the delivered filter, the
one-shot warning for unmapped statuses — is suite-wide machinery and should be
left alone. Remaining ``TODO(carrier)`` markers flag the bits still unverified
against a real parcel (the pickup event code and the ``peso``/dimensions units).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-correos/issues/new"
    "?template=unrecognised_status.yml"
)

# Correos event codes (``codEvento``) → canonical ParcelStatus. The same codes
# appear on both the parcel's latest event and every history entry, so this one
# map serves both. Codes are ``[A-Z0-9]{7}V``-shaped.
#
# The first six are the happy path, taken from the working 2021 community
# integration ``rikman122/homeassistant-correos_spain`` (verified codes). The
# pickup code is a *plausible reconstruction* and marked as such — Correos parks
# undelivered parcels in a *oficina* / *Lista de Correos* (hence the
# ``num_dias_lista`` field), but the exact code is unconfirmed until a real
# parcel shows one. No verified return/exception codes yet: prefer mapping too
# little over mapping wrongly — an unmapped code surfaces as ``unknown`` plus a
# one-shot warning that asks the user to report it, which is how the map grows.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "A010000V": ParcelStatus.REGISTERED,        # Admitido
    "A090000V": ParcelStatus.REGISTERED,        # Pre-registrado / admitido
    "P040000V": ParcelStatus.IN_TRANSIT,        # Clasificado
    "G01L010V": ParcelStatus.IN_TRANSIT,        # En unidad de reparto
    "H020000V": ParcelStatus.OUT_FOR_DELIVERY,  # En reparto
    "I010000V": ParcelStatus.DELIVERED,         # Entregado
    # TODO(carrier): confirm against a real parcel held at an office.
    "L010000V": ParcelStatus.AT_PICKUP_POINT,   # En oficina / Lista de Correos
}

# Status codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()

# ``peso``/``largo``/``alto``/``ancho`` appear in the envelope, but their UNITS
# (grams vs kg, cm vs mm) are unconfirmed, so ``weight`` / ``dimensions`` stay
# ``None`` rather than publish a wrong-unit value (see TODO.md). The first real
# parcel that carries them logs once — an open question we want a tester to
# answer so we can wire them up. See NEW_ISSUE_URL.
_DIMENSION_KEYS = ("peso", "largo", "alto", "ancho")
_dimension_fields_logged = False


def _note_dimension_fields(raw: dict) -> None:
    """One-shot: flag weight/size fields whose units we have not confirmed."""
    global _dimension_fields_logged
    if _dimension_fields_logged:
        return
    present = sorted(k for k in _DIMENSION_KEYS if raw.get(k) not in (None, ""))
    if not present:
        return
    _dimension_fields_logged = True
    _LOGGER.warning(
        "Correos payload carries weight/size fields whose units we have not "
        "confirmed yet (%s), so weight and dimensions are left empty. Please "
        "help us confirm the units — a diagnostics file is ideal: %s",
        present,
        NEW_ISSUE_URL,
    )


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Correos status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
    )


def map_parcel_status(code: str | None) -> ParcelStatus:
    """Map a carrier status code to a canonical :class:`ParcelStatus`.

    ``None`` (a not-yet-scanned parcel) reports ``unknown`` silently; an
    unrecognised code reports ``unknown`` with a one-shot warning.
    """
    if not code:
        return ParcelStatus.UNKNOWN
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return ParcelStatus.UNKNOWN


def map_event_status(code: str | None) -> ParcelStatus | None:
    """Map a history entry's status code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once, reusing the parcel-status one-shot set.
    """
    if not code:
        return None
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


# Correos stamps its events in Spanish local time, split across two fields.
_MADRID_TZ = ZoneInfo("Europe/Madrid")


def event_datetime(event: dict) -> datetime | None:
    """Combine a Correos event's split date/time fields into a datetime.

    Events carry ``fecEvento`` (``DD/MM/YYYY``) and ``horEvento``
    (``HH:MM:SS``) in Europe/Madrid local time; a missing time defaults to
    midnight. Returns an aware datetime, or ``None`` when the date is missing
    or malformed (that event is then dropped rather than mis-sorted).
    """
    date_str = event.get("fecEvento")
    if not date_str:
        return None
    time_str = event.get("horEvento") or "00:00:00"
    try:
        naive = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M:%S")
    except (ValueError, TypeError):
        return None
    return naive.replace(tzinfo=_MADRID_TZ)


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from Correos' ``eventos`` list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``raw_status`` is the event's Spanish summary
    text, falling back to its event code. Sorted oldest → newest and capped to
    the most recent ``max_events``. Events without a parseable timestamp are
    dropped, since Correos' split date/time either combines cleanly or not
    at all.
    """
    dated: list[tuple[datetime, dict]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        moment = event_datetime(event)
        if moment is None:
            continue
        code = event.get("codEvento")
        dated.append(
            (
                moment,
                {
                    "timestamp": moment.isoformat(),
                    "status": map_event_status(code),
                    "raw_status": event.get("desTextoResumen") or code,
                },
            )
        )
    dated.sort(key=lambda item: item[0])
    return [entry for _, entry in dated][-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order, and the aggregator and
    cross-carrier dashboards depend on it. A key the carrier does not expose is
    ``None`` — never omitted.

    Rules the body follows:

    * ``status`` is canonical, ``raw_status`` is the carrier's own text.
    * A delivered parcel has ``delivered_at`` set and ``planned_from`` /
      ``planned_to`` cleared — the ETA is meaningless once it has arrived.
    * ``planned_to`` is ``None`` for a point estimate; only fill it when the
      carrier genuinely reports a *window*.
    * ``weight`` is kilograms, ``dimensions`` centimetres (see
      :func:`format_dimensions`).
    * ``history`` is ``None`` when the option is off — the key still exists.
    """
    # ``codEnvio`` is the envelope's own tracking number; fall back to the code
    # the coordinator asked for (it injects ``trackingNumber`` on the pending
    # placeholder for a not-yet-scanned parcel).
    _note_dimension_fields(raw)
    barcode = raw.get("codEnvio") or raw.get("trackingNumber")

    # Correos' ``eventos`` run oldest → newest, so the parcel's current state is
    # the last entry.
    events = raw.get("eventos") or []
    latest = events[-1] if events else None
    status_code = latest.get("codEvento") if latest else None
    status = map_parcel_status(status_code)
    delivered = status is ParcelStatus.DELIVERED
    at_pickup = status is ParcelStatus.AT_PICKUP_POINT

    raw_status = None
    if latest is not None:
        raw_status = latest.get("desTextoResumen") or status_code

    delivered_at = None
    if delivered and latest is not None:
        moment = event_datetime(latest)
        delivered_at = moment.isoformat() if moment is not None else None

    return {
        "carrier": "Correos",
        "barcode": barcode,
        # Correos' consumer trace does not name the sender.
        "sender": None,
        # ``nombre_cliente`` is the addressee on the account's own trace.
        # Best-effort until a real payload confirms which party it names.
        "receiver": raw.get("nombre_cliente") or None,
        "status": status,
        "raw_status": raw_status,
        "delivered": delivered,
        "delivered_at": delivered_at,
        # Correos' consumer endpoint carries no delivery-window estimate, so
        # there is never a planned ETA to publish.
        "planned_from": None,
        "planned_to": None,
        "pickup": at_pickup,
        # When held for collection, the ``unidad`` names the office.
        "pickup_point": (latest.get("unidad") if at_pickup and latest else None),
        "url": tracking_url(barcode),
        # TODO(carrier): the envelope exposes ``peso`` and ``largo``/``alto``/
        # ``ancho``, but their units (grams vs kg, cm vs mm) are unconfirmed —
        # publishing a wrong-unit value is worse than none, so they stay None
        # until a real parcel pins them down. Then map via format_dimensions().
        "weight": None,
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
