"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

Quickpac-specific shape, in one paragraph: a ``TrackingInfoResponse`` carries
one ``LastStatusCode`` (int) plus a ``Protocol`` list of the same-vocabulary
events. The status vocabulary is **not** documented by Quickpac — it is
reverse-engineered from the tracking SPA's own icon-selection logic (see
:func:`map_parcel_status`), and no populated response has ever been seen on
the wire, so this ships pre-1.0 with one-shot ``WARNING``s on every unconfirmed shape per
CONVENTIONS.md.
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

# Quickpac is domestic to Switzerland. Its ``Time`` values carry no offset
# (see :func:`parse_quickpac_timestamp`) and are read as Europe/Zurich, the
# same choice ha-planzer makes for the same reason: UTC would shift every
# event by 1-2h depending on DST. Unverified — no populated response has ever
# been seen to check an offset against.
CARRIER_TZ = ZoneInfo("Europe/Zurich")

# Where users report a status/shape we do not map yet. Rewritten by the
# bootstrap script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-quickpac/issues/new"
    "?template=unrecognised_status.yml"
)

# Quickpac status vocabulary, reverse-engineered from the tracking SPA's own
# icon-selection logic (app.js), not from any documentation Quickpac
# publishes. ``n`` is ``LastStatusCode`` (or a ``Protocol[].Status``, same
# vocabulary); ``r = n // 100``.
#
# Three things that make this ordering-sensitive, not just table-lookup:
#
# * **`2600`/`2601`/`2604` must be matched before any `26xx` banding** — the
#   26-hundreds split three ways across three different canonical statuses
#   (Pickup / Prepare / Detour). There is no generic `r == 26` branch at all;
#   an unmatched `26xx` code correctly falls through to the catch-all.
# * **`3xxx` and `4xxx` are not delivered and not problems.** They are not
#   cases in the front-end at all — they fall to the `Info` catch-all →
#   `unknown` + a warning. A superseded third-party CLI invented
#   `>= 3000 -> delivered` / `>= 4000 -> exception`; both are wrong.
# * **`Info` (the catch-all) must never be mapped to `in_transit`** to make
#   dashboards look tidy — it is where the unmapped half of the code space
#   lives, and `unknown` + a warning is the only thing that ever teaches us
#   the rest of the vocabulary.
def _bucket(code: int) -> ParcelStatus | None:
    """Return the canonical status for one Quickpac status code, or ``None``.

    ``None`` means the code fell into Quickpac's own ``Info`` catch-all —
    the caller is responsible for warning and reporting ``unknown``.
    """
    remainder = code // 100
    if code == 1000:
        return ParcelStatus.REGISTERED
    if code == 2600:
        return ParcelStatus.AT_PICKUP_POINT
    if code in (2301, 2601):
        return ParcelStatus.IN_TRANSIT
    if code == 2604:
        # A redirect is still moving — not a problem, nothing failed.
        return ParcelStatus.IN_TRANSIT
    if remainder == 21:
        # Includes 2104 "sicher deponiert" — a CH safe-deposit is a delivery.
        return ParcelStatus.DELIVERED
    if remainder == 24:
        return ParcelStatus.RETURNING
    if remainder in (22, 25):
        return ParcelStatus.IN_TRANSIT
    if remainder in (12, 14, 15):
        return ParcelStatus.IN_TRANSIT
    if remainder in (18, 19):
        return ParcelStatus.OUT_FOR_DELIVERY
    return None


# Status codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[int] = set()

# One-shot flags for the pre-1.0 "we have never seen this populated/shaped"
# warnings that are not keyed on a specific status code.
_shape_warnings_logged: set[str] = set()

# Distinct StatusGroup values already logged (its vocabulary is undocumented;
# this is the cheapest route to learning it).
_status_groups_logged: set[str] = set()

# Known top-level keys of TrackingInfoResponse, per Quickpac's own OpenAPI
# document (schema-confirmed; values unseen). Anything else is schema drift.
_KNOWN_TOP_LEVEL_KEYS = frozenset(
    {
        "IdentCode",
        "IdentCodeFormatted",
        "LastStatus",
        "LastStatusCode",
        "StatusTooltipText",
        "Product",
        "ProductTooltipText",
        "FAQTag",
        "OutlookHeadline",
        "OutlookInfo",
        "PostTrackingUrl",
        "PlanzerTrackingUrl",
        "ForceForwardUrl",
        "QuickpicOrderUrl",
        "QuickpicReverseTrackingUrl",
        "QuickpicIsOrigin",
        "QuickpicHasOrder",
        "Protocol",
        "ServiceOptions",
        "TokenInfos",
    }
)


def reset_one_shot_warnings() -> None:
    """Clear the one-shot warning bookkeeping (tests only)."""
    _unmapped_statuses_logged.clear()
    _shape_warnings_logged.clear()
    _status_groups_logged.clear()


def _warn_once(key: str, message: str, *args: Any) -> None:
    """Log ``message`` at WARNING the first time ``key`` is seen this session.

    ``WARNING`` and not ``DEBUG``/``INFO`` on purpose: Home Assistant's
    default log level hides those, so a quieter level means nobody ever
    reports the shape and the unknown stays unknown forever.
    """
    if key in _shape_warnings_logged:
        return
    _shape_warnings_logged.add(key)
    _LOGGER.warning(message, *args)


def _warn_unmapped_status(code: int) -> None:
    """Log an unmapped Quickpac status code once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Quickpac status — help us map it. Open an issue "
        "and paste this line: %s\n  LastStatusCode=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
    )


def _log_status_group(group: Any, code: Any) -> None:
    """Log a never-before-seen ``StatusGroup`` value once.

    ``StatusGroup``'s value set is undocumented; logging the first pairing of
    each distinct value with the status it accompanied is the cheapest route
    to learning it.
    """
    if not group or group in _status_groups_logged:
        return
    _status_groups_logged.add(group)
    _LOGGER.warning(
        "Quickpac StatusGroup value seen for the first time — help us learn "
        "the vocabulary. Open an issue and paste this line: %s\n"
        "  StatusGroup=%r paired with Status=%r",
        NEW_ISSUE_URL,
        group,
        code,
    )


def map_parcel_status(code: Any) -> ParcelStatus:
    """Map a Quickpac ``LastStatusCode``/``Protocol[].Status`` to a canonical status.

    ``None`` or a non-numeric value (a shipment Quickpac has not registered
    yet) reports ``unknown`` silently; an unrecognised code reports
    ``unknown`` with a one-shot warning.
    """
    if not isinstance(code, int):
        return ParcelStatus.UNKNOWN
    mapped = _bucket(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return ParcelStatus.UNKNOWN


def map_event_status(code: Any) -> ParcelStatus | None:
    """Map a ``Protocol`` entry's status code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to
    unknown") and warn once, reusing the parcel-status one-shot set.
    """
    if not isinstance(code, int):
        return None
    mapped = _bucket(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set. Quickpac timestamps are made aware by
    :func:`parse_quickpac_timestamp` long before they reach here; this is the
    generic reader used by the sensor and calendar platforms on values we
    published ourselves.
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


def parse_quickpac_timestamp(value: Any) -> datetime | None:
    """Parse a Quickpac ``Time`` field into an aware datetime.

    ``Time`` (``yyyy-MM-ddTHH:mm:ss``) carries no timezone offset and this is
    a domestic Swiss carrier, so a naive value is read as ``Europe/Zurich``
    rather than UTC — reading it as UTC would shift every event by 1-2h
    depending on DST. *Unverified*: no sample has ever carried an offset to
    check against, and the first parse failure warns once.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        _warn_once(
            "timestamp_format",
            "Quickpac returned a Time value that does not parse as "
            "yyyy-MM-ddTHH:mm:ss — the naive-timestamp assumption may be "
            "wrong. Please report it: %s\n  value=%r",
            NEW_ISSUE_URL,
            value,
        )
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CARRIER_TZ)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for a Quickpac ``Time`` field, or ``None``."""
    parsed = parse_quickpac_timestamp(value)
    return parsed.isoformat() if parsed else None


def protocol_entries(raw: dict) -> list[dict]:
    """Return ``raw["Protocol"]``, always as a list of dicts."""
    protocol = raw.get("Protocol")
    if not isinstance(protocol, list):
        return []
    return [entry for entry in protocol if isinstance(entry, dict)]


def _dated_protocol(protocol: list[dict]) -> list[tuple[datetime, dict]]:
    """Pair each protocol entry with its parsed ``Time``, dropping unparseable ones."""
    dated = []
    for entry in protocol:
        if not isinstance(entry, dict):
            continue
        parsed = parse_quickpac_timestamp(entry.get("Time"))
        if parsed is not None:
            dated.append((parsed, entry))
    return dated


def _check_protocol_order(dated: list[tuple[datetime, dict]]) -> None:
    """Warn once if the wire order of ``Protocol`` was not already ascending.

    ``Protocol[]`` order is unverified — sorting on ``Time`` ourselves removes
    the question regardless, but a wire order that turns out descending (or
    unordered) is worth knowing about, since it may hint at a field we are
    misreading elsewhere.
    """
    times = [ts for ts, _ in dated]
    if len(times) >= 2 and times != sorted(times):
        _warn_once(
            "protocol_order",
            "Quickpac Protocol entries did not arrive already ordered "
            "oldest→newest by Time — sorting before building history. "
            "Please report it: %s",
            NEW_ISSUE_URL,
        )


def _check_token_infos(raw: dict) -> None:
    """Warn once if ``TokenInfos`` is populated without a token being requested.

    The integration never calls ``GetToken``, so ``TokenInfos`` should always
    be ``null``. If it ever isn't, the "everything sender/receiver/pickup is
    gated behind a token" assumption this build made needs revisiting.
    """
    if raw.get("TokenInfos"):
        _warn_once(
            "token_infos_populated",
            "Quickpac returned a non-null TokenInfos without requesting a "
            "token — the gated-fields assumption (sender/receiver/pickup_point "
            "all None on the public path) may be wrong. Please report it: %s",
            NEW_ISSUE_URL,
        )


def _check_schema_drift(raw: dict) -> None:
    """Warn once (per distinct key set) about unexpected top-level keys."""
    unexpected = tuple(sorted(set(raw) - _KNOWN_TOP_LEVEL_KEYS))
    if not unexpected:
        return
    _warn_once(
        f"schema_drift:{unexpected}",
        "Quickpac TrackingInfoResponse has unexpected top-level key(s) — the "
        "schema may have changed since it was captured. Please report it: "
        "%s\n  unexpected keys=%s",
        NEW_ISSUE_URL,
        list(unexpected),
    )


def build_history(
    protocol: list[dict], *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from ``Protocol``.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. Sorted oldest→newest by :func:`parse_quickpac_timestamp`
    regardless of wire order (see :func:`_check_protocol_order`); entries
    whose ``Time`` fails to parse are dropped rather than guessed at.
    """
    dated = _dated_protocol(protocol)
    _check_protocol_order(dated)
    dated.sort(key=lambda item: item[0])

    history = [
        {
            "timestamp": timestamp.isoformat(),
            "status": map_event_status(entry.get("Status")),
            "raw_status": entry.get("StatusText") or entry.get("Status"),
        }
        for timestamp, entry in dated
    ]
    for _, entry in dated:
        _log_status_group(entry.get("StatusGroup"), entry.get("Status"))
    return history[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def _delivered_at(dated_protocol: list[tuple[datetime, dict]]) -> str | None:
    """Return the ``Time`` of the newest delivered-bucket Protocol entry.

    There is no dedicated delivery-timestamp field on the response — the
    newest entry whose own ``Status`` maps to ``delivered`` is the best
    available signal.
    """
    for timestamp, entry in sorted(dated_protocol, key=lambda item: item[0], reverse=True):
        if map_event_status(entry.get("Status")) is ParcelStatus.DELIVERED:
            return timestamp.isoformat()
    return None


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order, and the aggregator and
    cross-carrier dashboards depend on it. A key Quickpac does not expose is
    ``None``, never omitted.

    Quickpac-specific decisions:

    * ``sender``/``receiver``/``pickup_point``/``planned_from``/``planned_to``
      are always ``None`` — all of them sit behind ``POST GetToken`` (surname
      + postcode), and this build ships the public path only. There is also
      no ETA field anywhere on the public response.
    * ``pickup`` reflects the canonical status (``True`` exactly when
      ``status`` is ``at_pickup_point``, i.e. ``LastStatusCode == 2600``) even
      though the *address* (``pickup_point``) stays gated — "code 2600
      carries the state, just not the address." Unlike GLS/Dragonfly, where
      ``pickup`` is an independent delivery-method signal that can be
      ``True`` *before* the parcel arrives (so a "still en route to the
      parcel shop" sensor is possible), Quickpac's public payload has no such
      signal — ``pickup`` can never lead ``status`` here, it only flips true
      at the same moment ``status`` already says so.
    * ``weight``/``dimensions`` are always ``None`` — not in the schema.
    """
    _check_schema_drift(raw)
    _check_token_infos(raw)

    tracking_code = raw.get("IdentCode")
    status = map_parcel_status(raw.get("LastStatusCode"))
    delivered = status is ParcelStatus.DELIVERED

    protocol = protocol_entries(raw)
    dated_protocol = _dated_protocol(protocol)

    return {
        "carrier": "Quickpac",
        "barcode": tracking_code,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": raw.get("LastStatus") or raw.get("LastStatusCode"),
        "delivered": delivered,
        "delivered_at": _delivered_at(dated_protocol) if delivered else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": None,
        "url": tracking_url(tracking_code),
        "weight": None,
        "dimensions": None,
        "history": build_history(protocol) if include_history else None,
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
