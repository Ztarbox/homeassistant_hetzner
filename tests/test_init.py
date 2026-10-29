"""Tests for the Hetzner Storage Box integration setup and migrations."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (  # type: ignore[import-untyped]
    MockConfigEntry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (  # type: ignore[import-untyped]
    AiohttpClientMocker,
)

from custom_components.hetzner import (
    _safe_update_unique_id,
    async_migrate_entry,
    async_unload_entry,
)
from custom_components.hetzner.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_STORAGE_BOX_ID,
    CONF_UNIT,
    DATA_COORDINATOR,
    DEFAULT_HOST,
    DOMAIN,
)
from custom_components.hetzner.coordinator import HetznerDataUpdateCoordinator

from tests.conftest import API_KEY, BOX_URL, STORAGE_BOX_ID

PREFIX = f"{DOMAIN}_storage_box_{STORAGE_BOX_ID}"
METRICS = ["total", "used", "data", "snapshots", "free"]
NON_SIZE_IDS = [PREFIX, f"{PREFIX}_location", f"{PREFIX}_access_options"]


def _mock_box(aioclient_mock: AiohttpClientMocker, box: dict[str, Any]) -> None:
    """Register the storage box API response."""
    aioclient_mock.get(BOX_URL, json={"storage_box": box})


def _make_entry(
    hass: HomeAssistant,
    version: int,
    unit: str = "GB",
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Create and add a config entry with the given version."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hetzner backup",
        version=version,
        unique_id=STORAGE_BOX_ID,
        data={
            CONF_API_KEY: API_KEY,
            CONF_HOST: DEFAULT_HOST,
            CONF_STORAGE_BOX_ID: STORAGE_BOX_ID,
            CONF_UNIT: unit,
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


def _register(
    hass: HomeAssistant, unique_id: str, entry: MockConfigEntry | None
) -> er.RegistryEntry:
    """Pre-populate the entity registry with a hetzner sensor."""
    return er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, unique_id, config_entry=entry
    )


def _size_entities(
    hass: HomeAssistant, entry: MockConfigEntry
) -> dict[str, er.RegistryEntry]:
    """Return size sensor registry entries of an entry keyed by metric."""
    result = {}
    for reg in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        for metric in METRICS:
            if str(reg.unique_id).startswith(f"{PREFIX}_{metric}_"):
                result[metric] = reg
    return result


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the entry and wait for it."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_unload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Setup stores entry data in hass.data, unload removes it."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    entry_data = dict(hass.data[DOMAIN][config_entry.entry_id])
    coordinator = entry_data.pop(DATA_COORDINATOR)
    assert isinstance(coordinator, HetznerDataUpdateCoordinator)
    assert coordinator.config_entry is not None
    assert coordinator.config_entry.entry_id == config_entry.entry_id
    assert coordinator.data == storage_box
    assert entry_data == {
        CONF_API_KEY: API_KEY,
        CONF_HOST: DEFAULT_HOST,
        CONF_UNIT: "GB",
    }

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert config_entry.entry_id not in hass.data[DOMAIN]


async def test_setup_options_unit_overrides_data(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Unit from options wins over unit from data."""
    _mock_box(aioclient_mock, storage_box)
    entry = _make_entry(hass, 3, unit="GB", options={CONF_UNIT: "TB"})
    await _setup(hass, entry)

    assert hass.data[DOMAIN][entry.entry_id][CONF_UNIT] == "TB"
    assert set(_size_entities(hass, entry)) == set(METRICS)
    for reg in _size_entities(hass, entry).values():
        assert str(reg.unique_id).endswith("_tb")


async def test_unload_failure_keeps_data(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Data is kept when unloading the platforms fails."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)

    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=False
    ):
        assert not await async_unload_entry(hass, config_entry)

    assert config_entry.entry_id in hass.data[DOMAIN]

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_migrate_v1_legacy_unique_ids(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Legacy size unique_ids get the unit appended and the entry is bumped to v3."""
    _mock_box(aioclient_mock, storage_box)
    entry = _make_entry(hass, 1, unit="GB")
    legacy = {m: _register(hass, f"{PREFIX}_{m}", entry) for m in METRICS}
    others = {uid: _register(hass, uid, entry) for uid in NON_SIZE_IDS}
    foreign = _register(hass, "some_other_unique_id", entry)

    await _setup(hass, entry)

    registry = er.async_get(hass)
    assert entry.version == 3
    for metric, reg in legacy.items():
        updated = registry.async_get(reg.entity_id)
        assert updated is not None
        assert updated.unique_id == f"{PREFIX}_{metric}_gb"
    for uid, reg in others.items():
        updated = registry.async_get(reg.entity_id)
        assert updated is not None
        assert updated.unique_id == uid
    updated = registry.async_get(foreign.entity_id)
    assert updated is not None
    assert updated.unique_id == "some_other_unique_id"

    # Migrated entities are reused by the platform: no duplicates created
    assert {r.entity_id for r in _size_entities(hass, entry).values()} == {
        r.entity_id for r in legacy.values()
    }
    for reg in legacy.values():
        state = hass.states.get(reg.entity_id)
        assert state is not None
        assert state.attributes["unit_of_measurement"] == "GB"


async def test_migrate_v1_uses_options_unit(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """Migration appends the unit from options when present."""
    _mock_box(aioclient_mock, storage_box)
    entry = _make_entry(hass, 1, unit="GB", options={CONF_UNIT: "TB"})
    reg = _register(hass, f"{PREFIX}_total", entry)

    await _setup(hass, entry)

    updated = er.async_get(hass).async_get(reg.entity_id)
    assert updated is not None
    assert updated.unique_id == f"{PREFIX}_total_tb"


async def test_migrate_v1_removes_conflicting_stale_entity(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stale entity holding the target unique_id is removed before renaming."""
    _mock_box(aioclient_mock, storage_box)
    entry = _make_entry(hass, 1, unit="GB")
    stale = _register(hass, f"{PREFIX}_total_gb", None)
    legacy = _register(hass, f"{PREFIX}_total", entry)

    await _setup(hass, entry)

    registry = er.async_get(hass)
    assert registry.async_get(stale.entity_id) is None
    updated = registry.async_get(legacy.entity_id)
    assert updated is not None
    assert updated.unique_id == f"{PREFIX}_total_gb"
    assert "Removing conflicting stale entity" in caplog.text
    assert entry.version == 3


async def test_migrate_v2_entry_bumped_without_renaming(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    storage_box: dict[str, Any],
) -> None:
    """A v2 entry is bumped to v3 once and its unique_ids are left alone."""
    _mock_box(aioclient_mock, storage_box)
    entry = _make_entry(hass, 2, unit="GB")
    reg = _register(hass, f"{PREFIX}_total_gb", entry)

    with patch(
        "custom_components.hetzner.async_migrate_entry",
        wraps=async_migrate_entry,
    ) as migrate:
        await _setup(hass, entry)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 3
    assert migrate.call_count == 1
    updated = er.async_get(hass).async_get(reg.entity_id)
    assert updated is not None
    assert updated.unique_id == f"{PREFIX}_total_gb"


async def test_migrate_current_entry_is_noop(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Migrating an entry that is already v3 changes nothing."""
    reg = _register(hass, f"{PREFIX}_total", config_entry)

    with patch.object(hass.config_entries, "async_update_entry") as update:
        assert await async_migrate_entry(hass, config_entry)

    update.assert_not_called()
    updated = er.async_get(hass).async_get(reg.entity_id)
    assert updated is not None
    assert updated.unique_id == f"{PREFIX}_total"


async def test_options_change_renames_and_reloads(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Changing the unit renames size unique_ids, keeps entity_ids and reloads."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)
    registry = er.async_get(hass)

    before = _size_entities(hass, config_entry)
    assert set(before) == set(METRICS)
    for metric, reg in before.items():
        assert reg.unique_id == f"{PREFIX}_{metric}_gb"
    status_id = registry.async_get_entity_id("sensor", DOMAIN, PREFIX)
    calls_before = aioclient_mock.call_count

    hass.config_entries.async_update_entry(config_entry, options={CONF_UNIT: "TB"})
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    # Reload fetched the API again
    assert aioclient_mock.call_count > calls_before
    assert hass.data[DOMAIN][config_entry.entry_id][CONF_UNIT] == "TB"
    assert registry.async_get_entity_id("sensor", DOMAIN, PREFIX) == status_id

    after = _size_entities(hass, config_entry)
    for metric, reg in before.items():
        updated = after[metric]
        assert updated.entity_id == reg.entity_id
        assert updated.unique_id == f"{PREFIX}_{metric}_tb"
        assert (
            updated.options["sensor.private"]["suggested_unit_of_measurement"] == "TB"
        )

    state = hass.states.get(before["total"].entity_id)
    assert state is not None
    assert state.attributes["unit_of_measurement"] == "TB"
    assert float(state.state) == 1.0
    state = hass.states.get(before["used"].entity_id)
    assert state is not None
    assert float(state.state) == 0.3
    # No duplicate entities were created
    assert len(er.async_entries_for_config_entry(registry, config_entry.entry_id)) == 8


async def test_options_change_user_unit_override(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """A unit change clears a user unit override but keeps other sensor options."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)
    registry = er.async_get(hass)
    entity_id = _size_entities(hass, config_entry)["total"].entity_id

    registry.async_update_entity_options(
        entity_id, "sensor", {"unit_of_measurement": "MB", "display_precision": 1}
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["unit_of_measurement"] == "MB"

    hass.config_entries.async_update_entry(config_entry, options={CONF_UNIT: "TB"})
    await hass.async_block_till_done()

    reg = registry.async_get(entity_id)
    assert reg is not None
    assert reg.unique_id == f"{PREFIX}_total_tb"
    assert "unit_of_measurement" not in reg.options["sensor"]
    assert reg.options["sensor"]["display_precision"] == 1
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["unit_of_measurement"] == "TB"
    assert state.state == "1.0"


async def test_options_change_without_user_override(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
) -> None:
    """Without a user unit override the "sensor" options are left untouched."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)
    registry = er.async_get(hass)
    entity_id = _size_entities(hass, config_entry)["total"].entity_id
    before = registry.async_get(entity_id)
    assert before is not None
    sensor_options = before.options.get("sensor")

    hass.config_entries.async_update_entry(config_entry, options={CONF_UNIT: "TB"})
    await hass.async_block_till_done()

    reg = registry.async_get(entity_id)
    assert reg is not None
    assert reg.options.get("sensor") == sensor_options


async def test_options_change_renames_legacy_ids_and_removes_conflicts(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    storage_box: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legacy ids are renamed; stale entities holding a target id are removed."""
    _mock_box(aioclient_mock, storage_box)
    await _setup(hass, config_entry)
    registry = er.async_get(hass)
    other = f"{DOMAIN}_storage_box_999"

    # Legacy id that can be renamed
    legacy_ok = _register(hass, f"{other}_used", config_entry)
    # Legacy id whose target is taken by a stale entity of the same entry
    legacy_conflict = _register(hass, f"{other}_total", config_entry)
    taken_legacy = _register(hass, f"{other}_total_tb", config_entry)
    # Current-format id whose target is taken by an unrelated entity
    taken_current = _register(hass, f"{PREFIX}_free_tb", None)
    free_id = _size_entities(hass, config_entry)["free"].entity_id
    # Non-size and foreign ids are ignored
    location = _register(hass, f"{other}_location", config_entry)
    foreign = _register(hass, "foreign_unique_id", config_entry)

    hass.config_entries.async_update_entry(config_entry, options={CONF_UNIT: "TB"})
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED

    def uid(entity_id: str) -> str | None:
        reg = registry.async_get(entity_id)
        return None if reg is None else reg.unique_id

    assert uid(legacy_ok.entity_id) == f"{other}_used_tb"
    assert uid(legacy_conflict.entity_id) == f"{other}_total_tb"
    assert uid(taken_legacy.entity_id) is None
    assert uid(free_id) == f"{PREFIX}_free_tb"
    assert uid(taken_current.entity_id) is None
    assert uid(location.entity_id) == f"{other}_location"
    assert uid(foreign.entity_id) == "foreign_unique_id"
    assert f"Removing conflicting stale entity {taken_legacy.entity_id}" in caplog.text
    assert f"Removing conflicting stale entity {taken_current.entity_id}" in caplog.text

    for entity_id in (legacy_ok.entity_id, legacy_conflict.entity_id, free_id):
        reg = registry.async_get(entity_id)
        assert reg is not None
        assert reg.options["sensor.private"]["suggested_unit_of_measurement"] == "TB"

    # The renamed entity keeps its entity_id and receives the platform's state
    state = hass.states.get(free_id)
    assert state is not None
    assert state.state == "0.7"
    assert state.attributes["unit_of_measurement"] == "TB"


async def test_safe_update_unique_id_same_id_noop(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Renaming to the same unique_id does nothing."""
    registry = er.async_get(hass)
    reg = _register(hass, f"{PREFIX}_total_gb", config_entry)

    with patch.object(registry, "async_update_entity") as update:
        await _safe_update_unique_id(hass, registry, reg, f"{PREFIX}_total_gb")

    update.assert_not_called()


async def test_safe_update_unique_id_value_error_logged(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A ValueError while renaming is logged, not raised."""
    registry = er.async_get(hass)
    reg = _register(hass, f"{PREFIX}_total", config_entry)

    with patch.object(registry, "async_update_entity", side_effect=ValueError("boom")):
        await _safe_update_unique_id(hass, registry, reg, f"{PREFIX}_total_gb")

    assert "Could not update unique_id" in caplog.text
    assert "boom" in caplog.text
    updated = registry.async_get(reg.entity_id)
    assert updated is not None
    assert updated.unique_id == f"{PREFIX}_total"
