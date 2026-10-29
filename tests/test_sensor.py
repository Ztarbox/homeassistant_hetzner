"""Tests for the Hetzner Storage Box sensor platform."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any
from unittest.mock import MagicMock

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (  # type: ignore[import-untyped]
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (  # type: ignore[import-untyped]
    AiohttpClientMocker,
)

from custom_components.hetzner.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_STORAGE_BOX_ID,
    CONF_UNIT,
    DEFAULT_HOST,
    DOMAIN,
)
from custom_components.hetzner.sensor import HetznerSizeSensor

from tests.conftest import API_KEY, BOX_URL, STORAGE_BOX_ID

PREFIX = "sensor.storage_box_backup"

# entity_id -> unique_id for the GB fixture entry
EXPECTED_ENTITIES = {
    PREFIX: f"hetzner_storage_box_{STORAGE_BOX_ID}",
    f"{PREFIX}_location": f"hetzner_storage_box_{STORAGE_BOX_ID}_location",
    f"{PREFIX}_access_options": f"hetzner_storage_box_{STORAGE_BOX_ID}_access_options",
    f"{PREFIX}_total_size": f"hetzner_storage_box_{STORAGE_BOX_ID}_total_gb",
    f"{PREFIX}_total_used": f"hetzner_storage_box_{STORAGE_BOX_ID}_used_gb",
    f"{PREFIX}_data_size": f"hetzner_storage_box_{STORAGE_BOX_ID}_data_gb",
    f"{PREFIX}_snapshot_size": f"hetzner_storage_box_{STORAGE_BOX_ID}_snapshots_gb",
    f"{PREFIX}_free_space": f"hetzner_storage_box_{STORAGE_BOX_ID}_free_gb",
}

SIZE_ENTITIES = {
    "total": "total_size",
    "used": "total_used",
    "data": "data_size",
    "snapshots": "snapshot_size",
    "free": "free_space",
}


def _mock_box(
    aioclient_mock: AiohttpClientMocker, payload: Any, status: int = 200
) -> None:
    """Register the storage box endpoint response."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, json={"storage_box": payload}, status=status)


def _make_entry(hass: HomeAssistant, **data: Any) -> MockConfigEntry:
    """Create and add a v3 config entry with the given data overrides."""
    entry_data = {
        CONF_API_KEY: API_KEY,
        CONF_HOST: DEFAULT_HOST,
        CONF_STORAGE_BOX_ID: STORAGE_BOX_ID,
        CONF_UNIT: "GB",
    }
    entry_data.update(data)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hetzner backup",
        version=3,
        unique_id=STORAGE_BOX_ID,
        data={k: v for k, v in entry_data.items() if v is not None},
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the config entry."""
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _tick(hass: HomeAssistant) -> None:
    """Advance time past the coordinator update interval."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=601))
    await hass.async_block_till_done()


async def test_entities_created(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """All entities are created under one storage box device."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    # Entities reuse the first refresh instead of fetching again
    assert aioclient_mock.call_count == 1
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {API_KEY}"

    ent_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
    assert {e.entity_id: e.unique_id for e in entries} == EXPECTED_ENTITIES

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device_by_identifier(
        (DOMAIN, STORAGE_BOX_ID), config_entry.entry_id
    )
    assert device is not None
    assert device.name == "Storage Box backup"
    assert device.manufacturer == "Hetzner"
    assert device.model == "bx11"
    assert device.configuration_url == "https://console.hetzner.com"
    assert {e.device_id for e in entries} == {device.id}
    assert len(dr.async_entries_for_config_entry(dev_reg, config_entry.entry_id)) == 1


async def test_status_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Status sensor exposes status and box details."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    state = hass.states.get(PREFIX)
    assert state is not None
    assert state.state == "active"
    assert state.attributes["icon"] == "mdi:database"
    assert state.attributes["friendly_name"] == "Storage Box backup"
    for key in ("id", "name", "username", "server", "system", "created"):
        assert state.attributes[key] == storage_box[key]
    assert state.attributes["storage_box_type"] == "bx11"


async def test_location_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Location sensor exposes location name and details."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    state = hass.states.get(f"{PREFIX}_location")
    assert state is not None
    assert state.state == "fsn1"
    assert state.attributes["icon"] == "mdi:map-marker"
    location = storage_box["location"]
    for key in ("id", "country", "city", "latitude", "longitude", "description"):
        assert state.attributes[key] == location[key]


async def test_access_options_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Access options counts only True values and mirrors flags as bools."""
    storage_box["access_settings"]["samba_enabled"] = 1  # truthy, but not True
    storage_box["access_settings"]["webdav_enabled"] = None
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    state = hass.states.get(f"{PREFIX}_access_options")
    assert state is not None
    assert state.state == "3"
    assert state.attributes["icon"] == "mdi:network"
    assert state.attributes["state_class"] == "measurement"
    assert state.attributes["reachable_externally"] is True
    assert state.attributes["samba_enabled"] is True
    assert state.attributes["ssh_enabled"] is True
    assert state.attributes["webdav_enabled"] is False
    assert state.attributes["zfs_enabled"] is True


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        (
            "B",
            {
                "total": "1000000000000.0",
                "used": "300000000000.0",
                "data": "250000000000.0",
                "snapshots": "50000000000.0",
                "free": "700000000000.0",
            },
        ),
        (
            "MB",
            {
                "total": "1000000.0",
                "used": "300000.0",
                "data": "250000.0",
                "snapshots": "50000.0",
                "free": "700000.0",
            },
        ),
        (
            "GB",
            {
                "total": "1000.0",
                "used": "300.0",
                "data": "250.0",
                "snapshots": "50.0",
                "free": "700.0",
            },
        ),
        (
            "TB",
            {
                "total": "1.0",
                "used": "0.3",
                "data": "0.25",
                "snapshots": "0.05",
                "free": "0.7",
            },
        ),
    ],
)
async def test_size_sensors_units(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
    unit: str,
    expected: dict[str, str],
) -> None:
    """Size sensors convert bytes to the configured unit with data size metadata."""
    entry = _make_entry(hass, **{CONF_UNIT: unit})
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, entry)

    ent_reg = er.async_get(hass)
    for metric, suffix in SIZE_ENTITIES.items():
        entity_id = f"{PREFIX}_{suffix}"
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == expected[metric], metric
        assert state.attributes["unit_of_measurement"] == unit
        assert state.attributes["device_class"] == "data_size"
        assert state.attributes["state_class"] == "measurement"
        assert state.attributes["icon"] == "mdi:harddisk"
        reg_entry = ent_reg.async_get(entity_id)
        assert reg_entry is not None
        assert reg_entry.unique_id == (
            f"hetzner_storage_box_{STORAGE_BOX_ID}_{metric}_{unit.lower()}"
        )


async def test_size_sensor_rounding(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Converted values are rounded to two decimals."""
    storage_box["stats"]["size"] = 1_234_567_890
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    state = hass.states.get(f"{PREFIX}_total_used")
    assert state is not None
    assert state.state == "1.23"


async def test_free_space_clamped(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Free space never goes negative."""
    storage_box["stats"]["size_data"] = 2_000_000_000_000
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    state = hass.states.get(f"{PREFIX}_free_space")
    assert state is not None
    assert state.state == "0.0"


async def test_missing_stats_and_type(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Missing stats and storage box type report zero sizes."""
    del storage_box["stats"]
    storage_box["storage_box_type"] = None
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    for suffix in SIZE_ENTITIES.values():
        state = hass.states.get(f"{PREFIX}_{suffix}")
        assert state is not None
        assert state.state == "0.0", suffix

    state = hass.states.get(PREFIX)
    assert state is not None
    assert state.attributes["storage_box_type"] is None

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, STORAGE_BOX_ID), config_entry.entry_id
    )
    assert device is not None
    assert device.model is None


@pytest.mark.parametrize("payload", [None, {}])
async def test_empty_storage_box(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    payload: Any,
) -> None:
    """An empty storage_box response yields default values."""
    _mock_box(aioclient_mock, payload)
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED

    # Without a name the device falls back to the storage box id
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, STORAGE_BOX_ID), config_entry.entry_id
    )
    assert device is not None
    assert device.name == f"Storage Box {STORAGE_BOX_ID}"
    assert device.model is None

    prefix = f"sensor.storage_box_{STORAGE_BOX_ID}"
    status = hass.states.get(prefix)
    assert status is not None
    assert status.state == STATE_UNKNOWN
    assert status.attributes["id"] is None
    location = hass.states.get(f"{prefix}_location")
    assert location is not None
    assert location.state == STATE_UNKNOWN
    assert location.attributes["city"] is None
    access = hass.states.get(f"{prefix}_access_options")
    assert access is not None
    assert access.state == "0"
    for suffix in SIZE_ENTITIES.values():
        state = hass.states.get(f"{prefix}_{suffix}")
        assert state is not None
        assert state.state == "0.0", suffix


async def test_missing_storage_box_key(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A response without a storage_box key is treated as empty."""
    aioclient_mock.get(BOX_URL, json={})
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    state = hass.states.get(f"sensor.storage_box_{STORAGE_BOX_ID}_access_options")
    assert state is not None
    assert state.state == "0"


@pytest.mark.parametrize(
    "mock_kwargs",
    [
        {"status": 500},
        {"status": 401},
        {"exc": asyncio.TimeoutError},
        {"exc": aiohttp.ClientError},
    ],
)
async def test_first_refresh_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    mock_kwargs: dict[str, Any],
) -> None:
    """API errors on the first refresh retry setup and create no entities.

    Error statuses carry a valid body so only the status check can reject them.
    """
    aioclient_mock.get(BOX_URL, json={"storage_box": storage_box}, **mock_kwargs)
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert aioclient_mock.call_count == 1
    assert not hasattr(config_entry, "runtime_data")
    assert hass.states.async_entity_ids("sensor") == []
    ent_reg = er.async_get(hass)
    assert er.async_entries_for_config_entry(ent_reg, config_entry.entry_id) == []


async def test_first_refresh_error_recovers_on_retry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """A failed first refresh is retried and loads once the API recovers."""
    aioclient_mock.get(BOX_URL, status=500)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY

    _mock_box(aioclient_mock, storage_box)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(PREFIX).state == "active"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "mock_kwargs",
    [
        {"status": 500},
        {"status": 401},
        {"exc": asyncio.TimeoutError},
        {"exc": aiohttp.ClientError},
    ],
)
async def test_later_refresh_error_and_recovery(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    mock_kwargs: dict[str, Any],
) -> None:
    """Entities become unavailable on API errors and recover afterwards."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)
    assert hass.states.get(PREFIX).state == "active"  # type: ignore[union-attr]

    aioclient_mock.clear_requests()
    aioclient_mock.get(BOX_URL, json={"storage_box": storage_box}, **mock_kwargs)
    await _tick(hass)

    for entity_id in EXPECTED_ENTITIES:
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_UNAVAILABLE, entity_id

    storage_box["status"] = "locked"
    _mock_box(aioclient_mock, storage_box)
    await _tick(hass)

    assert hass.states.get(PREFIX).state == "locked"  # type: ignore[union-attr]
    for entity_id in EXPECTED_ENTITIES:
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state != STATE_UNAVAILABLE, entity_id


async def test_missing_storage_box_id(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without a storage box id setup fails, logs an error and adds no entities."""
    entry = _make_entry(hass, **{CONF_STORAGE_BOX_ID: None})
    with caplog.at_level(logging.ERROR):
        await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert not hasattr(entry, "runtime_data")
    assert "No storage box id in config entry" in caplog.text
    assert aioclient_mock.call_count == 0
    assert hass.states.async_entity_ids("sensor") == []
    ent_reg = er.async_get(hass)
    assert er.async_entries_for_config_entry(ent_reg, entry.entry_id) == []


async def test_unload_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Unloading the entry removes its data and makes entities unavailable."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)
    assert config_entry.runtime_data.coordinator.data == storage_box

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert not hasattr(config_entry, "runtime_data")
    state = hass.states.get(PREFIX)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


def test_size_sensor_invalid_metric() -> None:
    """An unknown metric raises ValueError."""
    with pytest.raises(ValueError, match="Invalid metric"):
        HetznerSizeSensor(MagicMock(), STORAGE_BOX_ID, "bogus", "GB")


def test_size_sensor_unknown_unit_falls_back_to_bytes() -> None:
    """An unknown display unit leaves the byte value unconverted."""
    coordinator = MagicMock()
    coordinator.data = {"storage_box_type": {"size": 1_000_000}}
    sensor = HetznerSizeSensor(coordinator, STORAGE_BOX_ID, "total", "KiB")
    assert sensor.native_value == 1_000_000.0
    assert sensor.unique_id == f"hetzner_storage_box_{STORAGE_BOX_ID}_total_kib"


def test_size_sensor_without_coordinator_data() -> None:
    """A coordinator without data reports zero."""
    coordinator = MagicMock()
    coordinator.data = None
    sensor = HetznerSizeSensor(coordinator, STORAGE_BOX_ID, "free", "GB")
    assert sensor.native_value == 0.0
    assert sensor.device_info["name"] == f"Storage Box {STORAGE_BOX_ID}"
