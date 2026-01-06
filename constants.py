"""Constants for Matter2MQTT bridge."""

# Matter Cluster IDs
ONOFF_CLUSTER_ID = 6

# Matter Attribute IDs
ONOFF_ATTRIBUTE_ID = 0

# Default configuration paths
DEFAULT_CONFIG_FILE = "matter2mqtt.yaml"
DEFAULT_CONFIG_EXAMPLE_FILE = "matter2mqtt.yaml.example"

# MQTT Topics and Payloads
MQTT_CMD_TOPIC_PATTERN = "matter/+/+/set"
MQTT_PAYLOAD_ON = "ON"
MQTT_PAYLOAD_OFF = "OFF"
MQTT_PAYLOAD_AVAILABLE = "true"
MQTT_PAYLOAD_UNAVAILABLE = "false"

# Home Assistant Discovery
HA_DISCOVERY_DEVICE_CLASS = "light"

# Timeouts (seconds)
MATTER_WS_CONNECT_TIMEOUT = 5.0
MATTER_SEND_COMMAND_TIMEOUT = 15.0
DEVICE_COMMAND_TIMEOUT = 15.0

# Refresh interval (seconds)
SNAPSHOT_REFRESH_INTERVAL = 30

# MQTT settings
MQTT_QOS = 1
MQTT_KEEPALIVE = 60
