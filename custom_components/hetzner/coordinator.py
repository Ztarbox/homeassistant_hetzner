from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    HetznerApiError,
    HetznerAuthError,
    HetznerClient,
    HetznerConnectionError,
    HetznerNotFoundError,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=600)  # update every 10 minutes

# The coordinator is the only runtime data of a loaded entry
type HetznerConfigEntry = ConfigEntry[HetznerDataUpdateCoordinator]


@dataclass(frozen=True, slots=True)
class StorageBoxData:
    """Result of one update: the storage box and its subaccounts."""

    box: dict[str, Any]
    # Keyed by the subaccount id as str, the form used in device identifiers
    # and unique_ids
    subaccounts: dict[str, dict[str, Any]] = field(default_factory=dict)


def storage_box_not_found_issue_id(entry_id: str) -> str:
    """Return the repair issue id for a storage box the API cannot find."""
    return f"storage_box_not_found_{entry_id}"


def subaccount_device_identifier(storage_box_id: str, subaccount_id: str) -> str:
    """Return the device identifier of a subaccount of a storage box."""
    return f"{storage_box_id}_subaccount_{subaccount_id}"


def subaccount_ids_of_device(
    identifiers: Iterable[tuple[str, str]], storage_box_id: str
) -> set[str]:
    """Return the ids of the box's subaccounts a device stands for.

    Empty for the storage box device itself and for foreign devices.
    """
    prefix = subaccount_device_identifier(storage_box_id, "")
    return {
        identifier[len(prefix) :]
        for domain, identifier in identifiers
        if domain == DOMAIN and identifier.startswith(prefix)
    }


class HetznerDataUpdateCoordinator(DataUpdateCoordinator[StorageBoxData]):
    """Fetch storage box info, stats and subaccounts for one config entry."""

    config_entry: HetznerConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: HetznerConfigEntry,
        api_key: str,
        host: str,
        storage_box_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{storage_box_id}",
            update_interval=SCAN_INTERVAL,
        )
        self._client = HetznerClient(async_get_clientsession(hass), api_key, host)
        self._storage_box_id = storage_box_id

    async def _async_update_data(self) -> StorageBoxData:
        issue_id = storage_box_not_found_issue_id(self.config_entry.entry_id)
        try:
            storage_box = await self._client.async_get_storage_box(self._storage_box_id)
            # Two requests per update, far below the API's 3600 requests/hour
            subaccounts = await self._client.async_list_subaccounts(
                self._storage_box_id
            )
        except HetznerAuthError as err:
            # Starts reauth and stops polling: a rejected token will not recover.
            # The reauth notice replaces a not found issue that says HA retries
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_api_key",
                translation_placeholders={"error": str(err)},
            ) from err
        except HetznerNotFoundError as err:
            # A 404 may also mean a token of another project, so keep retrying
            # instead of failing the entry for good and tell the user instead.
            # The subaccount list lives below the box and is empty for a box
            # without subaccounts, so a 404 there means the box itself is gone
            # (deleted between the two requests) and is handled the same way
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="storage_box_not_found",
                translation_placeholders={"storage_box_id": self._storage_box_id},
            )
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="storage_box_not_found",
                translation_placeholders={"storage_box_id": self._storage_box_id},
            ) from err
        except HetznerApiError as err:
            # Includes maintenance (403), rate limits (429) and server errors
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={"error": str(err)},
            ) from err
        except HetznerConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

        ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        data = StorageBoxData(
            box=storage_box,
            subaccounts={
                str(subaccount["id"]): subaccount
                for subaccount in subaccounts
                # Without an id there is nothing to identify it by
                if isinstance(subaccount, dict) and subaccount.get("id") is not None
            },
        )
        self._async_remove_stale_devices(data.subaccounts)
        return data

    @callback
    def _async_remove_stale_devices(self, subaccounts: Iterable[str]) -> None:
        """Remove the devices of subaccounts the API no longer lists.

        Removing a device also removes its entities from the entity registry.
        Only a successful update gets here, so an API error never removes one.
        """
        current = set(subaccounts)
        device_registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(
            device_registry, self.config_entry.entry_id
        ):
            subaccount_ids = subaccount_ids_of_device(
                device.identifiers, self._storage_box_id
            )
            if subaccount_ids and not subaccount_ids & current:
                _LOGGER.debug("Removing device %s of a deleted subaccount", device.name)
                device_registry.async_remove_device(device.id)
