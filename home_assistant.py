"""Home Assistant–compatible MQTT discovery payloads for Matter endpoints."""

from __future__ import annotations

import json
from dataclasses import dataclass

from constants import HA_DISCOVERY_DEVICE_CLASS, MQTT_PAYLOAD_AVAILABLE, MQTT_PAYLOAD_UNAVAILABLE
from ha_common import build_availability_fields, build_device_info
from models import EndpointInfo
from topics import ha_discovery_topic, topic_available, topic_state


@dataclass(slots=True)
class DiscoveryPayload:
    topic: str
    payload: str


def build_discovery_payload(ep: EndpointInfo, discovery_prefix: str) -> DiscoveryPayload:
    payload = {
        "name": f"Matter {ep.node_id}/{ep.endpoint}",
        "state_topic": topic_state(ep.node_id, ep.endpoint),
        "command_topic": f"matter/{ep.node_id}/{ep.endpoint}/set",
        "payload_on": "ON",
        "payload_off": "OFF",
        "unique_id": f"matter_{ep.node_id}_{ep.endpoint}",
        "device": build_device_info(
            identifiers=[f"matter_node_{ep.node_id}"],
            name=f"Matter Node {ep.node_id}",
            manufacturer="Matter",
            model="OnOff Device",
        ),
    }
    payload.update(
        build_availability_fields(
            availability_topic=topic_available(ep.node_id, ep.endpoint),
            payload_available=MQTT_PAYLOAD_AVAILABLE,
            payload_not_available=MQTT_PAYLOAD_UNAVAILABLE,
        )
    )
    return DiscoveryPayload(
        topic=ha_discovery_topic(
            node_id=ep.node_id,
            endpoint=ep.endpoint,
            discovery_prefix=discovery_prefix,
            component=HA_DISCOVERY_DEVICE_CLASS,
        ),
        payload=json.dumps(payload, separators=(",", ":")),
    )
