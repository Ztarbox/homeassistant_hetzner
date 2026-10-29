from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING, Any, TypedDict

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    HetznerApiError,
    HetznerAuthError,
    HetznerClient,
    HetznerConnectionError,
    HetznerNotFoundError,
    HetznerTimeoutError,
)
from .const import CONF_STORAGE_BOX_ID, CONFIG_ENTRY_VERSION, DEFAULT_HOST, DOMAIN

if TYPE_CHECKING:
    import probatio as vol
else:
    # Recent Home Assistant versions install probatio under this name; importing
    # it as voluptuous keeps older Home Assistant versions working
    import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})


class StorageBoxInfo(TypedDict):
    """Fields of a storage box shown in the selection dropdown."""

    name: str | None
    status: str | None
    box_type: str | None
    description: str | None


def _error_key(err: Exception, storage_box_id: str | None = None) -> str:
    """Map an API error to a form error key.

    Expected failures are shown in the form, so they are only logged at
    debug level.
    """
    if isinstance(err, HetznerAuthError):
        _LOGGER.debug("Hetzner API rejected the API key: %s", err)
        return "invalid_api_key"
    if isinstance(err, HetznerNotFoundError):
        if storage_box_id is not None:
            _LOGGER.debug("Storage box %s not found", storage_box_id)
            return "storage_box_not_found"
        _LOGGER.debug("Hetzner API endpoint not found")
        return "invalid_host"
    if isinstance(err, HetznerTimeoutError):
        _LOGGER.debug("Timeout connecting to Hetzner API")
        return "connection_timeout"
    if isinstance(err, HetznerConnectionError):
        _LOGGER.debug("Error connecting to Hetzner API: %s", err)
        return "validation_error"
    if isinstance(err, HetznerApiError):
        _LOGGER.debug("Unexpected response from Hetzner API: %s", err)
        return "api_error"
    # Unexpected, so keep the traceback visible
    _LOGGER.error("Unexpected error calling the Hetzner API", exc_info=err)
    return "validation_error"


# Field key used for storage box selection; using the config constant
# allows Home Assistant to look up a translation for this key under
# `config.step.storage_box.data.storage_box_id`.
STORAGE_BOX_SELECT_FIELD = CONF_STORAGE_BOX_ID


def create_storage_box_schema(
    box_options: list[selector.SelectOptionDict],
) -> vol.Schema:
    """Create schema with available storage boxes as options using selector.

    Args:
        box_options: Storage box IDs as values with formatted descriptions as labels
    """
    return vol.Schema(
        {
            vol.Required(STORAGE_BOX_SELECT_FIELD): selector.SelectSelector(
                selector.SelectSelectorConfig(options=box_options)
            )
        }
    )


class HetznerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hetzner Storage Box."""

    VERSION = CONFIG_ENTRY_VERSION

    data: dict[str, Any]

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - Ask for API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY, "").strip()
            host = DEFAULT_HOST

            # Validate the API key is not empty
            if not api_key:
                errors[CONF_API_KEY] = "api_key_required"
            else:
                # Attempt to validate the API key by testing connection
                validation_error = await self._validate_api_key(api_key, host)
                if validation_error:
                    errors[CONF_API_KEY] = validation_error
                else:
                    # API key is valid, proceed to next step
                    self.data = {
                        CONF_API_KEY: api_key,
                        CONF_HOST: host,
                    }
                    return await self.async_step_storage_box()

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the API rejected the key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key and check it can still see the storage box."""
        errors: dict[str, str] = {}

        if user_input is not None:
            reauth_entry = self._get_reauth_entry()
            api_key = user_input.get(CONF_API_KEY, "").strip()
            host = reauth_entry.data.get(CONF_HOST, DEFAULT_HOST)

            if not api_key:
                errors[CONF_API_KEY] = "api_key_required"
            # Query the box itself: a key of another project is valid as well
            elif validation_error := await self._validate_api_key(
                api_key, host, reauth_entry.data[CONF_STORAGE_BOX_ID]
            ):
                errors[CONF_API_KEY] = validation_error
            else:
                # Reloads a loaded entry and sets up one that failed with the
                # rejected key
                return self.async_update_reload_and_abort(
                    reauth_entry, data_updates={CONF_API_KEY: api_key}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )

    def _client(self, api_key: str, host: str) -> HetznerClient:
        """Return an API client using Home Assistant's shared session."""
        return HetznerClient(async_get_clientsession(self.hass), api_key, host)

    async def _validate_api_key(
        self, api_key: str, host: str, storage_box_id: str | None = None
    ) -> str | None:
        """Validate API key by testing connection to Hetzner API.

        With a storage box id the key must also have access to that box.
        Returns None if valid, or error string if invalid.
        """
        client = self._client(api_key, host)
        try:
            if storage_box_id is None:
                await client.async_list_storage_boxes()
            else:
                await client.async_get_storage_box(storage_box_id)
        except Exception as err:  # noqa: BLE001
            return _error_key(err, storage_box_id)
        _LOGGER.debug("API key validated successfully")
        return None

    def _build_box_options(
        self, storage_boxes: dict[str, StorageBoxInfo]
    ) -> list[selector.SelectOptionDict]:
        """Build dropdown options for the storage boxes.

        Args:
            storage_boxes: Dict mapping box ID to box info

        Returns:
            Options with the box ID as value and a formatted description as label
        """
        return [
            selector.SelectOptionDict(
                value=box_id, label=self._box_label(box_id, box_info)
            )
            for box_id, box_info in storage_boxes.items()
        ]

    @staticmethod
    def _box_label(box_id: str, box_info: StorageBoxInfo) -> str:
        """Format a dropdown label, leaving out fields the API did not return."""
        details = [f"ID: {box_id}"]
        if box_info["status"]:
            details.append(f"Status: {box_info['status']}")
        type_info = ", ".join(
            part for part in (box_info["box_type"], box_info["description"]) if part
        )
        if type_info:
            details.append(f"Type: {type_info}")

        if box_info["name"]:
            return f"{box_info['name']} ({', '.join(details)})"
        return ", ".join(details)

    async def _fetch_storage_boxes(
        self, api_key: str, host: str
    ) -> dict[str, StorageBoxInfo]:
        """Fetch list of available storage boxes from Hetzner API.

        Returns a dict mapping storage box ID (as string) to its name, status,
        type name and type description. API errors are raised.
        """
        storage_boxes: dict[str, StorageBoxInfo] = {}
        for box in await self._client(api_key, host).async_list_storage_boxes():
            box_type = box.get("storage_box_type") or {}
            storage_boxes[str(box.get("id"))] = {
                "name": box.get("name"),
                "status": box.get("status"),
                "box_type": box_type.get("name"),
                "description": box_type.get("description"),
            }
        return storage_boxes

    async def async_step_storage_box(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the storage box selection step."""
        errors: dict[str, str] = {}

        # Fetched on every submit, so the form without boxes retries
        try:
            storage_boxes = await self._fetch_storage_boxes(
                self.data[CONF_API_KEY], self.data[CONF_HOST]
            )
        except Exception as err:  # noqa: BLE001
            # Not "no_storage_boxes": the key may well have boxes
            errors["base"] = _error_key(err)
            storage_boxes = {}
        else:
            if not storage_boxes:
                errors["base"] = "no_storage_boxes"

        if not storage_boxes:
            return self.async_show_form(
                step_id="storage_box",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        # Empty when the form without boxes is submitted to fetch them again
        if user_input:
            # The dropdown submits the box ID, which stays stable while the
            # box's status or name may change between showing and submitting
            selected_box_id = user_input.get(STORAGE_BOX_SELECT_FIELD, "")
            selected_box = storage_boxes.get(selected_box_id)

            if selected_box:
                # Set unique ID for the config entry (storage box id) to allow single instance per box
                await self.async_set_unique_id(selected_box_id)
                self._abort_if_unique_id_configured()
                # Create config entry with selected storage box
                return self.async_create_entry(
                    title=f"Hetzner {selected_box['name'] or selected_box_id}",
                    data={
                        CONF_API_KEY: self.data[CONF_API_KEY],
                        CONF_HOST: self.data[CONF_HOST],
                        CONF_STORAGE_BOX_ID: selected_box_id,
                    },
                )
            else:
                errors["base"] = "invalid_selection"

        return self.async_show_form(
            step_id="storage_box",
            data_schema=create_storage_box_schema(
                self._build_box_options(storage_boxes)
            ),
            errors=errors,
        )
