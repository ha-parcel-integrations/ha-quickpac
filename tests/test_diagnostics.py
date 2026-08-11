"""Tests for Quickpac diagnostics."""
from unittest.mock import MagicMock

from custom_components.quickpac.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "990000123456"}]}
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "990000123456",
            "sender": None,
            "receiver": None,
            "status": "out_for_delivery",
            "raw": {
                "IdentCode": "990000123456",
                "IdentCodeFormatted": "9900 0012 3456",
                "TokenInfos": {
                    "Customer": "should never be populated on the public path",
                    "AddressCity": "Zurich",
                },
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {"incoming_active": 1, "delivered": 0}
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["IdentCode"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["IdentCodeFormatted"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["TokenInfos"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "out_for_delivery"
