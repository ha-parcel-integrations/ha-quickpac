"""Diagnostics support for the Quickpac parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import QuickpacConfigEntry

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
#
# The public path this integration uses never returns TokenInfos populated
# (it is only unlocked by POST GetToken, which this build never calls — see
# parcels.py's _check_token_infos), but the whole subtree is redacted anyway
# in case that assumption is ever wrong.
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "sender",
    "receiver",
    "url",
    # carrier payload identity fields
    "IdentCode",
    "IdentCodeFormatted",
    # the whole token-gated subtree, redacted defensively even though the
    # public path should never populate it
    "TokenInfos",
    "TokenExtraInfo",
    "TokenExtraInfoData",
    "Customer",
    "AddressName",
    "AddressStreet",
    "AddressZip",
    "AddressCity",
    "AddressCountry",
    "PickupStationName",
    "PickupStationAddress1",
    "PickupStationAddress2",
    "PickupStationZip",
    "PickupStationCity",
    "PickupStationPhone",
    "PickupStationCode",
    "NotificationEmail",
    "NotificationMobile",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: QuickpacConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Quickpac config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "polling": {
            "current_tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }
