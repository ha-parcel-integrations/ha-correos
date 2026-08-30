"""Tests for Correos diagnostics."""
from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.correos.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "PQ0011223344ES"}]}
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "PQ0011223344ES",
            "sender": None,
            "receiver": "Jane Doe",
            "status": "out_for_delivery",
            "raw": {
                "codEnvio": "PQ0011223344ES",
                "nombre_cliente": "Jane Doe",
                "numReferencia1": "ORDER-4815162342",
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.current_tier_minutes = 15
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=15)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {"incoming_active": 1, "delivered": 0}
    assert result["polling"] == {
        "current_tier_minutes": 15,
        "update_interval_seconds": 900.0,
    }
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["receiver"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["codEnvio"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["nombre_cliente"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["numReferencia1"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "out_for_delivery"
