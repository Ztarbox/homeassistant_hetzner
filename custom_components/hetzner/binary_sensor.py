from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_STORAGE_BOX_ID, DOMAIN
from .coordinator import HetznerConfigEntry, HetznerDataUpdateCoordinator
from .entity import HetznerEntity

# Entities only read coordinator data and never call the API themselves
PARALLEL_UPDATES = 0

# One binary sensor per boolean of access_settings in the API; the key is the
# API field and the end of the unique_id
ACCESS_SETTINGS: tuple[BinarySensorEntityDescription, ...] = tuple(
    BinarySensorEntityDescription(
        key=key,
        translation_key=translation_key,
        entity_category=EntityCategory.DIAGNOSTIC,
    )
    for key, translation_key in (
        ("reachable_externally", "reachable_externally"),
        ("samba_enabled", "samba"),
        ("ssh_enabled", "ssh"),
        ("webdav_enabled", "webdav"),
        ("zfs_enabled", "zfs_snapshot_folder"),
    )
)

# English entity names by translation key, the base of new entity_ids in every
# language; must match translations/en.json (checked by the tests)
OBJECT_ID_NAMES = {
    "reachable_externally": "Reachable externally",
    "samba": "Samba",
    "ssh": "SSH",
    "webdav": "WebDAV",
    "zfs_snapshot_folder": "ZFS snapshot folder",
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: HetznerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the access setting binary sensors of a storage box."""
    # Presence checked in __init__.async_setup_entry
    storage_box_id = config_entry.data[CONF_STORAGE_BOX_ID]
    # Already refreshed in __init__.async_setup_entry
    coordinator = config_entry.runtime_data

    async_add_entities(
        HetznerAccessSettingBinarySensor(coordinator, storage_box_id, description)
        for description in ACCESS_SETTINGS
    )


class HetznerAccessSettingBinarySensor(HetznerEntity, BinarySensorEntity):
    """Whether one access method of the storage box is enabled."""

    def __init__(
        self,
        coordinator: HetznerDataUpdateCoordinator,
        storage_box_id: str,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, storage_box_id)
        self.entity_description = description
        self._attr_unique_id = (
            f"{DOMAIN}_storage_box_{self._storage_box_id}_{description.key}"
        )
        self._object_id_name = OBJECT_ID_NAMES[str(description.translation_key)]

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        access = data.get("access_settings") or {}
        # Unknown rather than off when the API leaves a setting out
        value = access.get(self.entity_description.key)
        return None if value is None else bool(value)
