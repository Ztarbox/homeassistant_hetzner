from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    HetznerConfigEntry,
    HetznerDataUpdateCoordinator,
    subaccount_device_identifier,
)

CONFIGURATION_URL = "https://console.hetzner.com"


def storage_box_device_info(storage_box_id: str, box: dict[str, Any]) -> DeviceInfo:
    """Return the device info of the storage box device."""
    box_name = box.get("name") or storage_box_id
    return DeviceInfo(
        identifiers={(DOMAIN, str(storage_box_id))},
        name=f"Storage Box {box_name}",
        manufacturer="Hetzner",
        model=(box.get("storage_box_type") or {}).get("name"),
        configuration_url=CONFIGURATION_URL,
    )


@callback
def async_add_subaccount_entities(
    config_entry: HetznerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    entity_factory: Callable[[str], Iterable[Entity]],
) -> None:
    """Add the entities of every subaccount, now and when new ones appear.

    entity_factory returns the entities of one subaccount id.
    """
    coordinator = config_entry.runtime_data
    known: set[str] = set()

    @callback
    def _async_add_new_subaccounts() -> None:
        current = set(coordinator.data.subaccounts)
        # Removed subaccounts lost their device and entities in the coordinator
        known.intersection_update(current)
        if new := current - known:
            known.update(new)
            async_add_entities(
                entity
                for subaccount_id in sorted(new)
                for entity in entity_factory(subaccount_id)
            )

    _async_add_new_subaccounts()
    config_entry.async_on_unload(
        coordinator.async_add_listener(_async_add_new_subaccounts)
    )


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
    def _box(self) -> dict[str, Any]:
        """Return the storage box of the last update, empty without data."""
        data = self.coordinator.data
        return data.box if data is not None else {}

    @property
    def suggested_object_id(self) -> str | None:
        """Return the English name to base new entity_ids on.

        HA would derive them from the translated name in languages with
        native entity_ids (e.g. de, nl); keep them English as documented.
        """
        return self._object_id_name

    @property
    def device_info(self) -> DeviceInfo:
        return storage_box_device_info(self._storage_box_id, self._box)


class HetznerSubaccountEntity(HetznerEntity):
    """Base entity grouping the entities of a subaccount under its own device."""

    def __init__(
        self,
        coordinator: HetznerDataUpdateCoordinator,
        storage_box_id: str,
        subaccount_id: str,
        key: str,
    ) -> None:
        super().__init__(coordinator, storage_box_id)
        self._subaccount_id = subaccount_id
        self._attr_unique_id = (
            f"{DOMAIN}_storage_box_{storage_box_id}_subaccount_{subaccount_id}_{key}"
        )

    @property
    def _subaccount(self) -> dict[str, Any]:
        """Return the subaccount of the last update, empty if it is gone."""
        data = self.coordinator.data
        if data is None:
            return {}
        return data.subaccounts.get(self._subaccount_id) or {}

    @property
    def available(self) -> bool:
        """Unavailable once the API no longer lists the subaccount.

        Its device and entities are removed by the coordinator right after.
        """
        data = self.coordinator.data
        return (
            super().available
            and data is not None
            and self._subaccount_id in data.subaccounts
        )

    @property
    def device_info(self) -> DeviceInfo:
        subaccount = self._subaccount
        box_name = self._box.get("name") or self._storage_box_id
        # The name is required by the API; fall back to the username anyway.
        # The box name keeps entity_ids readable and apart from the box's own
        # (binary_sensor.storage_box_backup_sub1_ssh vs ..._backup_ssh) and
        # from equally named subaccounts of other boxes
        name = (
            subaccount.get("name") or subaccount.get("username") or self._subaccount_id
        )
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    subaccount_device_identifier(
                        self._storage_box_id, self._subaccount_id
                    ),
                )
            },
            name=f"Storage Box {box_name} {name}",
            manufacturer="Hetzner",
            model="Storage Box subaccount",
            configuration_url=CONFIGURATION_URL,
            # Registered in __init__.async_setup_entry before the platforms
            via_device_id=dr.async_get_device_id_by_identifier(
                self.coordinator.hass,
                (DOMAIN, str(self._storage_box_id)),
                config_entry_id=self.coordinator.config_entry.entry_id,
            ),
        )
