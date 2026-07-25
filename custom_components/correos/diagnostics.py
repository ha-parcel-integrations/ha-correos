"""Diagnostics support for the Correos parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import CorreosConfigEntry

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
#
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "sender",
    "receiver",
    "url",
    # Correos envelope fields (``localizador`` response)
    "codEnvio",          # the tracking number
    "nombre_cliente",    # addressee name
    "numReferencia1",    # customer/order reference numbers
    "numReferencia2",
    "numReferencia3",
    "observaciones",     # free-text notes; may carry an address
    "nom_codired",       # named delivery office
    # generic fields that turn up in nested blocks
    "trackingNumber",
    "recipient",
    "address",
    "postalCode",
    "postal_code",
    "city",
    "street",
    "email",
    "name",
    "signature",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CorreosConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Correos config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }
