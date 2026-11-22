"""Data models and dataclasses."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class EndpointInfo:
    """Information about a Matter endpoint with OnOff cluster."""
    node_id: int
    endpoint: int
    available: bool
    onoff: Optional[bool]  # None if unknown


@dataclass
class MqttCommand:
    """Command received from MQTT."""
    node_id: int
    endpoint: int
    action: str  # "on" | "off" | "toggle"


@dataclass
class ElectricalEndpointInfo:
    """Electrical measurements actually exposed by a Matter endpoint.

    A present metric with value None is supported but currently has no valid
    sample. An absent metric must not create a discovery sensor.
    """
    node_id: int
    endpoint: int
    available: bool
    values: dict[str, Optional[float]]
