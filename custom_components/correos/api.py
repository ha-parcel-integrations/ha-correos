"""Correos public tracking API client.

Correos exposes a keyless ``localizador`` traceability service: the tracking
code alone keys the lookup — no account, no API key, no header. It answers
HTTP 200 with a JSON **array** whose first element is the shipment envelope::

    [{"codEnvio": "...", "eventos": [...],
      "error": {"codError": "0", "desError": "OK"}}]

The transport status is always 200; the real result lives in
``[0].error.codError`` as a string — ``"0"`` means the parcel was found, any
other value (e.g. ``"3"`` — *Sin Trazabilidad en Minerva*) means the code is
unknown or not yet scanned. Branch on the body, never on the HTTP status.

The contract the coordinator relies on:

* ``async_get_parcel`` returns the raw envelope dict on success,
* returns ``None`` when Correos reports the code as unknown/not-yet-scanned (a
  normal, expected state — never an error),
* raises :class:`CorreosApiError` for a malformed or unexpected body,
* lets ``aiohttp.ClientError`` propagate untouched — ``DataUpdateCoordinator``
  already wraps those into ``UpdateFailed``.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import TRACKING_API_URL

_LOGGER = logging.getLogger(__name__)


class CorreosApiError(Exception):
    """Raised when a Correos API call returns an unexpected response."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Correos API request failed: {detail}")
        self.detail = detail


class CorreosApiClient:
    """Client for the keyless Correos ``localizador`` tracking endpoint."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking envelope.

        Returns the shipment envelope for a known parcel, or ``None`` when
        Correos reports the code as unknown — which is also what a
        not-yet-scanned parcel gets. A malformed body or a non-2xx status
        raises :class:`CorreosApiError`; network errors propagate as
        ``aiohttp.ClientError``.
        """
        url = TRACKING_API_URL.format(tracking_code=tracking_code)
        async with self._session.get(url) as response:
            if response.status != 200:
                raise CorreosApiError(f"HTTP {response.status}")
            try:
                # content_type=None: the endpoint serves JSON without a reliable
                # JSON content-type, which aiohttp would otherwise refuse.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise CorreosApiError(f"unparseable body ({err})") from err

        # The endpoint returns a single-element array; accept a bare object too,
        # since some error bodies come back unwrapped.
        if isinstance(payload, list):
            envelope = payload[0] if payload else None
        elif isinstance(payload, dict):
            envelope = payload
        else:
            raise CorreosApiError("unexpected body (not a JSON array or object)")

        if not isinstance(envelope, dict):
            raise CorreosApiError("unexpected body (empty or malformed envelope)")

        error = envelope.get("error")
        if not isinstance(error, dict) or error.get("codError") is None:
            # Every real response — success or not — carries an error block with
            # a codError string. Its absence means we did not get a shipment
            # envelope at all.
            raise CorreosApiError("missing error envelope")

        cod_error = str(error.get("codError"))
        if cod_error == "0":
            return envelope

        # Any non-zero code means "no traceability": an unknown or not-yet-
        # scanned code, which is a normal, expected state rather than an error.
        _LOGGER.debug(
            "Correos has no traceability for %s (codError=%s: %s)",
            tracking_code,
            cod_error,
            error.get("desError"),
        )
        return None
