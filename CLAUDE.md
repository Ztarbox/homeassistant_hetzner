# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

HACS-distributed Home Assistant custom integration (`custom_components/hetzner`, domain `hetzner`) that polls the Hetzner Storage Box JSON API (`https://api.hetzner.com/v1/storage_boxes/{id}`, Bearer token) and exposes sensors and binary sensors. There is no separate library; all code lives in `custom_components/hetzner/`. Version is in `manifest.json`.

## Commands

Use Python 3.14 in a virtualenv.

```bash
pip install -r requirements.dev.txt
pytest . --black --isort --flake8 --mypy   # full check, as documented in README (run before pushing)
pytest tests/test_api.py::test_get_storage_box # single test
```

CI (`.github/workflows/`) runs pytest, black, isort, flake8, mypy separately, plus hassfest and HACS validation. Style config (88 cols, isort `force_sort_within_sections`, flake8 ignores E501) is in `setup.cfg`. `tests/` is a full suite (pytest-homeassistant-custom-component, `aioclient_mock` for the API) that keeps every module at 100% coverage; check with `pytest --cov=custom_components.hetzner --cov-report=term-missing`. `tests/test_translations.py` checks that `strings.json` equals `translations/en.json`, that de/nl have the same keys and placeholders, and that every translation key used in the code exists.

## Architecture

- `api.py`: HA-agnostic `HetznerClient` (aiohttp session passed in, 10 s timeout) with `async_list_storage_boxes`/`async_get_storage_box`. Errors: `HetznerAuthError` (401, or 403 with `error.code` `forbidden` or no readable code), `HetznerNotFoundError` (404), `HetznerApiError` (everything else incl. 403 `maintenance`/`resource_limit_exceeded`, 429, 5xx, bad JSON; carries `status`/`code`), `HetznerConnectionError`/`HetznerTimeoutError`; all derive from `HetznerError`. Callers pass `async_get_clientsession(hass)`.
- `config_flow.py`: user step (API key → validate → fetch storage boxes → pick box) and a reauth step (new API key, checked with `GET /v1/storage_boxes/{id}`, then `async_update_reload_and_abort`, which reloads a loaded entry and sets up a failed one). No options flow; `entry.options` is empty. `CONF_API_KEY`/`CONF_HOST` come from `homeassistant.const`. `VERSION = CONFIG_ENTRY_VERSION` (5, in `const.py`). `_error_key` maps client errors to form errors on both steps; `no_storage_boxes` only means a successful, empty list. Schemas are typed against `probatio` (HA installs it as `voluptuous`) but imported as `voluptuous` at runtime for older HA versions.
- `__init__.py`: creates the coordinator and runs its first refresh (so API errors put the entry into setup retry; raising `ConfigEntryNotReady` from a forwarded platform would only be logged), stores the coordinator itself as `entry.runtime_data` (`HetznerConfigEntry = ConfigEntry[HetznerDataUpdateCoordinator]`) and forwards to the `binary_sensor` and `sensor` platforms. There is no update listener (nothing to react to; reauth reloads by itself).
- `coordinator.py`: `HetznerDataUpdateCoordinator`, one per config entry (10-minute poll), plus `HetznerConfigEntry`. `HetznerAuthError` raises `ConfigEntryAuthFailed` (starts reauth, polling stops). 404 means "not found — deleted, or not accessible with this API token" (the API cannot tell), so it creates a non-fixable repair issue `storage_box_not_found_<entry_id>` and raises `UpdateFailed` (setup retry / polling go on, waqi pattern); the issue is deleted after every successful fetch, on an auth error (reauth stops polling), in `async_unload_entry` and in `async_remove_entry`. Maintenance, rate limits, 5xx and connection errors raise `UpdateFailed` and never start reauth. All raised HA exceptions use `translation_domain`/`translation_key` (`exceptions` section of the translations).
- `entity.py`: `HetznerEntity` base class (coordinator entity, device info, `has_entity_name`, `suggested_object_id` from `_object_id_name`).
- `binary_sensor.py`: one `HetznerAccessSettingBinarySensor` per `access_settings` key (`ACCESS_SETTINGS` entity descriptions: key = API field, diagnostic category, `PARALLEL_UPDATES = 0`), unique_id `hetzner_storage_box_<id>_<api key>`; `is_on` is `None` when the key is missing or null. English entity_id names in its own `OBJECT_ID_NAMES`; on/off icons in `icons.json`.
- `sensor.py`: `SensorEntity` subclasses of `HetznerEntity` sharing the coordinator (`PARALLEL_UPDATES = 0`): status (ENUM with `STORAGE_BOX_STATUSES`; unknown API values become `None`), location, and five `HetznerSizeSensor`s (`SIZE_METRICS` in `const.py`). These report the API's raw int bytes (`native_unit_of_measurement = B`, `DATA_SIZE`, `MEASUREMENT`, no rounding) and leave conversion to HA: `suggested_unit_of_measurement = GiB` (`SIZE_SUGGESTED_UNIT`, only applied when HA first registers an entity) and `suggested_display_precision = 2`; users change the unit per entity in its settings. Names come from `_attr_translation_key` (`entity` section; the status sensor keeps `name = None` and only translates states), icons from `icons.json`. `HetznerEntity.suggested_object_id` returns the English name (`OBJECT_ID_NAMES` in `sensor.py`, must match `en.json`), because HA would otherwise derive new entity_ids from the translated names in languages with native entity_ids (`NATIVE_ENTITY_IDS`, e.g. de/nl) and they would no longer match the README.

### Config entry migration to v5 (non-obvious)

Before v5 the integration had a unit setting (`unit` in `data` up to v3, in `options` in v4; `"B"`/`"MB"`/`"GB"`/`"TB"`, default `"B"`, converted with decimal factors) and put it into the size sensors' native unit and unique_id (`hetzner_storage_box_<id>_<metric>_<unit_lower>`; v1: `..._<metric>`). It also had an `access_options` count sensor. `async_migrate_entry` (returns `False` for entries newer than `CONFIG_ENTRY_VERSION`; HA already refuses those before calling it) migrates every entry < 5 in one step:

- Size-sensor unique_ids become `hetzner_storage_box_<id>_<metric>` (`_size_unique_id`: strips a trailing `LEGACY_UNITS` suffix; parsed by splitting on `_`, so metrics must not contain one). `_safe_update_unique_id` removes a conflicting stale registry entity first; leftovers of earlier unit changes are processed before the entity of the current unit so that one survives.
- Display continuity: the native unit becomes bytes and `suggested_unit_of_measurement` is only applied to new registry entries. HA's own fallback (`SensorEntity.add_to_platform_start`) keeps the registry's previous unit only when no `sensor.private` options exist (the old code wrote them after unit changes), so the state's unit and value could change under numeric_state automations, templates and dashboards (long-term statistics would survive either way: the recorder converts between data size units and keeps the metadata unit, `sensor/recorder.py` `_normalize_states`). The migration therefore writes registry option `sensor.unit_of_measurement` = old unit (options unit wins over data unit; skipped if the user already set one) and replaces `sensor.private` with `{"suggested_unit_of_measurement": "GiB"}` (what a new entity gets; the old code stored its unit there). A `sensor` option, not only `sensor.private`: `sensor.async_update_suggested_units` (unit system change) recomputes `sensor.private` from the suggested unit, which would switch to GiB.
- Removes the registry entity with exactly unique_id `hetzner_storage_box_<id>_access_options` (sensor domain, box id from `data`).
- Drops `unit` from `data` and `options` and sets version 5 (keep `CONFIG_ENTRY_VERSION` and the flow `VERSION` in step).

Translations live in `strings.json` and `translations/{en,de,nl}.json` (`entity.binary_sensor` and `entity.sensor`); `strings.json` and `en.json` must be identical, and de/nl must have the same keys (de uses "du", nl "u").
