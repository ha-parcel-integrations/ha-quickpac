"""Tests for the Quickpac API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.quickpac.api import QuickpacApiClient, QuickpacApiError

CODE = "990000123456"


def _response(status: int, body: object = None) -> AsyncMock:
    response = AsyncMock()
    response.status = status
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    return response


def _ctx(response: AsyncMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _session_returning(status: int, body: object = None) -> MagicMock:
    session = MagicMock()
    session.get = MagicMock(return_value=_ctx(_response(status, body)))
    return session


def _session_with(*, tracking_status: int, tracking_body: object, canary_status: int) -> MagicMock:
    """A session whose two distinct URLs answer independently."""
    tracking_ctx = _ctx(_response(tracking_status, tracking_body))
    canary_ctx = _ctx(_response(canary_status, {"TextMap": {}}))

    def _get(url, *args, **kwargs):
        return canary_ctx if "GetTrackingStartup" in url else tracking_ctx

    session = MagicMock()
    session.get = MagicMock(side_effect=_get)
    return session


async def test_get_parcel_returns_payload_on_success():
    session = _session_returning(200, {"IdentCode": CODE, "LastStatusCode": 1000})
    client = QuickpacApiClient(session)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["IdentCode"] == CODE
    # the tracking code ends up in the URL
    assert CODE in session.get.call_args[0][0]


async def test_get_parcel_returns_none_on_400_when_canary_succeeds():
    """A 400 with a healthy canary means the code is unknown/not yet registered."""
    session = _session_with(tracking_status=400, tracking_body={}, canary_status=200)
    client = QuickpacApiClient(session)
    assert await client.async_get_parcel("990000000000") is None


async def test_get_parcel_raises_on_400_when_canary_also_fails(caplog):
    """A 400 whose canary also fails means the route moved, not the code."""
    session = _session_with(tracking_status=400, tracking_body={}, canary_status=500)
    client = QuickpacApiClient(session)
    with pytest.raises(QuickpacApiError):
        await client.async_get_parcel(CODE)
    assert "liveness canary" in caplog.text


async def test_canary_failure_warns_only_once(caplog):
    session = _session_with(tracking_status=400, tracking_body={}, canary_status=500)
    client = QuickpacApiClient(session)
    for _ in range(3):
        with pytest.raises(QuickpacApiError):
            await client.async_get_parcel(CODE)
    assert caplog.text.count("liveness canary") == 1


async def test_canary_recovery_resets_the_warning_flag(caplog):
    """A canary that later succeeds clears the flag, so a fresh failure warns again."""
    client = QuickpacApiClient(
        _session_with(tracking_status=400, tracking_body={}, canary_status=500)
    )
    with pytest.raises(QuickpacApiError):
        await client.async_get_parcel(CODE)

    client._session = _session_with(
        tracking_status=400, tracking_body={}, canary_status=200
    )
    assert await client.async_get_parcel(CODE) is None

    client._session = _session_with(
        tracking_status=400, tracking_body={}, canary_status=500
    )
    with pytest.raises(QuickpacApiError):
        await client.async_get_parcel(CODE)
    assert caplog.text.count("liveness canary") == 2


async def test_get_parcel_raises_on_error_status():
    client = QuickpacApiClient(_session_returning(500, {}))
    with pytest.raises(QuickpacApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_unparseable_body():
    client = QuickpacApiClient(_session_returning(200, "not json"))
    with pytest.raises(QuickpacApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_object_body():
    client = QuickpacApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(QuickpacApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = QuickpacApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)


async def test_canary_network_error_is_treated_as_dead():
    """A canary that cannot even connect is a failed canary, not a crash."""
    tracking_ctx = _ctx(_response(400, {}))

    def _get(url, *args, **kwargs):
        if "GetTrackingStartup" in url:
            raise aiohttp.ClientError("boom")
        return tracking_ctx

    session = MagicMock()
    session.get = MagicMock(side_effect=_get)
    client = QuickpacApiClient(session)
    with pytest.raises(QuickpacApiError):
        await client.async_get_parcel(CODE)
