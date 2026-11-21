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
left alone.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
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
# map serves both.
#
# The first block is the happy path taken from the working 2021 community
# integration ``rikman122/homeassistant-correos_spain`` — plausible but never
# independently confirmed. The second and third blocks are confirmed against
# real ES parcels captured 2026-08-24; codes are *not* uniformly
# ``[A-Z0-9]{7}V``-shaped like the first block — an exception code can end in
# ``R``. Prefer mapping too little over mapping wrongly — an unmapped code
# surfaces as ``unknown`` plus a one-shot warning that asks the user to report
# it, which is how the map grows.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "A010000V": ParcelStatus.REGISTERED,  # Admitido
    "A090000V": ParcelStatus.REGISTERED,  # Pre-registrado / admitido
    "P040000V": ParcelStatus.IN_TRANSIT,  # Clasificado
    "G01L010V": ParcelStatus.IN_TRANSIT,  # En unidad de reparto
    "H020000V": ParcelStatus.OUT_FOR_DELIVERY,  # En reparto
    "I010000V": ParcelStatus.DELIVERED,  # Entregado (community-integration guess)
    "L010000V": ParcelStatus.AT_PICKUP_POINT,  # En oficina / Lista de Correos (community-integration guess)
    # --- Confirmed against a real parcel, 2026-08-24 ---
    "H010930R": ParcelStatus.PROBLEM,  # Realizado intento de entrega (failed delivery attempt)
    "H01I350V": ParcelStatus.AT_PICKUP_POINT,  # A disposición del destinatario (ready for collection)
    "I01H210V": ParcelStatus.DELIVERED,  # Entregado
    # --- Confirmed against two more real parcels, 2026-08-24 ---
    "P101110V": ParcelStatus.IN_TRANSIT,  # En tránsito (left origin logistics center)
    "P101120V": ParcelStatus.IN_TRANSIT,  # En tránsito (heading to the delivery unit)
    "P100000V": ParcelStatus.IN_TRANSIT,  # Clasificado (same wording as P040000V)
    "P090000V": ParcelStatus.IN_TRANSIT,  # Transferido a proveedor externo (handed to an external delivery provider, still in-network)
    "M01E020R": ParcelStatus.PROBLEM,  # Envío a estacionar: dirección incorrecta (halted for a bad address, pending sender instructions)
    # --- Confirmed against user-tracked parcels, 2026-08-28 ---
    "P110000V": ParcelStatus.IN_TRANSIT,  # Clasificado
    "O140000V": ParcelStatus.RETURNING,  # Solicitada devolución
    "H06P010V": ParcelStatus.PROBLEM,  # En proceso de entrega (waiting for the next attempt)
    "H01I360V": ParcelStatus.IN_TRANSIT,  # Entrega concertada
    "X010000V": ParcelStatus.REGISTERED,  # Informado
    "X020000V": ParcelStatus.IN_TRANSIT,  # En ruta a la localidad de destino
    "X040000V": ParcelStatus.IN_TRANSIT,  # En ruta a la localidad de destino
    "X060000V": ParcelStatus.IN_TRANSIT,  # En destino
    "X070000V": ParcelStatus.IN_TRANSIT,  # En destino
    "X080000V": ParcelStatus.OUT_FOR_DELIVERY,  # En reparto
    "X090100R": ParcelStatus.PROBLEM,  # Estacionado
    "X110100V": ParcelStatus.IN_TRANSIT,  # Nuevo reparto
    "X090060R": ParcelStatus.PROBLEM,  # Estacionado
    "X380000V": ParcelStatus.AT_PICKUP_POINT,  # Entregado en PUDO
    "X390000V": ParcelStatus.AT_PICKUP_POINT,  # Disponible en PUDO
    "X120000V": ParcelStatus.DELIVERED,  # Entregado
    "X130000R": ParcelStatus.PROBLEM,  # En gestión
}

# Status codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()


def _parse_measurement(value: Any) -> float | None:
    """Parse a Correos ``peso``/``largo``/``ancho``/``alto`` string field.

    Confirmed grams (``peso``) and centimetres (``largo``/``ancho``/``alto``)
    on a real capture 2026-08-24 (``peso: 380`` against a 24x22x13 cm parcel
    too large for a mailbox slot). Absent/empty on parcels the envelope never
    populated them for.
    """
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
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
            return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
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
        "text": f"{round(length)} x {round(width)} x {round(height)} cm",
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


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


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
    * ``pickup`` stays ``True`` once a pickup-point parcel is collected.
    * ``weight`` is kilograms, ``dimensions`` centimetres (see
      :func:`format_dimensions`).
    * ``history`` is ``None`` when the option is off — the key still exists.
    """
    # ``codEnvio`` is the envelope's own tracking number; fall back to the code
    # the coordinator asked for (it injects ``trackingNumber`` on the pending
    # placeholder for a not-yet-scanned parcel).
    barcode = raw.get("codEnvio") or raw.get("trackingNumber")

    # Correos' ``eventos`` run oldest → newest, so the parcel's current state is
    # the last entry.
    events = raw.get("eventos") or []
    latest = events[-1] if events else None
    status_code = latest.get("codEvento") if latest else None
    status = map_parcel_status(status_code)
    delivered = status is ParcelStatus.DELIVERED
    pickup = is_pickup(
        None,
        status,
        (
            map_event_status(event.get("codEvento"))
            for event in events
            if isinstance(event, dict)
        ),
    )
    planned_from, planned_to = planned_for_status(
        status, *planned_window(None, None, zone=_MADRID_TZ)
    )

    raw_status = None
    if latest is not None:
        raw_status = latest.get("desTextoResumen") or status_code

    delivered_at = None
    if delivered and latest is not None:
        moment = event_datetime(latest)
        delivered_at = moment.isoformat() if moment is not None else None

    # ``peso`` is grams; the contract wants kilograms.
    weight_grams = _parse_measurement(raw.get("peso"))
    weight = weight_grams / 1000 if weight_grams is not None else None

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
        # Correos' consumer endpoint carries no delivery-window estimate.
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": pickup,
        # The envelope's ``nom_codired`` names the office (e.g. "MADRID SUC 37.
        # LA ELIPA"); events carry no per-event office field.
        "pickup_point": (raw.get("nom_codired") or None) if pickup else None,
        "url": tracking_url(barcode),
        "weight": weight,
        "dimensions": format_dimensions(
            _parse_measurement(raw.get("largo")),
            _parse_measurement(raw.get("ancho")),
            _parse_measurement(raw.get("alto")),
        ),
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
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
