from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
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


def storage_box_not_found_issue_id(entry_id: str) -> str:
    """Return the repair issue id for a storage box the API cannot find."""
    return f"storage_box_not_found_{entry_id}"


class HetznerDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch storage box info and stats for one config entry."""

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

    async def _async_update_data(self) -> dict[str, Any]:
        issue_id = storage_box_not_found_issue_id(self.config_entry.entry_id)
        try:
            storage_box = await self._client.async_get_storage_box(self._storage_box_id)
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
            # instead of failing the entry for good and tell the user instead
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
        return storage_box
