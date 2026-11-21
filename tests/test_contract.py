"""The parcel contract's checks, run over every normalised sample."""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.correos.calendar import CorreosDeliveriesCalendar
from custom_components.correos.const import ParcelStatus
from custom_components.correos.parcels import (
    _MADRID_TZ,
    normalize_parcel,
    parse_iso,
    planned_window,
)

from .payloads import active_sample, delivered_sample, event, pickup_sample

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


def _with_last_event(code: str) -> dict:
    sample = pickup_sample()
    sample["eventos"] = [
        *sample["eventos"][:-1],
        event(code, "2026-04-29T10:00:00Z", "x"),
    ]
    return sample


def _collected() -> dict:
    sample = pickup_sample()
    sample["eventos"] = [
        *sample["eventos"],
        event("I01H210V", "2026-04-29T16:00:00Z", "Entregado"),
    ]
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "at_pickup_point": pickup_sample,
    "collected": _collected,
    "returning": lambda: _with_last_event("O140000V"),
    "problem": lambda: _with_last_event("H010930R"),
    "bare": lambda: {"trackingNumber": "PQ0000000001ES"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is not None
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "OFICINA MADRID CENTRAL"


def test_a_home_delivery_is_not_pickup():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2099-04-29T13:00:00Z", None, zone=_MADRID_TZ)
    end_dt = datetime.fromisoformat(end)
    assert (end_dt.hour, end_dt.minute, end_dt.second) == (23, 59, 59)
    assert end_dt.date() == parse_iso(start).date()


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2099-04-29", None, zone=_MADRID_TZ)
    start_dt, end_dt = datetime.fromisoformat(start), datetime.fromisoformat(end)
    assert (start_dt.hour, start_dt.minute, start_dt.second) == (0, 0, 0)
    assert (end_dt.hour, end_dt.minute, end_dt.second) == (23, 59, 59)
    assert start_dt.date() == end_dt.date()


def test_a_whole_day_window_is_an_all_day_event():
    start, end = planned_window("2099-04-29", None, zone=_MADRID_TZ)
    coordinator = MagicMock()
    coordinator.data = [
        {
            "barcode": "A",
            "sender": None,
            "status": "in_transit",
            "planned_from": start,
            "planned_to": end,
            "pickup": False,
            "pickup_point": None,
            "url": None,
        }
    ]
    entry = MagicMock()
    entry.entry_id = "e1"
    event_ = CorreosDeliveriesCalendar(coordinator, entry)._events()[0]
    assert event_.start == date(2099, 4, 29)
    assert event_.end == date(2099, 4, 29) + timedelta(days=1)
