"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.correos import parcels as parcels_module
from custom_components.correos.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.correos.parcels import (
    apply_delivered_filter,
    build_history,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from .payloads import (
    active_sample,
    delivered_sample,
    event,
    pickup_sample,
    problem_sample,
)

# ---------------------------------------------------------------------------
# map_parcel_status / map_event_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("A010000V", ParcelStatus.REGISTERED),
        ("A090000V", ParcelStatus.REGISTERED),
        ("P040000V", ParcelStatus.IN_TRANSIT),
        ("G01L010V", ParcelStatus.IN_TRANSIT),
        ("H020000V", ParcelStatus.OUT_FOR_DELIVERY),
        ("L010000V", ParcelStatus.AT_PICKUP_POINT),
        ("I010000V", ParcelStatus.DELIVERED),
        ("H010930R", ParcelStatus.PROBLEM),
        ("H01I350V", ParcelStatus.AT_PICKUP_POINT),
        ("I01H210V", ParcelStatus.DELIVERED),
        ("P101110V", ParcelStatus.IN_TRANSIT),
        ("P101120V", ParcelStatus.IN_TRANSIT),
        ("P100000V", ParcelStatus.IN_TRANSIT),
        ("P090000V", ParcelStatus.IN_TRANSIT),
        ("M01E020R", ParcelStatus.PROBLEM),
        ("P110000V", ParcelStatus.IN_TRANSIT),
        ("O140000V", ParcelStatus.RETURNING),
        ("H06P010V", ParcelStatus.PROBLEM),
        ("H01I360V", ParcelStatus.IN_TRANSIT),
        ("X010000V", ParcelStatus.REGISTERED),
        ("X020000V", ParcelStatus.IN_TRANSIT),
        ("X040000V", ParcelStatus.IN_TRANSIT),
        ("X060000V", ParcelStatus.IN_TRANSIT),
        ("X070000V", ParcelStatus.IN_TRANSIT),
        ("X080000V", ParcelStatus.OUT_FOR_DELIVERY),
        ("X090100R", ParcelStatus.PROBLEM),
        ("X110100V", ParcelStatus.IN_TRANSIT),
        ("X090060R", ParcelStatus.PROBLEM),
        ("X380000V", ParcelStatus.AT_PICKUP_POINT),
        ("X390000V", ParcelStatus.AT_PICKUP_POINT),
        ("X120000V", ParcelStatus.DELIVERED),
        ("X130000R", ParcelStatus.PROBLEM),
    ],
)
def test_map_parcel_status_known(code, expected):
    assert map_parcel_status(code) == expected


def test_map_parcel_status_missing_is_unknown():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_is_unknown():
    assert map_parcel_status("TELEPORTED") == ParcelStatus.UNKNOWN


def test_map_event_status_missing_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status("SOMETHING_NEW") is None
    assert map_event_status("I010000V") == ParcelStatus.DELIVERED


def test_unmapped_status_warns_only_once(caplog):
    assert map_parcel_status("ABDUCTED") == ParcelStatus.UNKNOWN
    assert map_parcel_status("ABDUCTED") == ParcelStatus.UNKNOWN
    assert caplog.text.count("ABDUCTED") == 1
    assert "issues/new" in caplog.text


def test_parse_measurement_handles_absent_and_malformed():
    assert parcels_module._parse_measurement("380") == 380.0
    assert parcels_module._parse_measurement(None) is None
    assert parcels_module._parse_measurement("") is None
    assert parcels_module._parse_measurement("not-a-number") is None


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    history = build_history(delivered_sample()["eventos"])
    assert len(history) == 4
    assert history[0]["raw_status"] == "Admitido"
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_build_history_caps_to_max_events():
    events = [
        event("P040000V", f"2026-04-{day:02d}T10:00:00Z", "moved")
        for day in range(1, 26)
    ]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"codEvento": "P040000V"}]) == []  # no timestamp
    assert build_history(["not-a-dict"]) == []


def test_build_history_drops_unparseable_timestamp():
    """Correos' split date/time either combines cleanly or the event is dropped."""
    history = build_history(
        [
            event("A090000V", "2026-04-24T10:00:00Z", "fine"),
            event("P040000V", "not-a-date", "odd"),
        ]
    )
    assert [entry["raw_status"] for entry in history] == ["fine"]


def test_build_history_falls_back_to_status_code_without_text():
    history = build_history([event("P040000V", "2026-04-24T10:00:00Z", "")])
    assert history[0]["raw_status"] == "P040000V"


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "Correos"
    assert parcel["barcode"] == "PQ9988776655ES"
    # Correos' consumer trace names neither the sender nor a confirmed receiver.
    assert parcel["sender"] is None
    assert parcel["receiver"] == "Jane Doe"
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Entregado"
    assert parcel["delivered"] is True
    # Timestamps are Europe/Madrid; assert the wall-clock moment, not the offset.
    assert parcel["delivered_at"].startswith("2026-04-29T13:12:42")
    # Correos exposes no ETA window at all.
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["url"] == (
        "https://www.correos.es/es/es/herramientas/localizador/envios/detalle"
        "?tracking-number=PQ9988776655ES"
    )
    # ``peso`` (grams) / ``largo``-``ancho``-``alto`` (cm) — confirmed units.
    assert parcel["weight"] == 1.5
    assert parcel["dimensions"] == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 4
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_active_parcel_has_no_window():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    # Correos consumer tracking carries no delivery-window estimate.
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


def test_normalize_pickup_parcel():
    parcel = normalize_parcel(pickup_sample())
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    # The office name lives on the envelope's ``nom_codired``, not the event.
    assert parcel["pickup_point"] == "OFICINA MADRID CENTRAL"


def test_normalize_failed_delivery_attempt_is_problem():
    parcel = normalize_parcel(problem_sample())
    assert parcel["status"] == ParcelStatus.PROBLEM
    assert parcel["raw_status"] == "Realizado intento de entrega"
    assert parcel["delivered"] is False
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_match_the_no_window_gap():
    """CAPABILITIES must agree with test_normalize_delivered_parcel /
    test_normalize_pickup_parcel / test_normalize_history_is_opt_in."""
    assert CAPABILITIES == {"pickup_point", "url", "history", "weight", "dimensions"}


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = normalize_parcel({"trackingNumber": "EXAMPLE000001"})
    assert parcel["barcode"] == "EXAMPLE000001"
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None


def test_normalize_blank_receiver_becomes_none():
    raw = active_sample()
    raw["nombre_cliente"] = ""
    parcel = normalize_parcel(raw)
    assert parcel["sender"] is None
    assert parcel["receiver"] is None


def test_normalize_keeps_raw_payload():
    raw = active_sample()
    assert normalize_parcel(raw)["raw"] is raw


def test_normalize_falls_back_to_status_code_without_text():
    raw = active_sample()
    # Blank the latest event's summary text; raw_status falls back to its code.
    raw["eventos"][-1]["desTextoResumen"] = ""
    assert normalize_parcel(raw)["raw_status"] == "H020000V"


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
