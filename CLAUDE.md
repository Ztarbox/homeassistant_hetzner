# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

HACS-distributed Home Assistant custom integration (`custom_components/hetzner`, domain `hetzner`) that polls the Hetzner Storage Box JSON API (`https://api.hetzner.com/v1/storage_boxes/{id}`, Bearer token) and exposes sensors. There is no separate library; all code lives in `custom_components/hetzner/`. Version is in `manifest.json`.

## Commands

Use Python 3.14 in a virtualenv.

```bash
pip install -r requirements.dev.txt
pytest . --black --isort --flake8 --mypy   # full check, as documented in README (run before pushing)
pytest tests/test_example.py::test_example # single test
```

CI (`.github/workflows/`) runs pytest, black, isort, flake8, mypy separately, plus hassfest and HACS validation. Style config (88 cols, isort `force_sort_within_sections`, flake8 ignores E501) is in `setup.cfg`. Tests are currently only a placeholder.

## Architecture

- `config_flow.py`: user step (API key → validate → fetch storage boxes → pick box + display unit) and an `OptionsFlow` to change the unit later. `VERSION = 3`.
- `__init__.py`: stores api key/host/unit per entry in `hass.data[DOMAIN][entry_id]` (unit prefers `options` over `data`), forwards to the `sensor` platform, and registers an update listener.
- `sensor.py`: one `DataUpdateCoordinator` per config entry (10-minute poll) shared by all entities: status, location, access options, and five `HetznerSizeSensor`s (`total`, `used`, `data`, `snapshots`, `free`). API values are bytes; `HetznerSizeSensor` converts to the selected display unit.

### Unit changes and unique_id migration (non-obvious)

Size sensors embed the unit in their unique_id: `hetzner_storage_box_<id>_<metric>_<unit_lower>` (legacy, pre-unit format: `..._<metric>`). Consequences:

- `async_migrate_entry` appends the unit to legacy unique_ids and bumps the entry to version 3 (config flow `VERSION` must stay in step with this).
- `_async_update_listener` (fires on options change) renames size-sensor unique_ids to the new unit, updates the registry's `suggested_unit_of_measurement` and clears user unit overrides (`_update_sensor_unit_options`), then reloads the entry.
- `_safe_update_unique_id` removes a conflicting stale registry entity before renaming.

Unique_ids are parsed by splitting on `_`, so changing the ID format or metric names requires updating `SIZE_METRICS`/`VALID_UNITS` and the parsing in `__init__.py`.

Translations live in `strings.json` and `translations/{en,de,nl}.json`; keep them in sync when changing config-flow text.
