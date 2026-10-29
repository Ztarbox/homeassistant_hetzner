<img width="100%" align=right alt="Logo_Hetzner svg" src="https://github.com/user-attachments/assets/4e410d98-0222-430e-beb6-923fdf30d7bb" />  
&nbsp;

# Hetzner Storage Box Integration for Home Assistant  
 
<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Description](#description)
- [Installation](#installation)
  - [Installation through HACS](#installation-through-hacs)
  - [Manual Installation](#manual-installation)
- [Configuration](#configuration)
  - [Prerequisites: Obtaining an API Key](#prerequisites-obtaining-an-api-key)
  - [Setup via Configuration Flow](#setup-via-configuration-flow)
  - [Changing the Unit](#changing-the-unit)
  - [Replacing the API Key](#replacing-the-api-key)
- [Sensors and Attributes](#sensors-and-attributes)
- [Update Interval](#update-interval)
- [Removing the Integration](#removing-the-integration)
- [Development](#development)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Description
**Hetzner Storage Box**  

Home Assistant custom component that exposes storage metrics for your [Hetzner Storage Box](https://www.hetzner.com/storage/storage-box/) via the official JSON API.  
Track **total, used, free, data and snapshot sizes** as native size sensors in your configured unit (`B`, `MB`, `GB`, or `TB`), plus a status sensor that shows the box state and enriches it with details such as type, creation date and server name. Also a `location` sensor is available to show details of where your data is stored.
Perfect for monitoring your off-site backup space and add it to dashboards, automations and long-term statistics.

## Installation

### Installation through HACS
The easiest way to install the Hetzer Storage Box integration is through HACS. 

1. Find the Hetzner Storage Box Integration in the Home Assistant Community Store [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Dixet&repository=homeassistant_hetzner&category=integration)
2. Download the repository
3. Restart Home Assistant

### Manual Installation


1. Download the `hetzner` custom component from this repository
2. Navigate to your Home Assistant configuration directory
3. Create a `custom_components` folder if it doesn't already exist
4. Place the `hetzner` folder inside the `custom_components` directory
5. Restart Home Assistant

Your directory structure should look like this:
```
home-assistant/
├── configuration.yaml
├── custom_components/
│   └── hetzner/
│       ├── brand/
│       ├── __init__.py
│       ├── config_flow.py
│       ├── const.py
│       ├── coordinator.py
│       ├── entity.py
│       ├── manifest.json
│       ├── sensor.py
│       ├── strings.json
│       └── translations/
```

## Configuration

### Prerequisites: Obtaining an API Key

You need a Hetzner account with at least one Storage Box and an API key of the project that contains it.
To obtain one, sign in into the [Hetzner Console](https://console.hetzner.com) and choose a Project, go to Security → API Tokens, and generate a new token. Read access is enough. Make sure to copy the token because it won’t be shown to you again.

### Setup via Configuration Flow
[![Start Config Flow](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=hetzner)
1. In Home Assistant, navigate to **Settings** > **Devices & Services**
2. Click **Add Integration** and search for "Hetzner"
3. Enter your API key when prompted
4. Select the desired unit of measurement (`B`, `MB`, `GB`, or `TB`). The default is `B`.
5. Select the Storage Box you would like to configure
6. Complete the configuration flow

To monitor more than one Storage Box, add the integration once per box.

### Changing the Unit

Open **Settings** > **Devices & Services** > **Hetzner Storage Box**, click **Configure** and select a new unit. The size sensors keep their entity IDs.

### Replacing the API Key

If the Hetzner API rejects the API key, for example because the token was revoked, Home Assistant shows a **Reauthenticate** notice for the integration. Follow it and enter a new API key of the same project.


## Sensors and Attributes

The integration provides the following sensors:

#### sensor.storage_box_&lt;boxname&gt;
- **State**: The current status of the storage box
- **Attributes**:
  - `ID`: The id of your storage box
  - `name`: The name of your storage box
  - `username`: The username of the storage box owner
  - `server`: FQDN of the Storage Box
  - `system`: Host system of the Storage Box.
  - `storage_box_type`: Type of storage box (subscription type)
  - `created`: When the storage box was first created

#### sensor.storage_box_&lt;boxname&gt;
- **State**: The current status of the storage box
- **Attributes**:
  - `ID`: unique location identifier
  - `Country`: Country where the data is stored
  - `City`: City where the data is stored
  - `Latitude`: Latitude of the location
  - `Longitude`: Longitude  of the location
  - `Description`: Full name of the location

#### sensor.storage_box_&lt;boxname&gt;_location
- **State**: Location code for your storage box location
- **Unit**: bytes

#### sensor.storage_box_&lt;boxname&gt;_total_used
- **State**: The total used size in the configured unit
- **Unit**: User-selected unit (`B`, `MB`, `GB`, or `TB`)

#### sensor.storage_box_&lt;boxname&gt;_data_size
- **State**: The size used for data in the configured unit
- **Unit**: User-selected unit (`B`, `MB`, `GB`, or `TB`)

#### sensor.storage_box_&lt;boxname&gt;_snapshot_size
- **State**: The size used for snapshots in the configured unit
- **Unit**: User-selected unit (`B`, `MB`, `GB`, or `TB`)

#### sensor.storage_box_&lt;boxname&gt;_free_space
- **State**: The available free space in your Storage Box in the configured unit
- **Unit**: User-selected unit (`B`, `MB`, `GB`, or `TB`)

#### sensor.storage_box_&lt;boxname&gt;_access_options
- **State**: Number of access methods that are enabled (integer)
- **Attributes**:
  - `webdav_enabled`: boolean
  - `zfs_enabled`: boolean
  - `samba_enabled`: boolean
  - `ssh_enabled`: boolean
  - `reachable_externally`: boolean

**Example:** with `samba_enabled`, `ssh_enabled`, `reachable_externally` set to `true`, the sensor state will be `3`.

## Update Interval

All sensors are automatically updated every 10 minutes to fetch the latest status of your Storage Box.

## Removing the Integration

1. In Home Assistant, navigate to **Settings** > **Devices & Services** > **Hetzner Storage Box**
2. Open the three-dot menu of each configured Storage Box and select **Delete**
3. To uninstall the files as well, remove the integration in HACS (or delete `custom_components/hetzner` for a manual installation) and restart Home Assistant
4. Optionally, revoke the API token in the [Hetzner Console](https://console.hetzner.com) under your project → Security → API Tokens

## Development

It is recommend to create a virtualenv with Python 3.14.x
After that you can run 

```bash
$ pip install -r requirements.dev.txt
```

If you make any changes, please run pytest before pushing your changes.

```bash
$ pytest . --black --isort --flake8 --mypy
```
