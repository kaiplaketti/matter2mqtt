# Matter2MQTT Bridge

A Python application that bridges Matter IoT devices to MQTT, enabling integration with home automation systems like Home Assistant and Domoticz.

## What This Does

Matter2MQTT is a bridge that:

- **Connects to a Matter network** via the Python Matter Server WebSocket interface
- **Monitors Matter device states** - tracks OnOff cluster status for connected devices
- **Publishes to MQTT** - exposes device states as MQTT topics for home automation systems
- **Receives MQTT commands** - allows control of Matter devices through MQTT topics
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

- **Python 3.9 or later**

## Installation

1. **Clone or download this project**

2. **Install Python dependencies:**

   ```bash
   pip install paho-mqtt pyyaml matter-server
   ```

   Or install from `requirements.txt` if available:

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

   matter_ws:
     url: ws://localhost:5580/ws  # Your Matter Server WebSocket URL
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

- **Device State:** `matter/{node_id}/{endpoint}/state` → `on` or `off`
- **Device Availability:** `matter/{node_id}/{endpoint}/available` → `online` or `offline`
- **Home Assistant Discovery:** `homeassistant/light/matter-{node_id}-{endpoint}/config`

### Subscribing (MQTT to Bridge)

- **Command Topic:** `matter/{node_id}/{endpoint}/set` ← `on`, `off`, or `toggle`

## Configuration

Edit `matter2mqtt.yaml` to customize:

```yaml
mqtt:
  host: localhost        # MQTT broker hostname/IP
  port: 1883            # MQTT broker port (default 1883)

matter_ws:
  url: ws://localhost:5580/ws  # Matter Server WebSocket URL
```

## Home Assistant Integration

Matter2MQTT publishes MQTT Discovery messages automatically. In Home Assistant:

1. Ensure MQTT integration is configured
2. Discovered devices appear in Settings → Devices & Services → MQTT
3. Devices are created with automatic on/off control

## Troubleshooting

- **Can't connect to MQTT**: Verify MQTT broker is running and accessible
- **Can't connect to Matter Server**: Verify Matter Server is running and WebSocket URL is correct
- **No devices showing up**: Ensure devices are commissioned in the Matter network
- **Commands not working**: Check Matter Server logs for OnOff cluster support

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
