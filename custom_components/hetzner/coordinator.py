from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from aiohttp import ClientTimeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=600)  # update every 10 minutes

type HetznerConfigEntry = ConfigEntry[HetznerData]


@dataclass
class HetznerData:
    """Runtime data of a loaded config entry."""

    coordinator: HetznerDataUpdateCoordinator
    unit: str


class HetznerDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch storage box info and stats for one config entry."""

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
        self._api_key = api_key
        self._host = host
        self._storage_box_id = storage_box_id

    async def _async_update_data(self) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = f"https://{self._host}/v1/storage_boxes/{self._storage_box_id}"

        async with session.get(
            url, headers=headers, timeout=ClientTimeout(total=10)
        ) as resp:
            if resp.status != 200:
                raise UpdateFailed(f"Error fetching storage box data: {resp.status}")
            data = await resp.json()
            return data.get("storage_box") or {}
