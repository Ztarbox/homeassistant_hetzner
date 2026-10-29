from __future__ import annotations

import logging

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_STORAGE_BOX_ID,
    CONF_UNIT,
    DEFAULT_HOST,
    DEFAULT_UNIT,
    DOMAIN,
    UNIT_OPTIONS,
)
from .coordinator import HetznerConfigEntry, HetznerData, HetznerDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]
SIZE_METRICS = {"total", "used", "data", "snapshots", "free"}
# Unit suffixes of size sensor unique_ids; only units the flows offer
VALID_UNITS = {unit.lower() for unit in UNIT_OPTIONS}


def _update_sensor_unit_options(
    entity_registry: er.EntityRegistry,
    entity_id: str,
    unit: str,
) -> None:
    """Update stored sensor unit options in the entity registry."""
    # This is the option SensorEntity actually reads back on startup
    registry_entry = entity_registry.async_update_entity_options(
        entity_id,
        f"{SENSOR_DOMAIN}.private",
        {"suggested_unit_of_measurement": unit},
    )

    # Clear an explicit user unit override, keeping other sensor options
    sensor_options = dict(registry_entry.options.get(SENSOR_DOMAIN, {}))
    if sensor_options.pop("unit_of_measurement", None) is not None:
        entity_registry.async_update_entity_options(
            entity_id,
            SENSOR_DOMAIN,
            sensor_options,
        )


async def async_setup_entry(
    hass: HomeAssistant, config_entry: HetznerConfigEntry
) -> bool:
    """Set up Hetzner from a config entry."""
    storage_box_id = config_entry.data.get(CONF_STORAGE_BOX_ID)
    if storage_box_id is None:
        # Shown as the reason in the UI; the entry has to be added again
        raise ConfigEntryError("No storage box id in config entry")

    unit = config_entry.options.get(CONF_UNIT, DEFAULT_UNIT)

    # Done on every setup, not only in the update listener: the unit can be
    # changed in the options flow while the entry waits for a setup retry,
    # when no listener is registered
    await _async_align_size_unique_ids(hass, config_entry, unit)

    coordinator = HetznerDataUpdateCoordinator(
        hass,
        config_entry,
        config_entry.data[CONF_API_KEY],
        config_entry.data.get(CONF_HOST, DEFAULT_HOST),
        storage_box_id,
    )
    # Refresh here rather than in the platform: HA only turns
    # ConfigEntryNotReady into a setup retry when raised from setup_entry
    await coordinator.async_config_entry_first_refresh()

    config_entry.runtime_data = HetznerData(
        coordinator=coordinator,
        unit=unit,
        api_key=config_entry.data[CONF_API_KEY],
    )

    config_entry.async_on_unload(
        config_entry.add_update_listener(_async_update_listener)
    )

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: HetznerConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old config entries and entity unique IDs."""
    entity_registry = er.async_get(hass)

    current_unit = config_entry.options.get(
        CONF_UNIT,
        config_entry.data.get(CONF_UNIT, DEFAULT_UNIT),
    ).lower()

    _LOGGER.debug(
        "Migrating config entry %s with version %s to version 4",
        config_entry.entry_id,
        config_entry.version,
    )

    if config_entry.version < 2:
        for entry in er.async_entries_for_config_entry(
            entity_registry, config_entry.entry_id
        ):
            unique_id = str(entry.unique_id)

            if not unique_id.startswith(f"{DOMAIN}_storage_box_"):
                continue

            parts = unique_id.split("_")

            # Legacy format: hetzner_storage_box_<id>_<metric>
            if len(parts) >= 5 and parts[-1] in SIZE_METRICS:
                new_unique_id = f"{unique_id}_{current_unit}"
                await _safe_update_unique_id(
                    hass=hass,
                    entity_registry=entity_registry,
                    entity_entry=entry,
                    new_unique_id=new_unique_id,
                )

    # Version 2 entries already use unit-suffixed unique_ids. Up to version 3
    # the unit chosen during setup was stored in data; it is a display setting
    # and belongs in options
    if config_entry.version < 4:
        data = dict(config_entry.data)
        options = dict(config_entry.options)
        data_unit = data.pop(CONF_UNIT, None)
        if data_unit is not None:
            options.setdefault(CONF_UNIT, data_unit)
        hass.config_entries.async_update_entry(
            config_entry, data=data, options=options, version=4
        )

    return True


async def _async_update_listener(
    hass: HomeAssistant, config_entry: HetznerConfigEntry
) -> None:
    """Reload when the unit or API key changed.

    Setup renames the size sensor unique_ids to a new unit; a new API key
    comes from the reauth flow.
    """
    runtime_data = config_entry.runtime_data
    new_unit = config_entry.options.get(CONF_UNIT, DEFAULT_UNIT)

    # HA also calls the listener for title or system option changes
    if (
        new_unit.upper() == runtime_data.unit.upper()
        and config_entry.data[CONF_API_KEY] == runtime_data.api_key
    ):
        return

    await hass.config_entries.async_reload(config_entry.entry_id)


async def _async_align_size_unique_ids(
    hass: HomeAssistant, config_entry: HetznerConfigEntry, unit: str
) -> None:
    """Rename size sensor unique_ids to the unit and reset their unit options."""
    unit = unit.upper()
    entity_registry = er.async_get(hass)

    for entry in er.async_entries_for_config_entry(
        entity_registry, config_entry.entry_id
    ):
        unique_id = str(entry.unique_id)

        if not unique_id.startswith(f"{DOMAIN}_storage_box_"):
            continue

        # Removed as a stale conflict while renaming an earlier entry
        if entity_registry.async_get(entry.entity_id) is None:
            continue

        parts = unique_id.split("_")

        # Current format: hetzner_storage_box_<id>_<metric>_<unit>
        if len(parts) >= 6 and parts[-2] in SIZE_METRICS and parts[-1] in VALID_UNITS:
            new_unique_id = "_".join(parts[:-1] + [unit.lower()])
        # Legacy format: hetzner_storage_box_<id>_<metric>
        elif len(parts) >= 5 and parts[-1] in SIZE_METRICS:
            new_unique_id = f"{unique_id}_{unit.lower()}"
        else:
            continue

        # Unchanged unit: keep the options the user set on the entity
        if new_unique_id == unique_id:
            continue

        await _safe_update_unique_id(
            hass=hass,
            entity_registry=entity_registry,
            entity_entry=entry,
            new_unique_id=new_unique_id,
        )
        _update_sensor_unit_options(entity_registry, entry.entity_id, unit)


async def _safe_update_unique_id(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    entity_entry: er.RegistryEntry,
    new_unique_id: str,
) -> None:
    """Safely update a unique_id, removing conflicting stale entries if needed."""
    old_unique_id = str(entity_entry.unique_id)

    if old_unique_id == new_unique_id:
        return

    existing_entity_id = entity_registry.async_get_entity_id(
        "sensor",
        DOMAIN,
        new_unique_id,
    )

    if existing_entity_id and existing_entity_id != entity_entry.entity_id:
        _LOGGER.warning(
            "Removing conflicting stale entity %s before renaming %s -> %s",
            existing_entity_id,
            old_unique_id,
            new_unique_id,
        )
        entity_registry.async_remove(existing_entity_id)

    try:
        _LOGGER.debug(
            "Updating unique_id for %s: %s -> %s",
            entity_entry.entity_id,
            old_unique_id,
            new_unique_id,
        )
        entity_registry.async_update_entity(
            entity_entry.entity_id,
            new_unique_id=new_unique_id,
        )
    except ValueError as err:
        _LOGGER.error(
            "Could not update unique_id for %s: %s -> %s (%s)",
            entity_entry.entity_id,
            old_unique_id,
            new_unique_id,
            err,
        )
