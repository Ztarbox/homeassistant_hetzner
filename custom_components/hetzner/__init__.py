from __future__ import annotations

import logging

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)

from .const import (
    CONF_STORAGE_BOX_ID,
    CONFIG_ENTRY_VERSION,
    DEFAULT_HOST,
    DOMAIN,
    LEGACY_CONF_UNIT,
    LEGACY_DEFAULT_UNIT,
    LEGACY_UNITS,
    SIZE_METRICS,
    SIZE_SUGGESTED_UNIT,
)
from .coordinator import (
    HetznerConfigEntry,
    HetznerDataUpdateCoordinator,
    storage_box_not_found_issue_id,
    subaccount_ids_of_device,
)
from .entity import storage_box_device_info

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

# Registry option keys of the sensor component (homeassistant.components.sensor)
_SENSOR_UNIT_OPTION = "unit_of_measurement"
_SENSOR_PRIVATE_OPTIONS = f"{SENSOR_DOMAIN}.private"


async def async_setup_entry(
    hass: HomeAssistant, config_entry: HetznerConfigEntry
) -> bool:
    """Set up Hetzner from a config entry."""
    storage_box_id = config_entry.data.get(CONF_STORAGE_BOX_ID)
    if storage_box_id is None:
        # Shown as the reason in the UI; the entry has to be added again
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="missing_storage_box_id"
        )

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

    config_entry.runtime_data = coordinator

    # Subaccount devices link to the box device by its registry id, so it has
    # to exist before the platforms add their entities
    dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id,
        **storage_box_device_info(storage_box_id, coordinator.data.box),
    )

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: HetznerConfigEntry
) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS):
        return False
    # Nothing retries while unloaded or disabled; setup raises it again if the
    # box is still not found
    ir.async_delete_issue(
        hass, DOMAIN, storage_box_not_found_issue_id(config_entry.entry_id)
    )
    return True


async def async_remove_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Remove the repair issue of a deleted entry."""
    # Issues are not tied to config entries and would otherwise stay around
    ir.async_delete_issue(
        hass, DOMAIN, storage_box_not_found_issue_id(config_entry.entry_id)
    )


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: HetznerConfigEntry,
    device_entry: dr.AnyDeviceEntry,
) -> bool:
    """Allow deleting the device of a subaccount the API no longer lists.

    The storage box device stays as long as the entry exists. Subaccount
    devices are normally removed by the coordinator already.
    """
    storage_box_id = config_entry.data.get(CONF_STORAGE_BOX_ID)
    if storage_box_id is None:
        return False
    subaccount_ids = subaccount_ids_of_device(device_entry.identifiers, storage_box_id)
    if not subaccount_ids:
        return False
    # Not loaded (e.g. waiting for a setup retry): nothing tells whether the
    # subaccount still exists, but if it does, the next setup adds it again
    if not hasattr(config_entry, "runtime_data"):
        return True
    return not subaccount_ids & set(config_entry.runtime_data.data.subaccounts)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old config entries and entity unique IDs."""
    # Written by a newer version of the integration (downgrade): its data
    # layout is unknown, so leave the entry untouched and fail the migration
    if config_entry.version > CONFIG_ENTRY_VERSION:
        _LOGGER.error(
            "Cannot downgrade config entry %s from version %s to %s",
            config_entry.entry_id,
            config_entry.version,
            CONFIG_ENTRY_VERSION,
        )
        return False

    _LOGGER.debug(
        "Migrating config entry %s with version %s to version %s",
        config_entry.entry_id,
        config_entry.version,
        CONFIG_ENTRY_VERSION,
    )

    if config_entry.version < 5:
        data = dict(config_entry.data)
        options = dict(config_entry.options)
        # Up to version 3 the unit was stored in data, then in options, which
        # wins; the size sensors convert from bytes themselves since version 5
        data_unit = data.pop(LEGACY_CONF_UNIT, None)
        options_unit = options.pop(LEGACY_CONF_UNIT, None)
        old_unit = LEGACY_UNITS.get(
            str(options_unit or data_unit or LEGACY_DEFAULT_UNIT).lower(),
            LEGACY_DEFAULT_UNIT,
        )
        await _async_migrate_size_sensors(hass, config_entry, old_unit)
        _async_remove_access_options_sensor(hass, config_entry)
        hass.config_entries.async_update_entry(
            config_entry, data=data, options=options, version=5
        )

    return True


def _size_unique_id(unique_id: str) -> str | None:
    """Return the unit-less unique_id of a size sensor, or None for others.

    Accepts hetzner_storage_box_<id>_<metric> (v1 and v5) and
    hetzner_storage_box_<id>_<metric>_<unit> (v2 to v4). The part counts are
    exact, so subaccount unique_ids
    (hetzner_storage_box_<id>_subaccount_<sub_id>_<key>) never match, whatever
    their key ends with.
    """
    if not unique_id.startswith(f"{DOMAIN}_storage_box_"):
        return None
    parts = unique_id.split("_")
    if len(parts) == 5 and parts[-1] in SIZE_METRICS:
        return unique_id
    if len(parts) == 6 and parts[-2] in SIZE_METRICS and parts[-1] in LEGACY_UNITS:
        return "_".join(parts[:-1])
    return None


async def _async_migrate_size_sensors(
    hass: HomeAssistant, config_entry: ConfigEntry, old_unit: str
) -> None:
    """Drop the unit from size sensor unique_ids and keep the displayed unit.

    The native unit changes to bytes. Without a unit option HA could show
    them in bytes or GiB, changing state values that automations, templates
    and dashboards rely on and the unit statistics are shown in.
    """
    entity_registry = er.async_get(hass)
    size_entries = [
        entry
        for entry in er.async_entries_for_config_entry(
            entity_registry, config_entry.entry_id
        )
        if entry.domain == SENSOR_DOMAIN and _size_unique_id(entry.unique_id)
    ]
    # Leftovers of earlier unit changes map to the same new unique_id; process
    # the one of the current unit last, so it wins over stale ones
    size_entries.sort(
        key=lambda entry: entry.unique_id.endswith(f"_{old_unit.lower()}")
    )

    for entry in size_entries:
        # Removed as a stale conflict while renaming an earlier entry
        if entity_registry.async_get(entry.entity_id) is None:
            continue

        new_unique_id = _size_unique_id(entry.unique_id)
        assert new_unique_id is not None
        await _safe_update_unique_id(
            hass=hass,
            entity_registry=entity_registry,
            entity_entry=entry,
            new_unique_id=new_unique_id,
        )

        # A unit the user chose in the entity settings stays as it is
        sensor_options = dict(entry.options.get(SENSOR_DOMAIN, {}))
        if _SENSOR_UNIT_OPTION not in sensor_options:
            # A user option rather than sensor.private: HA rewrites that one
            # from the suggested unit when the unit system changes
            sensor_options[_SENSOR_UNIT_OPTION] = old_unit
            entity_registry.async_update_entity_options(
                entry.entity_id, SENSOR_DOMAIN, sensor_options
            )
        # What a new size sensor gets; the old code stored its unit there
        entity_registry.async_update_entity_options(
            entry.entity_id,
            _SENSOR_PRIVATE_OPTIONS,
            {"suggested_unit_of_measurement": SIZE_SUGGESTED_UNIT},
        )


def _async_remove_access_options_sensor(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> None:
    """Remove the access options sensor, replaced by binary sensors."""
    storage_box_id = config_entry.data.get(CONF_STORAGE_BOX_ID)
    if storage_box_id is None:
        return
    entity_registry = er.async_get(hass)
    if entity_id := entity_registry.async_get_entity_id(
        SENSOR_DOMAIN,
        DOMAIN,
        f"{DOMAIN}_storage_box_{storage_box_id}_access_options",
    ):
        entity_registry.async_remove(entity_id)


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
        SENSOR_DOMAIN,
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
