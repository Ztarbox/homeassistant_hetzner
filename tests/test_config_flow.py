"""Tests for the Hetzner Storage Box config and options flows."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import aiohttp
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
import pytest
from pytest_homeassistant_custom_component.common import (  # type: ignore[import-untyped]
    MockConfigEntry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (  # type: ignore[import-untyped]
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.hetzner.config_flow import HetznerConfigFlow
from custom_components.hetzner.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_STORAGE_BOX_ID,
    CONF_UNIT,
    DEFAULT_HOST,
    DEFAULT_UNIT,
    DOMAIN,
)

from tests.conftest import API_KEY, LIST_URL, STORAGE_BOX_ID

BOX_DESCRIPTION = f"backup (ID: {STORAGE_BOX_ID}, Status: active, Type: bx11, BX11)"
BOX_OPTION = {"value": STORAGE_BOX_ID, "label": BOX_DESCRIPTION}


def _queue_list_responses(
    aioclient_mock: AiohttpClientMocker, *responses: dict[str, Any]
) -> None:
    """Register LIST_URL so successive GETs return the given responses in order.

    aioclient_mock always answers with the first matching registration, so a
    side_effect is used to hand out a different response per call.
    """
    queue = list(responses)

    async def _side_effect(
        method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(method, url, **queue.pop(0))

    aioclient_mock.get(LIST_URL, side_effect=_side_effect)


def _list_payload(*boxes: dict[str, Any]) -> dict[str, Any]:
    """Return a successful list response containing the given boxes."""
    return {"json": {"storage_boxes": list(boxes)}}


async def _start_flow(hass: HomeAssistant) -> ConfigFlowResult:
    """Start a user config flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _submit_key(
    hass: HomeAssistant, flow_id: str, api_key: str = API_KEY, unit: str = "GB"
) -> ConfigFlowResult:
    """Submit the user step."""
    return await hass.config_entries.flow.async_configure(
        flow_id, {CONF_API_KEY: api_key, CONF_UNIT: unit}
    )


def _schema(result: ConfigFlowResult) -> dict[Any, Any]:
    """Return the schema dict of a form result."""
    data_schema = result["data_schema"]
    assert data_schema is not None
    return data_schema.schema


async def test_user_step_shows_form(hass: HomeAssistant) -> None:
    """Test the initial form is shown."""
    result = await _start_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


@pytest.mark.parametrize("api_key", ["", "   "])
async def test_user_step_api_key_required(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, api_key: str
) -> None:
    """Test an empty or whitespace API key is rejected without an API call."""
    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"], api_key=api_key)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_API_KEY: "api_key_required"}
    assert aioclient_mock.call_count == 0


@pytest.mark.parametrize(
    ("response", "error"),
    [
        ({"status": 401}, "invalid_api_key"),
        ({"status": 404}, "invalid_host"),
        ({"status": 500}, "api_error"),
        ({"exc": asyncio.TimeoutError()}, "connection_timeout"),
        ({"exc": aiohttp.ClientError("boom")}, "validation_error"),
    ],
)
async def test_user_step_validation_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    response: dict[str, Any],
    error: str,
) -> None:
    """Test API key validation failures are mapped to form errors."""
    aioclient_mock.get(LIST_URL, **response)

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_API_KEY: error}


async def test_full_flow_creates_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test a valid key and box selection create a config entry."""
    payload = _list_payload(storage_box)
    _queue_list_responses(aioclient_mock, payload, payload, payload)

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"], api_key=f"  {API_KEY} ")

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "storage_box"
    assert result["errors"] == {}
    selector = _schema(result)[CONF_STORAGE_BOX_ID]
    assert selector.config["options"] == [BOX_OPTION]
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers == {"Authorization": f"Bearer {API_KEY}"}

    with patch("custom_components.hetzner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STORAGE_BOX_ID: STORAGE_BOX_ID}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hetzner backup"
    assert result["data"] == {
        CONF_API_KEY: API_KEY,
        CONF_HOST: DEFAULT_HOST,
        CONF_STORAGE_BOX_ID: STORAGE_BOX_ID,
        CONF_UNIT: "GB",
    }
    entry = result["result"]
    assert entry.unique_id == STORAGE_BOX_ID
    assert entry.version == 3


async def test_storage_box_step_lists_multiple_boxes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test every box is offered and a box without a type still lists."""
    other = {"id": 42, "name": "other", "status": "initializing"}
    payload = _list_payload(storage_box, other)
    _queue_list_responses(aioclient_mock, payload, payload)

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])

    selector = _schema(result)[CONF_STORAGE_BOX_ID]
    assert selector.config["options"] == [
        BOX_OPTION,
        {
            "value": "42",
            "label": "other (ID: 42, Status: initializing, Type: None, None)",
        },
    ]


async def test_storage_box_step_box_changed_before_submit(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test a box whose status changes before submitting can still be selected."""
    changed = {**storage_box, "status": "locked"}
    _queue_list_responses(
        aioclient_mock,
        _list_payload(storage_box),
        _list_payload(storage_box),
        _list_payload(changed),
    )

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])
    with patch("custom_components.hetzner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STORAGE_BOX_ID: STORAGE_BOX_ID}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STORAGE_BOX_ID] == STORAGE_BOX_ID


async def test_storage_box_without_name_uses_id_in_title(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test a box without a name gets its ID in the entry title."""
    storage_box["name"] = None
    aioclient_mock.get(LIST_URL, **_list_payload(storage_box))

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])
    with patch("custom_components.hetzner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STORAGE_BOX_ID: STORAGE_BOX_ID}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Hetzner {STORAGE_BOX_ID}"


@pytest.mark.parametrize(
    "list_response",
    [
        {"status": 500},
        {"exc": asyncio.TimeoutError()},
        {"exc": aiohttp.ClientError("boom")},
        {"json": {"storage_boxes": []}},
        {"json": {}},
    ],
)
async def test_storage_box_step_no_storage_boxes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    list_response: dict[str, Any],
) -> None:
    """Test a failed or empty box list shows the no_storage_boxes error."""
    _queue_list_responses(aioclient_mock, {"json": {}}, list_response)

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "storage_box"
    assert result["errors"] == {"base": "no_storage_boxes"}
    assert _schema(result) == {}


async def test_storage_box_step_invalid_selection(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test an unknown selection shows the invalid_selection error.

    The SelectSelector schema rejects unknown values before the step handler
    runs when going through the flow manager, so the step is called directly.
    """
    aioclient_mock.get(LIST_URL, **_list_payload(storage_box))
    flow = HetznerConfigFlow()
    flow.hass = hass
    flow.data = {CONF_API_KEY: API_KEY, CONF_HOST: DEFAULT_HOST, CONF_UNIT: "GB"}

    result = await flow.async_step_storage_box({CONF_STORAGE_BOX_ID: "nonexistent"})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "storage_box"
    assert result["errors"] == {"base": "invalid_selection"}
    selector = _schema(result)[CONF_STORAGE_BOX_ID]
    assert selector.config["options"] == [BOX_OPTION]


async def test_storage_box_step_already_configured(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Test selecting an already configured box aborts the flow."""
    aioclient_mock.get(LIST_URL, **_list_payload(storage_box))

    result = await _start_flow(hass)
    result = await _submit_key(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STORAGE_BOX_ID: STORAGE_BOX_ID}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


def _options_default(result: ConfigFlowResult) -> Any:
    """Return the default of the unit field in an options form."""
    for key in _schema(result):
        if key == CONF_UNIT:
            return key.default()
    raise AssertionError("unit field missing")


async def test_options_flow_defaults_to_data_unit(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test the options form defaults to the unit stored in data."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert _options_default(result) == "GB"


async def test_options_flow_options_override_data(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test the options form prefers the unit stored in options."""
    hass.config_entries.async_update_entry(config_entry, options={CONF_UNIT: "TB"})

    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert _options_default(result) == "TB"


async def test_options_flow_default_unit_fallback(hass: HomeAssistant) -> None:
    """Test the options form falls back to the default unit."""
    entry = MockConfigEntry(domain=DOMAIN, version=3, data={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert _options_default(result) == DEFAULT_UNIT


async def test_options_flow_saves_unit(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test submitting the options form stores the new unit."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UNIT: "TB"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_UNIT: "TB"}
    assert config_entry.options == {CONF_UNIT: "TB"}
    assert config_entry.state is config_entries.ConfigEntryState.NOT_LOADED


async def test_options_flow_rejects_unknown_unit(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test the options schema rejects units outside the allowed list."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_UNIT: "PB"}
        )


async def test_user_step_unit_defaults(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Test the unit falls back to the default when not submitted."""
    payload = _list_payload(storage_box)
    _queue_list_responses(aioclient_mock, payload, payload, payload)

    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    with patch("custom_components.hetzner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STORAGE_BOX_ID: STORAGE_BOX_ID}
        )
        await hass.async_block_till_done()

    assert result["data"][CONF_UNIT] == DEFAULT_UNIT
