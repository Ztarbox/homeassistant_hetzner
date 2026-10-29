import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_STORAGE_BOX_ID,
    DOMAIN,
    SIZE_METRICS,
    SIZE_SUGGESTED_UNIT,
    STORAGE_BOX_STATUSES,
)
from .coordinator import HetznerConfigEntry, HetznerDataUpdateCoordinator
from .entity import HetznerEntity

_LOGGER = logging.getLogger(__name__)

# Entities only read coordinator data and never call the API themselves
PARALLEL_UPDATES = 0

# Translation keys of the size sensors, which name the entities
SIZE_TRANSLATION_KEYS = {
    "total": "total_size",
    "used": "total_used",
    "data": "data_size",
    "snapshots": "snapshot_size",
    "free": "free_space",
}

# English entity names by translation key, the base of new entity_ids in every
# language; must match translations/en.json (checked by the tests)
OBJECT_ID_NAMES = {
    "location": "Location",
    "total_size": "Total size",
    "total_used": "Total used",
    "data_size": "Data size",
    "snapshot_size": "Snapshot size",
    "free_space": "Free space",
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: HetznerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for Hetzner Storage Box using the entry's coordinator."""
    # Presence checked in __init__.async_setup_entry
    storage_box_id = config_entry.data[CONF_STORAGE_BOX_ID]
    # Already refreshed in __init__.async_setup_entry
    coordinator = config_entry.runtime_data

    entities: list[SensorEntity] = [
        HetznerStatusSensor(coordinator, storage_box_id),
        HetznerLocationSensor(coordinator, storage_box_id),
    ]
    entities.extend(
        HetznerSizeSensor(coordinator, storage_box_id, metric)
        for metric in SIZE_METRICS
    )

    # No update_before_add: the coordinator already holds fresh data
    async_add_entities(entities)


class HetznerStatusSensor(HetznerEntity, SensorEntity):
    """Sensor representing the storage box status and details."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STORAGE_BOX_STATUSES
    # Translates the states and picks the icon; the name stays the device's
    _attr_translation_key = "status"

    def __init__(
        self, coordinator: HetznerDataUpdateCoordinator, storage_box_id: str
    ) -> None:
        super().__init__(coordinator, storage_box_id)
        self._attr_unique_id = f"{DOMAIN}_storage_box_{self._storage_box_id}"
        # Main feature of the device: entity takes the device name
        self._attr_name = None
        self._logged_unknown_status: str | None = None

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data or {}
        status = data.get("status")
        if status is None or status in STORAGE_BOX_STATUSES:
            return status
        # An ENUM sensor must not report a value outside its options, so a
        # status added to the API later shows as unknown instead of failing
        if status != self._logged_unknown_status:
            _LOGGER.warning(
                "Unknown status %r of storage box %s", status, self._storage_box_id
            )
            self._logged_unknown_status = status
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        s_type = data.get("storage_box_type") or {}
        return {
            "id": data.get("id"),
            "name": data.get("name"),
            "username": data.get("username"),
            "server": data.get("server"),
            "system": data.get("system"),
            "storage_box_type": s_type.get("name"),
            "created": data.get("created"),
        }


class HetznerLocationSensor(HetznerEntity, SensorEntity):
    """Sensor representing the storage box location."""

    _attr_translation_key = "location"
    _object_id_name = OBJECT_ID_NAMES["location"]

    def __init__(
        self, coordinator: HetznerDataUpdateCoordinator, storage_box_id: str
    ) -> None:
        super().__init__(coordinator, storage_box_id)
        self._attr_unique_id = f"{DOMAIN}_storage_box_{self._storage_box_id}_location"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data or {}
        location = data.get("location") or {}
        return location.get("name")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        location = data.get("location") or {}
        return {
            "id": location.get("id"),
            "country": location.get("country"),
            "city": location.get("city"),
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "description": location.get("description"),
        }


class HetznerSizeSensor(HetznerEntity, SensorEntity):
    """Sensor for various storage size metrics."""

    # The API reports bytes; HA converts them to the unit chosen in the entity
    # settings. New entities default to GiB: Hetzner sizes are binary, a BX11
    # ("1 TB") reports 2^40 bytes
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_suggested_unit_of_measurement = SIZE_SUGGESTED_UNIT
    _attr_suggested_display_precision = 2
    # Report as a measurement for long-term statistics
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DATA_SIZE

    def __init__(
        self,
        coordinator: HetznerDataUpdateCoordinator,
        storage_box_id: str,
        metric: str,
    ) -> None:
        super().__init__(coordinator, storage_box_id)

        if metric not in SIZE_METRICS:
            raise ValueError("Invalid metric for HetznerSizeSensor")

        self._metric = metric
        self._attr_translation_key = SIZE_TRANSLATION_KEYS[metric]
        self._object_id_name = OBJECT_ID_NAMES[self._attr_translation_key]
        self._attr_unique_id = (
            f"{DOMAIN}_storage_box_{self._storage_box_id}_{self._metric}"
        )

    @property
    def native_value(self) -> int:
        data = self.coordinator.data or {}
        stats = data.get("stats") or {}
        sb = data.get("storage_box_type") or {}

        total: int = sb.get("size") or 0
        used: int = stats.get("size") or 0
        data_size: int = stats.get("size_data") or 0
        snapshots: int = stats.get("size_snapshots") or 0

        if self._metric == "total":
            return total
        if self._metric == "used":
            return used
        if self._metric == "data":
            return data_size
        if self._metric == "snapshots":
            return snapshots
        # Free space, clamped to 0 to avoid negative values
        return max(0, total - data_size - snapshots)
