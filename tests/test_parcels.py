"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.quickpac.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    ParcelStatus,
)
from custom_components.quickpac.parcels import (
    apply_delivered_filter,
    build_history,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    parse_quickpac_timestamp,
    reset_one_shot_warnings,
    sort_parcels_by_ts,
    to_iso_timestamp,
    tracking_url,
)

from .payloads import active_sample, delivered_sample, event, pickup_sample

pytestmark = pytest.mark.usefixtures("_reset_warnings")


@pytest.fixture
def _reset_warnings():
    reset_one_shot_warnings()
    yield
    reset_one_shot_warnings()


# ---------------------------------------------------------------------------
# map_parcel_status — the band-ordering trap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        (1000, ParcelStatus.REGISTERED),
        # The 26-hundreds band splits three ways — this is the trap a refactor
        # would break if it banded by hundreds instead of matching exact codes
        # first.
        (2600, ParcelStatus.AT_PICKUP_POINT),
        (2601, ParcelStatus.IN_TRANSIT),
        (2604, ParcelStatus.IN_TRANSIT),
        (2301, ParcelStatus.IN_TRANSIT),
        (1201, ParcelStatus.IN_TRANSIT),
        (1401, ParcelStatus.IN_TRANSIT),
        (1501, ParcelStatus.IN_TRANSIT),
        (1801, ParcelStatus.OUT_FOR_DELIVERY),
        (1901, ParcelStatus.OUT_FOR_DELIVERY),
        (2100, ParcelStatus.DELIVERED),
        (2104, ParcelStatus.DELIVERED),  # "sicher deponiert" is still delivered
        (2401, ParcelStatus.RETURNING),
        (2201, ParcelStatus.IN_TRANSIT),  # detour, not problem
        (2501, ParcelStatus.IN_TRANSIT),  # detour, not problem
    ],
)
def test_map_parcel_status_band_ordering(code, expected):
    assert map_parcel_status(code) == expected


def test_2600_2601_2604_resolve_to_three_different_statuses():
    """The band-ordering trap: exact codes, not a 26xx band.

    2601 (Prepare) and 2604 (Detour) legitimately both land on
    ``in_transit`` — the trap a naive ``{2600s: X}`` band would fall into is
    filing *2600* (Pickup) the same way, which this asserts against.
    """
    assert map_parcel_status(2600) == ParcelStatus.AT_PICKUP_POINT
    assert map_parcel_status(2601) == ParcelStatus.IN_TRANSIT
    assert map_parcel_status(2604) == ParcelStatus.IN_TRANSIT
    assert map_parcel_status(2600) != map_parcel_status(2601)
    assert map_parcel_status(2600) != map_parcel_status(2604)


@pytest.mark.parametrize("code", [3001, 3999, 4001, 4999])
def test_3xxx_and_4xxx_are_not_cases_at_all(code):
    """A superseded CLI invented >=3000 -> delivered / >=4000 -> exception; both wrong."""
    assert map_parcel_status(code) == ParcelStatus.UNKNOWN


def test_map_parcel_status_missing_or_non_numeric_is_unknown():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("2100") == ParcelStatus.UNKNOWN  # wire int only


def test_map_parcel_status_unmapped_warns_with_issue_link(caplog):
    assert map_parcel_status(9999) == ParcelStatus.UNKNOWN
    assert "9999" in caplog.text
    assert "issues/new" in caplog.text


def test_unmapped_status_warns_only_once(caplog):
    assert map_parcel_status(9999) == ParcelStatus.UNKNOWN
    assert map_parcel_status(9999) == ParcelStatus.UNKNOWN
    assert caplog.text.count("9999") == 1


def test_map_event_status_missing_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status(9999) is None
    assert map_event_status(2100) == ParcelStatus.DELIVERED


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_quickpac_timestamp_naive_is_read_as_zurich():
    parsed = parse_quickpac_timestamp("2026-04-29T13:12:42")
    assert parsed is not None
    assert str(parsed.tzinfo) == "Europe/Zurich"


def test_parse_quickpac_timestamp_missing_and_garbage():
    assert parse_quickpac_timestamp(None) is None
    assert parse_quickpac_timestamp("") is None
    assert parse_quickpac_timestamp("not-a-date") is None


def test_parse_quickpac_timestamp_bad_format_warns_once(caplog):
    assert parse_quickpac_timestamp("not-a-date") is None
    assert parse_quickpac_timestamp("also-not-a-date") is None
    assert caplog.text.count("does not parse") == 1


def test_to_iso_timestamp_wraps_parse():
    assert to_iso_timestamp("2026-04-29T13:12:42") is not None
    assert to_iso_timestamp(None) is None


def test_parse_iso_handles_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    assert parse_iso(None) is None
    assert parse_iso("garbage") is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    history = build_history(delivered_sample()["Protocol"])
    assert len(history) == 4
    assert history[0]["raw_status"] == "Sendung angekündigt"
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_build_history_sorts_out_of_order_wire_data_and_warns_once(caplog):
    reversed_protocol = list(reversed(delivered_sample()["Protocol"]))
    history = build_history(reversed_protocol)
    assert [entry["status"] for entry in history] == [
        ParcelStatus.REGISTERED,
        ParcelStatus.IN_TRANSIT,
        ParcelStatus.OUT_FOR_DELIVERY,
        ParcelStatus.DELIVERED,
    ]
    assert caplog.text.count("did not arrive already ordered") == 1


def test_build_history_caps_to_max_events():
    protocol = [
        event(1401, f"2026-04-{day:02d}T10:00:00", "moved", group="Prepare")
        for day in range(1, 26)
    ]
    assert len(build_history(protocol, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history([]) == []
    assert build_history([{"Status": 1000}]) == []  # no Time
    assert build_history(["not-a-dict"]) == []


def test_build_history_drops_unparseable_timestamp():
    history = build_history(
        [
            event(1000, "2026-04-24T10:00:00", "fine", group="Announcement"),
            event(1401, "not-a-date", "odd", group="Prepare"),
        ]
    )
    assert [entry["raw_status"] for entry in history] == ["fine"]


def test_build_history_falls_back_to_status_code_without_text():
    history = build_history([event(1401, "2026-04-24T10:00:00", "", group="Prepare")])
    assert history[0]["raw_status"] == 1401


def test_build_history_logs_each_distinct_status_group_once(caplog):
    protocol = [
        event(1000, "2026-04-24T10:00:00", "a", group="Announcement"),
        event(1401, "2026-04-25T10:00:00", "b", group="Prepare"),
        event(1402, "2026-04-26T10:00:00", "c", group="Prepare"),  # same group again
    ]
    build_history(protocol)
    assert caplog.text.count("StatusGroup value seen for the first time") == 2


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


def test_tracking_url_none_without_a_code():
    assert tracking_url(None) is None
    assert tracking_url("") is None


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "Quickpac"
    assert parcel["barcode"] == "990000654321"
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Zugestellt"
    assert parcel["delivered"] is True
    # newest 21xx Protocol entry's Time, not a dedicated field
    assert parcel["delivered_at"] == "2026-04-29T13:12:42+02:00"
    assert parcel["url"] == "https://quickpac.ch/de/tracking?identcode=990000654321"
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_always_none_fields_regardless_of_status():
    """sender/receiver/planned_*/weight/dimensions are gated behind a token
    this build never requests — always None, on every status."""
    for sample in (delivered_sample(), active_sample(), pickup_sample()):
        parcel = normalize_parcel(sample)
        assert parcel["sender"] is None
        assert parcel["receiver"] is None
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None
        assert parcel["weight"] is None
        assert parcel["dimensions"] is None
        assert parcel["pickup_point"] is None


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 4
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_active_parcel_is_not_delivered():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_normalize_pickup_parcel_sets_pickup_true_without_address():
    """code 2600 carries the state; the address stays gated regardless."""
    parcel = normalize_parcel(pickup_sample())
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None


def test_normalize_non_pickup_parcel_has_pickup_false():
    parcel = normalize_parcel(active_sample())
    assert parcel["pickup"] is False


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = normalize_parcel({"IdentCode": "990000000001"})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["history"] is None


def test_normalize_keeps_raw_payload():
    raw = active_sample()
    assert normalize_parcel(raw)["raw"] is raw


def test_normalize_falls_back_to_status_code_without_text():
    raw = active_sample()
    raw["LastStatus"] = None
    assert normalize_parcel(raw)["raw_status"] == 1801


def test_normalize_delivered_at_none_when_no_delivered_protocol_entry():
    """Defensive: a status that maps to delivered without a matching 21xx event."""
    raw = active_sample()
    raw["LastStatusCode"] = 2100  # delivered, but Protocol has no 21xx entry
    parcel = normalize_parcel(raw)
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is None


def test_normalize_warns_on_schema_drift(caplog):
    raw = delivered_sample()
    raw["UnexpectedNewField"] = "surprise"
    normalize_parcel(raw)
    assert "schema drift" in caplog.text.lower() or "unexpected top-level" in caplog.text


def test_normalize_warns_when_token_infos_populated_unexpectedly(caplog):
    raw = delivered_sample()
    raw["TokenInfos"] = {"Customer": "should never be populated on the public path"}
    normalize_parcel(raw)
    assert "TokenInfos" in caplog.text


def test_normalize_no_warning_when_token_infos_is_null():
    raw = delivered_sample()
    assert raw["TokenInfos"] is None
    normalize_parcel(raw)  # must not raise, must not warn — nothing to assert on caplog


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_descending_puts_unparseable_last():
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


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": None},
        {"barcode": "b", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["b", "a"]


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
