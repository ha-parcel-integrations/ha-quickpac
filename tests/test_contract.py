"""The parcel contract's checks, run over every normalised sample."""

from datetime import date, datetime

import pytest

from custom_components.quickpac.const import ParcelStatus
from custom_components.quickpac.parcels import (
    CARRIER_TZ,
    normalize_parcel,
    parse_iso,
    planned_for_status,
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


def _with_status(code: int) -> dict:
    sample = pickup_sample()
    sample["LastStatusCode"] = code
    return sample


def _collected() -> dict:
    sample = pickup_sample()
    sample["LastStatus"] = "Zugestellt"
    sample["LastStatusCode"] = 2100
    sample["Protocol"] = [
        event(2600, "2026-04-29T09:30:00", "Bereit zur Abholung", group="Pickup"),
        event(2100, "2026-04-29T16:00:00", "Zugestellt", group="Delivered"),
    ]
    return sample


def _safe_deposit() -> dict:
    sample = active_sample()
    sample["LastStatus"] = "Sicher deponiert"
    sample["LastStatusCode"] = 2104
    sample["Protocol"] = [event(2104, "2026-04-29T16:00:00", "Sicher deponiert")]
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "at_pickup_point": pickup_sample,
    "collected": _collected,
    "safe_deposit": _safe_deposit,
    "returning": lambda: _with_status(2400),
    "detour": lambda: _with_status(2604),
    "bare": lambda: {"IdentCode": "990000000001"},
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
    assert parcel["delivered_at"] == "2026-04-29T16:00:00+02:00"
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None
    assert parcel["history"] is None


def test_a_safe_deposit_is_delivered_but_not_pickup():
    parcel = normalize_parcel(_safe_deposit())
    assert parcel["delivered"] is True
    assert parcel["pickup"] is False


def test_a_returning_parcel_after_arrival_keeps_pickup_and_is_not_delivered():
    parcel = normalize_parcel(_with_status(2400))
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["pickup"] is True


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2026-04-29T13:00:00Z", None, zone=CARRIER_TZ)
    assert parse_iso(start) == parse_iso("2026-04-29T13:00:00Z")
    last = datetime.fromisoformat(end)
    assert (last.hour, last.minute, last.second, last.microsecond) == (23, 59, 59, 0)
    assert last.date() == parse_iso(start).astimezone(CARRIER_TZ).date()


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2026-04-29", None, zone=CARRIER_TZ)
    first = datetime.fromisoformat(start)
    last = datetime.fromisoformat(end)
    assert (first.hour, first.minute, first.second) == (0, 0, 0)
    assert (last.hour, last.minute, last.second, last.microsecond) == (23, 59, 59, 0)
    assert first.date() == last.date()


def test_a_window_is_emptied_once_the_parcel_is_waiting_or_done():
    window = planned_window("2026-04-29T13:00:00+02:00", None, zone=CARRIER_TZ)
    for status in (
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
        ParcelStatus.DELIVERED,
    ):
        assert planned_for_status(status, *window) == (None, None)
    assert planned_for_status(ParcelStatus.IN_TRANSIT, *window) == window


def test_window_inputs_of_every_shape():
    zone = CARRIER_TZ
    assert planned_window(None, None, zone=zone) == (None, None)
    assert planned_window(None, "garbage", zone=zone) == (None, None)
    assert planned_window(12345, None, zone=zone) == (None, None)
    assert planned_window("garbage", None, zone=zone) == (None, None)
    assert planned_window(date(2099, 3, 2), None, zone=zone) == (
        "2099-03-02T00:00:00+01:00",
        "2099-03-02T23:59:59+01:00",
    )
    assert planned_window(datetime(2099, 3, 2, 8, 30), None, zone=zone) == (
        "2099-03-02T08:30:00+01:00",
        "2099-03-02T23:59:59+01:00",
    )
    assert planned_window("2099-03-02", "2099-03-04", zone=zone) == (
        "2099-03-02T00:00:00+01:00",
        "2099-03-04T23:59:59+01:00",
    )
    assert planned_window("2099-03-04", "2099-03-02", zone=zone) == (
        "2099-03-02T00:00:00+01:00",
        "2099-03-04T23:59:59+01:00",
    )
    assert planned_window(
        "2099-03-02T08:00:00+01:00", "2099-03-02T08:00:00+01:00", zone=zone
    ) == ("2099-03-02T08:00:00+01:00", "2099-03-02T23:59:59+01:00")
    assert planned_window("2099-03-02T23:59:59+01:00", None, zone=zone) == (
        "2099-03-02T23:59:59+01:00",
        None,
    )
