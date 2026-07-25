"""Tests for the Correos API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.correos.api import (
    CorreosApiClient,
    CorreosApiError,
)

CODE = "EXAMPLE123456"


def _session_returning(status: int, body: object = None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


def _envelope(cod_error: str, **extra) -> list[dict]:
    """A single-element ``localizador`` array with the given result code."""
    return [{"error": {"codError": cod_error, "desError": "x"}, **extra}]


async def test_get_parcel_returns_envelope_on_success():
    session = _session_returning(200, _envelope("0", codEnvio=CODE, eventos=[]))
    client = CorreosApiClient(session)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["codEnvio"] == CODE
    # the tracking code ends up in the URL
    assert CODE in session.get.call_args[0][0]


async def test_get_parcel_returns_none_when_not_found():
    """A non-zero codError (no traceability) is a normal state, not an error."""
    client = CorreosApiClient(_session_returning(200, _envelope("3")))
    assert await client.async_get_parcel("EXAMPLE000000") is None


async def test_get_parcel_accepts_a_bare_object_envelope():
    """Some error bodies come back unwrapped rather than in an array."""
    client = CorreosApiClient(
        _session_returning(200, {"error": {"codError": "3"}})
    )
    assert await client.async_get_parcel(CODE) is None


async def test_get_parcel_raises_on_error_status():
    client = CorreosApiClient(_session_returning(500, {}))
    with pytest.raises(CorreosApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_unparseable_body():
    client = CorreosApiClient(_session_returning(200, "not json"))
    with pytest.raises(CorreosApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_container_body():
    client = CorreosApiClient(_session_returning(200, 42))
    with pytest.raises(CorreosApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_empty_array():
    client = CorreosApiClient(_session_returning(200, []))
    with pytest.raises(CorreosApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_object_envelope():
    client = CorreosApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(CorreosApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_missing_error_envelope():
    """No ``error`` block at all means we did not get a shipment envelope."""
    client = CorreosApiClient(_session_returning(200, [{"codEnvio": CODE}]))
    with pytest.raises(CorreosApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = CorreosApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)
