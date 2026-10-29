"""Shared fixtures for the Hetzner Storage Box tests."""

from __future__ import annotations

from collections.abc import Generator
import copy
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (  # type: ignore[import-untyped]
    MockConfigEntry,
)

from custom_components.hetzner.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_STORAGE_BOX_ID,
    CONF_UNIT,
    DEFAULT_HOST,
    DOMAIN,
)

API_KEY = "test-api-key"
STORAGE_BOX_ID = "123456"
LIST_URL = f"https://{DEFAULT_HOST}/v1/storage_boxes"
BOX_URL = f"{LIST_URL}/{STORAGE_BOX_ID}"

STORAGE_BOX: dict[str, Any] = {
    "id": int(STORAGE_BOX_ID),
    "name": "backup",
    "username": "u123456",
    "status": "active",
    "server": "u123456.your-storagebox.de",
    "system": "FSN1-BX123",
    "created": "2024-01-01T00:00:00+00:00",
    "storage_box_type": {
        "name": "bx11",
        "description": "BX11",
        "size": 1_000_000_000_000,
    },
    "location": {
        "id": 1,
        "name": "fsn1",
        "country": "DE",
        "city": "Falkenstein",
        "latitude": 50.47612,
        "longitude": 12.370071,
        "description": "Falkenstein DC Park 1",
    },
    "access_settings": {
        "reachable_externally": True,
        "samba_enabled": False,
        "ssh_enabled": True,
        "webdav_enabled": False,
        "zfs_enabled": True,
    },
    "stats": {
        "size": 300_000_000_000,
        "size_data": 250_000_000_000,
        "size_snapshots": 50_000_000_000,
    },
}


def pytest_configure(config: pytest.Config) -> None:
    """Map files to modules from the repo root when running ``--mypy``.

    ``custom_components`` has no ``__init__.py``, so without
    ``--explicit-package-bases`` mypy sees ``custom_components/hetzner/const.py``
    both as ``hetzner.const`` (from the path) and as
    ``custom_components.hetzner.const`` (from the test imports) and aborts.
    """
    plugin = config.pluginmanager.getplugin("mypy")
    if plugin is not None:
        plugin.mypy_argv.append("--explicit-package-bases")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Enable loading the custom integration in every test."""
    yield


@pytest.fixture
def storage_box() -> dict[str, Any]:
    """Return a fresh copy of a storage box API payload."""
    return copy.deepcopy(STORAGE_BOX)


@pytest.fixture
def config_entry(hass) -> MockConfigEntry:
    """Return a current-version config entry added to hass (not set up)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hetzner backup",
        version=3,
        unique_id=STORAGE_BOX_ID,
        data={
            CONF_API_KEY: API_KEY,
            CONF_HOST: DEFAULT_HOST,
            CONF_STORAGE_BOX_ID: STORAGE_BOX_ID,
            CONF_UNIT: "GB",
        },
    )
    entry.add_to_hass(hass)
    return entry
