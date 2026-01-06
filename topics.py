"""Topic utilities for MQTT."""

from constants import HA_DISCOVERY_DEVICE_CLASS


def topic_state(node_id: int, endpoint: int) -> str:
    """Get MQTT topic for endpoint state."""
    return f"matter/{node_id}/{endpoint}/state"


def topic_available(node_id: int, endpoint: int) -> str:
    """Get MQTT topic for endpoint availability."""
    return f"matter/{node_id}/{endpoint}/available"


def ha_discovery_topic(node_id: int, endpoint: int) -> str:
    """Get Home Assistant discovery topic for endpoint."""
    return f"homeassistant/{HA_DISCOVERY_DEVICE_CLASS}/matter_{node_id}_{endpoint}/config"
