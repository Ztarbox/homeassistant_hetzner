import asyncio
import logging

from aiohttp import ClientTimeout
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

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

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): str,
        vol.Required(CONF_UNIT, default=DEFAULT_UNIT): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=UNIT_OPTIONS,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
    }
)

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


class HetznerOptionsFlow(config_entries.OptionsFlow):

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_unit = self.config_entry.options.get(CONF_UNIT, DEFAULT_UNIT)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_UNIT, default=current_unit
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=UNIT_OPTIONS,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )


class HetznerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hetzner Storage Box."""

    VERSION = 4

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HetznerOptionsFlow()

    async def async_step_user(self, user_input=None):
        """Handle the initial step - Ask for API key."""
        errors = {}

        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY, "").strip()
            host = DEFAULT_HOST
            unit = user_input.get(CONF_UNIT, DEFAULT_UNIT)

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
                        CONF_UNIT: unit,
                    }
                    return await self.async_step_storage_box()

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "api_key_info": "Your Hetzner Storage Box API key",
            },
        )

    async def async_step_reauth(self, entry_data):
        """Start reauthentication after the API rejected the key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Ask for a new API key and check it can still see the storage box."""
        errors = {}

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
            elif reauth_entry.state is config_entries.ConfigEntryState.LOADED:
                # The update listener reloads the entry on a new key
                return self.async_update_and_abort(
                    reauth_entry, data_updates={CONF_API_KEY: api_key}
                )
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry, data_updates={CONF_API_KEY: api_key}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )

    async def _validate_api_key(
        self, api_key: str, host: str, storage_box_id: str | None = None
    ) -> str | None:
        """Validate API key by testing connection to Hetzner API.

        With a storage box id the key must also have access to that box.
        Returns None if valid, or error string if invalid.
        """
        url = f"https://{host}/v1/storage_boxes"
        if storage_box_id is not None:
            url = f"{url}/{storage_box_id}"

        try:
            session = async_get_clientsession(self.hass)
            headers = {"Authorization": f"Bearer {api_key}"}

            # Attempt a test API call to validate the key against Hetzner API
            async with session.get(
                url,
                headers=headers,
                timeout=ClientTimeout(total=10),
            ) as resp:
                if resp.status in (401, 403):
                    _LOGGER.error("Invalid API key for Hetzner Storage Box")
                    return "invalid_api_key"
                elif resp.status == 200:
                    _LOGGER.debug("API key validated successfully")
                    return None
                elif resp.status == 404 and storage_box_id is not None:
                    _LOGGER.error("Storage box %s not found", storage_box_id)
                    return "storage_box_not_found"
                elif resp.status == 404:
                    _LOGGER.error("Hetzner API endpoint not found")
                    return "invalid_host"
                else:
                    _LOGGER.error(
                        f"Unexpected response from Hetzner API: {resp.status}"
                    )
                    return "api_error"
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout connecting to Hetzner API")
            return "connection_timeout"
        except Exception as e:
            _LOGGER.error(f"Error validating API key: {e}")
            return "validation_error"

    def _build_box_options(
        self, storage_boxes: dict
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
    def _box_label(box_id: str, box_info: dict) -> str:
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

    async def _fetch_storage_boxes(self, api_key: str, host: str) -> dict:
        """Fetch list of available storage boxes from Hetzner API.

        Returns a dict mapping storage box ID (as string) to info dict with:
        - name: storage box name
        - status: storage box status
        - box_type: storage box type name
        - description: storage box type description
        """
        try:
            session = async_get_clientsession(self.hass)
            headers = {"Authorization": f"Bearer {api_key}"}

            async with session.get(
                f"https://{host}/v1/storage_boxes",
                headers=headers,
                timeout=ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.error("Error fetching storage boxes: %s", resp.status)
                    return {}

                data = await resp.json()
                storage_boxes = {}

                for box in data.get("storage_boxes", []):
                    box_id = str(box.get("id"))
                    box_type = box.get("storage_box_type") or {}
                    storage_boxes[box_id] = {
                        "name": box.get("name"),
                        "status": box.get("status"),
                        "box_type": box_type.get("name"),
                        "description": box_type.get("description"),
                    }

                return storage_boxes
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout fetching storage boxes")
            return {}
        except Exception as e:
            _LOGGER.error("Error fetching storage boxes: %s", e)
            return {}

    async def async_step_storage_box(self, user_input=None):
        """Handle the storage box selection step."""
        errors = {}

        # Fetch available storage boxes from API
        storage_boxes = await self._fetch_storage_boxes(
            self.data[CONF_API_KEY], self.data[CONF_HOST]
        )

        if not storage_boxes:
            errors["base"] = "no_storage_boxes"
            return self.async_show_form(
                step_id="storage_box",
                data_schema=vol.Schema({}),
                errors=errors,
                description_placeholders={
                    "storage_box_info": "No storage boxes found for this account",
                },
            )

        if user_input is not None:
            # The dropdown submits the box ID, which stays stable while the
            # box's status or name may change between showing and submitting
            selected_box_id = user_input.get(STORAGE_BOX_SELECT_FIELD)
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
                    # Display setting only, changed later in the options flow
                    options={CONF_UNIT: self.data[CONF_UNIT]},
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
