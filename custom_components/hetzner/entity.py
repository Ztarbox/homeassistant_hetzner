from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HetznerDataUpdateCoordinator


class HetznerEntity(CoordinatorEntity[HetznerDataUpdateCoordinator]):
    """Base entity grouping all entities under one storage box device."""

    _attr_has_entity_name = True
    # English name for new entity_ids; None means the device name alone
    _object_id_name: str | None = None

    def __init__(
        self, coordinator: HetznerDataUpdateCoordinator, storage_box_id: str
    ) -> None:
        super().__init__(coordinator)
        self._storage_box_id = storage_box_id

    @property
    def suggested_object_id(self) -> str | None:
        """Return the English name to base new entity_ids on.

        HA would derive them from the translated name in languages with
        native entity_ids (e.g. de, nl); keep them English as documented.
        """
        return self._object_id_name

    @property
    def device_info(self) -> DeviceInfo:
        data = self.coordinator.data or {}
        box_name = data.get("name") or self._storage_box_id
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._storage_box_id))},
            name=f"Storage Box {box_name}",
            manufacturer="Hetzner",
            model=(data.get("storage_box_type") or {}).get("name"),
            configuration_url="https://console.hetzner.com",
        )
