"""Constants for the Correos parcel tracker integration."""
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "correos"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"               # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"               # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"   # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"     # Ready to collect at a pickup location
    DELIVERED = "delivered"                 # Handed over
    RETURNING = "returning"                 # Failed delivery, going back to sender
    PROBLEM = "problem"                     # Carrier reports an exception/issue
    UNKNOWN = "unknown"                     # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Correos exposes a keyless consumer traceability service — no API key, no
# header, no bot wall; the tracking code alone keys the lookup.
#
# ``TRACKING_API_URL`` is the ``localizador`` events endpoint the coordinator
# polls. The fixed query params are the public web channel: ``codAplicacion=60``
# ``codCanal=3`` (site), ``codIdioma=ES`` (Spanish event text), and
# ``indUltEvento=N`` (full event list rather than only the last event).
#
# The response is a JSON **array** whose first element is the shipment envelope.
# The transport status is always HTTP 200; the real result lives in
# ``[0].error.codError`` as a *string* — ``"0"`` = found, any other value
# (e.g. ``"3"`` = *Sin Trazabilidad en Minerva*) = unknown/not-yet-scanned.
# The body is served without a reliable JSON content-type, so the client parses
# with ``content_type=None``. Rate limiting is unmeasured (single probes only) —
# revisit ``--interval fixed`` if throttling shows up.
#
# The endpoint and envelope were verified live 2026-07-25; the *success* payload
# shape (the ``eventos`` fields, ``peso``/dimensions) is reconstructed from the
# 2021 community integration ``rikman122/homeassistant-correos_spain`` and is
# still unverified against a real parcel — see the research doc and TODO.md.
TRACKING_API_URL = (
    "https://localizador.correos.es/canonico/eventos_envio_servicio/"
    "{tracking_code}?codAplicacion=60&codCanal=3&codIdioma=ES&indUltEvento=N"
)
TRACKING_URL = (
    "https://www.correos.es/es/es/herramientas/localizador/envios/detalle"
    "?tracking-number={tracking_code}"
)

# Tracked parcels live in the config entry options as a list of
# ``{tracking_code}`` dicts — this carrier has no account or parcel feed, so the
# user enters the codes themselves. Kept as dicts so future per-parcel fields
# slot in without an options migration.
CONF_PARCELS = "parcels"
CONF_TRACKING_CODE = "tracking_code"

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Refresh interval (minutes) controls how often the coordinator polls the
# carrier. Default 30 min keeps the load on a consumer endpoint gentle; the
# minimum is 15 min for the same reason.
#
# Deliberate divergence from the HA Core rule that polling intervals are not
# user-configurable: that rule targets core integrations, and in a HACS parcel
# tracker a tunable cadence is a wanted feature. Generate with
# ``--interval fixed`` instead when the carrier throttles or soft-bans unusual
# traffic — that drops the option entirely and hard-codes the cadence, so users
# cannot dial it down to something that gets them blocked.
CONF_REFRESH_INTERVAL = "refresh_interval"
REFRESH_INTERVAL_OPTIONS = (15, 30, 60, 120, 240)
DEFAULT_REFRESH_INTERVAL = 30

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
