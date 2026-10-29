"""Tests for the Hetzner API client."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (  # type: ignore[import-untyped]
    AiohttpClientMocker,
)

from custom_components.hetzner.api import (
    HetznerApiError,
    HetznerAuthError,
    HetznerClient,
    HetznerConnectionError,
    HetznerError,
    HetznerNotFoundError,
    HetznerResponseError,
    HetznerTimeoutError,
)
from custom_components.hetzner.const import DEFAULT_HOST

from tests.conftest import API_KEY, BOX_URL, LIST_URL, STORAGE_BOX_ID


def _client(hass: HomeAssistant) -> HetznerClient:
    """Return a client using the mocked shared session."""
    return HetznerClient(async_get_clientsession(hass), API_KEY, DEFAULT_HOST)


def _error_body(code: Any) -> dict[str, Any]:
    """Return an API error body with the given code."""
    return {"error": {"code": code, "message": "message", "details": {}}}


async def test_list_storage_boxes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """The list is returned and the token is sent as bearer token."""
    aioclient_mock.get(LIST_URL, json={"storage_boxes": [storage_box]})

    assert await _client(hass).async_list_storage_boxes() == [storage_box]
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert str(url) == LIST_URL
    assert headers == {"Authorization": f"Bearer {API_KEY}"}


@pytest.mark.parametrize("payload", [{}, {"storage_boxes": None}])
async def test_list_storage_boxes_missing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, payload: Any
) -> None:
    """A response without boxes is an empty list."""
    aioclient_mock.get(LIST_URL, json=payload)

    assert await _client(hass).async_list_storage_boxes() == []


async def test_get_storage_box(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """One box is returned from its own endpoint."""
    aioclient_mock.get(BOX_URL, json={"storage_box": storage_box})

    assert await _client(hass).async_get_storage_box(STORAGE_BOX_ID) == storage_box
    assert str(aioclient_mock.mock_calls[0][1]) == BOX_URL


@pytest.mark.parametrize("payload", [{}, {"storage_box": None}])
async def test_get_storage_box_missing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, payload: Any
) -> None:
    """A response without a box is an empty dict."""
    aioclient_mock.get(BOX_URL, json=payload)

    assert await _client(hass).async_get_storage_box(STORAGE_BOX_ID) == {}


@pytest.mark.parametrize(
    ("response", "exc_type", "status", "code"),
    [
        (
            {"status": 401, "json": _error_body("unauthorized")},
            HetznerAuthError,
            401,
            "unauthorized",
        ),
        ({"status": 401}, HetznerAuthError, 401, None),
        (
            {"status": 403, "json": _error_body("forbidden")},
            HetznerAuthError,
            403,
            "forbidden",
        ),
        # Without a readable code a 403 is taken as missing permissions
        ({"status": 403}, HetznerAuthError, 403, None),
        (
            {"status": 403, "text": "<html>Forbidden</html>"},
            HetznerAuthError,
            403,
            None,
        ),
        ({"status": 403, "json": ["forbidden"]}, HetznerAuthError, 403, None),
        ({"status": 403, "json": {"error": "forbidden"}}, HetznerAuthError, 403, None),
        ({"status": 403, "json": _error_body(42)}, HetznerAuthError, 403, None),
        # Neither is fixed by a new token
        (
            {"status": 403, "json": _error_body("maintenance")},
            HetznerApiError,
            403,
            "maintenance",
        ),
        (
            {"status": 403, "json": _error_body("resource_limit_exceeded")},
            HetznerApiError,
            403,
            "resource_limit_exceeded",
        ),
        (
            {"status": 404, "json": _error_body("not_found")},
            HetznerNotFoundError,
            404,
            "not_found",
        ),
        (
            {"status": 429, "json": _error_body("rate_limit_exceeded")},
            HetznerApiError,
            429,
            "rate_limit_exceeded",
        ),
        ({"status": 500}, HetznerApiError, 500, None),
        ({"status": 503, "text": "Service Unavailable"}, HetznerApiError, 503, None),
        # Successful status, unusable body
        ({"text": "not json"}, HetznerApiError, 200, "invalid_json"),
        ({"json": ["not", "an", "object"]}, HetznerApiError, 200, "invalid_json"),
    ],
)
async def test_error_responses(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    response: dict[str, Any],
    exc_type: type[HetznerResponseError],
    status: int,
    code: str | None,
) -> None:
    """HTTP errors are mapped by status and error code."""
    aioclient_mock.get(BOX_URL, **response)

    with pytest.raises(exc_type) as exc_info:
        await _client(hass).async_get_storage_box(STORAGE_BOX_ID)

    assert exc_info.value.status == status
    assert exc_info.value.code == code
    expected = f"HTTP {status}" if code is None else f"HTTP {status} ({code})"
    assert str(exc_info.value) == expected


async def test_auth_error_on_list(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Listing maps errors the same way."""
    aioclient_mock.get(LIST_URL, status=401)

    with pytest.raises(HetznerAuthError):
        await _client(hass).async_list_storage_boxes()


@pytest.mark.parametrize(
    ("exc", "exc_type"),
    [
        (asyncio.TimeoutError(), HetznerTimeoutError),
        (aiohttp.ClientError("boom"), HetznerConnectionError),
        (aiohttp.ClientConnectionError("refused"), HetznerConnectionError),
    ],
)
async def test_connection_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    exc: Exception,
    exc_type: type[HetznerConnectionError],
) -> None:
    """Timeouts and aiohttp errors become connection errors."""
    aioclient_mock.get(BOX_URL, exc=exc)

    with pytest.raises(exc_type) as exc_info:
        await _client(hass).async_get_storage_box(STORAGE_BOX_ID)

    assert exc_info.value.__cause__ is exc


def test_exception_hierarchy() -> None:
    """Callers can catch every client error through the base class."""
    for exc_type in (
        HetznerAuthError,
        HetznerNotFoundError,
        HetznerApiError,
        HetznerConnectionError,
        HetznerTimeoutError,
    ):
        assert issubclass(exc_type, HetznerError)
    assert issubclass(HetznerTimeoutError, HetznerConnectionError)
    assert not issubclass(HetznerAuthError, HetznerApiError)
