# Matter2MQTT Bridge

A Python application that bridges Matter IoT devices to MQTT, enabling integration with home automation systems like Home Assistant and Domoticz.

## What This Does

Matter2MQTT is a bridge that:

- **Connects to a Matter network** via the Python Matter Server WebSocket interface
- **Monitors Matter device states** - tracks OnOff cluster status for connected devices
- **Publishes to MQTT** - exposes device states as MQTT topics for home automation systems
- **Optional MQTT commands** - relay control can be enabled explicitly; disabled by default
- **Supports Home Assistant** - publishes MQTT Discovery messages for automatic device integration
- **Maintains device availability** - tracks and publishes device availability status

### Supported Matter Devices

⚠️ **Currently this supports only one type of Matter device:** Devices with the **OnOff cluster** (e.g., smart lights, switches).

## Requirements

Before running Matter2MQTT, you need to install and configure:

### 1. MQTT Broker

An MQTT message broker is required for communication. Recommended:

- **[Mosquitto](https://mosquitto.org/)** - Lightweight, open-source MQTT broker
  - Windows: Download from [mosquitto.org](https://mosquitto.org/download/) or use `choco install mosquitto`
  - Linux: `apt-get install mosquitto mosquitto-clients`
  - Docker: `docker run -d -p 1883:1883 eclipse-mosquitto`

Default connection: `localhost:1883` (configurable in `matter2mqtt.yaml`)

### 2. Python Matter Server

A running Matter Server WebSocket service is required to communicate with Matter devices:

- **[python-matter-server](https://github.com/home-assistant-libs/python-matter-server)** - Python Matter Controller
  - Install: `pip install python-matter-server`
  - Run: `matter-server` (starts on port 5580 by default)
  - Docker: `docker run -d -p 5580:5580 ghcr.io/home-assistant-libs/python-matter-server:latest`

Default connection: `ws://localhost:5580/ws` (configurable in `matter2mqtt.yaml`)

### 3. Python Runtime

- **Python 3.11 or later**

## Installation

1. **Clone or download this project**

2. **Install Python dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure the application:**

   Copy the example configuration to create your config file:

   ```bash
   cp matter2mqtt.yaml.example matter2mqtt.yaml
   ```

   Then edit `matter2mqtt.yaml` with your settings:

   ```yaml
   mqtt:
     host: localhost      # Your MQTT broker host
     port: 1883          # Your MQTT broker port
     discovery_prefix: homeassistant  # HA discovery root

   matter_ws:
     url: ws://localhost:5580/ws  # Your Matter Server WebSocket URL
     commands_enabled: false
   ```

4. **Run the bridge:**

   ```bash
   python matter2mqtt.py
   ```

   Or on Linux/Mac:

   ```bash
   python3 matter2mqtt.py
   ```

## MQTT Topics

### Publishing (Bridge to MQTT)

- **Device State:** `matter/{node_id}/{endpoint}/state` → `ON` or `OFF`
- **Device Availability:** `matter/{node_id}/{endpoint}/available` → `true` or `false`
- **Home Assistant Discovery:** `{discovery_prefix}/light/matter_{node_id}_{endpoint}/config` (default `homeassistant`)

### Subscribing (MQTT to Bridge)

- **Command Topic:** `matter/{node_id}/{endpoint}/set` ← `on`, `off`, or `toggle`; subscribed only when `matter_ws.commands_enabled` is `true`. Retained commands are ignored.

## Configuration

Edit `matter2mqtt.yaml` to customize:

```yaml
mqtt:
  host: localhost        # MQTT broker hostname/IP
  port: 1883            # MQTT broker port (default 1883)
  discovery_prefix: homeassistant  # HA discovery root

matter_ws:
  url: ws://localhost:5580/ws  # Matter Server WebSocket URL
  commands_enabled: false
```

### Status-only mode and recovery

Status-only mode is the default, including when `commands_enabled` is omitted from an existing configuration. Discovery, availability and reported device states are published normally. The bridge does not subscribe to relay commands or construct a Matter command client in this mode.

To use relay control, explicitly set `matter_ws.commands_enabled: true` and restart the bridge. The bridge sends OnOff commands through the Matter Server's `device_command` WebSocket API; it does not need a local Matter client or CHIP SDK. The server's `read_attribute` API confirms the device state after a command. The JSON request format matches Python Matter Server 8.1.1 (schema 11).

Commands are sent once and are not automatically replayed after a transport failure. Retained MQTT commands are ignored. Reported states are republished after a command attempt, including failures, so a UI's optimistic state is corrected. The regression tests use a local fake server and never operate real relays.

Each command and confirming read uses a fresh, short-lived WebSocket connection. The command socket is closed between user actions, so an idle server heartbeat cannot leave the next command writing to a stale transport. The separate snapshot connection continues polling normally.

Snapshots use `get_nodes` every 30 seconds. The WebSocket client matches response IDs and reconnects after a dropped connection. MQTT reconnection republishes retained discovery, availability and state, even when the values have not changed.

Startup waits up to 15 seconds for an accepted MQTT connection. Startup errors propagate to a nonzero process exit after cleanup, so a supervisor configured with `Restart=on-failure` can retry while dependent services are starting. The bridge does not restart the broker or Matter Server.

## Home Assistant Integration

### Electrical measurements (Matter 1.3)

When a device actually exposes the standard Electrical Power Measurement (144)
or Electrical Energy Measurement (145) clusters, the bridge also publishes
read-only MQTT Discovery sensors for supported values:

- Active power (W), voltage (V), and active current (A).
- Cumulative imported energy (kWh), and exported energy if the device provides it.

Each metric uses `matter/<node>/<endpoint>/<metric>/state` and its own
`.../<metric>/available` topic. Discovery is published under
`<discovery_prefix>/sensor/matter_<node>_<endpoint>_<metric>/config`.
These sensors have no command topic. Existing relay discovery and endpoint
aliases retain their identities.

Only supported measurements create sensors. A null, invalid or missing sample
is unavailable and is never replaced with zero; zero is published when actually
reported by the device. The bridge converts standard mV/mA/mW to V/A/W and mWh
to kWh, and uses the device's cumulative energy counter rather than estimating
consumption from power. MQTT reconnection republishes discovery and valid
measurements even when the values did not change.
Valid measurement states are also refreshed on every snapshot (30 seconds), so
an idle plug's unchanged zero-watt reading does not time out in Domoticz.

Some plugs require a manufacturer firmware update before they expose these
clusters. This bridge does not install device firmware, reset devices, or create
placeholder power/energy values for plugs that only expose OnOff. After a device
update, the Matter Server must interview the device so its snapshot includes the
new clusters. The bridge then discovers the measurements automatically.

Matter2MQTT publishes MQTT Discovery messages automatically. In Home Assistant:

1. Ensure MQTT integration is configured
2. Discovered devices appear in Settings → Devices & Services → MQTT
3. Devices report state and availability; relay control is optional as described above

Notes:
- Matter2MQTT publishes endpoint availability as `true` / `false` on `matter/.../available`.
- Mijia2MQTT uses `online` / `offline` on `home/mijia/.../availability`.
- This difference is intentional today, so harmonize payload strings only if all subscribers are updated.

## Troubleshooting

- **Can't connect to MQTT**: Verify MQTT broker is running and accessible
- **Can't connect to Matter Server**: Verify Matter Server is running and WebSocket URL is correct
- **No devices showing up**: Ensure devices are commissioned in the Matter network
- **Commands not working**: Check `matter_ws.commands_enabled`, the command topic, the Matter Node's availability and Matter Server logs for OnOff cluster support

## Keeping an existing device after recommissioning

When a plug receives a new Matter Node ID, map its physical endpoint to the old MQTT identity in the local configuration:

```yaml
mqtt:
  host: localhost
  port: 1883
  discovery_prefix: homeassistant
  endpoint_aliases:
    "42/1": "1/1"
```

This example publishes the physical endpoint `42/1` as `matter_1_1`, with state, availability and command topics under `matter/1/1/`. Commands to that stable identity are routed to physical endpoint `42/1`. The bridge does not publish a second discovery identity for the mapped endpoint. Other endpoints retain their identities. Duplicate identities are rejected to prevent an alias from overwriting another discovered device.

This allows Domoticz MQTT Auto Discovery to reuse an existing DeviceID and IDX without editing its database or changing automations. Configure the mapping before the bridge publishes the recommissioned device. The alias is deployment-specific and belongs in the ignored local `matter2mqtt.yaml`.

## Tests

After installing `requirements.txt`, run:

```bash
python -m unittest discover -s tests -v
```

The suite creates its own temporary configuration and uses local WebSocket/MQTT fakes. It needs no broker, Matter Server or commissioned device and sends no commands to real relays. CI also runs the suite with pytest on Python 3.11, 3.12 and 3.13.

## Architecture

- **matter2mqtt.py** - Application entry point
- **matter2mqtt_app.py** - Main bridge application logic
- **matter_ws.py** - Matter WebSocket client
- **matter_commander.py** - Matter device command executor
- **mqtt_bridge.py** - MQTT client wrapper
- **matter_helpers.py** - Utility functions for Matter data parsing
- **models.py** - Data models and structures
- **topics.py** - MQTT topic helper functions

## License

[Specify your license here]

## Broker authentication

Set `MQTT_USERNAME` and `MQTT_PASSWORD` in the bridge process environment when the broker requires authentication. For a Linux service, load them from a private systemd `EnvironmentFile` with mode `0600`; keep that file outside the repository and never commit real credentials.

If a username is configured without a password, startup fails before connecting. Without a username, the existing unauthenticated development-broker behavior is preserved. Authentication does not enable relay commands: `matter_ws.commands_enabled` remains a separate setting and defaults to `false`.

The regression suite includes authenticated-client configuration, rejection of an incomplete credential pair, and the original no-credentials behavior. All 40 local-fake tests pass without contacting real devices.
