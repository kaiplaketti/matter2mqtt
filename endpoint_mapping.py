"""Stable MQTT identities for Matter endpoints that have been recommissioned."""

from dataclasses import replace

from models import EndpointInfo


def _endpoint(value):
    if not isinstance(value, str):
        raise ValueError("Endpoint aliases must use 'node/endpoint' strings")
    parts = value.split("/")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError("Endpoint aliases must use 'node/endpoint' strings")
    node, endpoint = map(int, parts)
    if node < 1 or endpoint < 0:
        raise ValueError("Alias node must be positive and endpoint nonnegative")
    return node, endpoint


class EndpointMap:
    """Map physical Matter IDs to stable MQTT IDs in both directions."""

    def __init__(self, aliases=None):
        if aliases is None:
            aliases = {}
        if not isinstance(aliases, dict):
            raise ValueError("mqtt.endpoint_aliases must be a mapping")
        self.to_logical = {}
        self.to_physical = {}
        for actual, logical in aliases.items():
            actual_key, logical_key = _endpoint(actual), _endpoint(logical)
            if actual_key in self.to_logical or logical_key in self.to_physical:
                raise ValueError("Endpoint aliases must have unique physical and MQTT identities")
            self.to_logical[actual_key] = logical_key
            self.to_physical[logical_key] = actual_key

    def matter_target(self, node_id, endpoint):
        return self.to_physical.get((node_id, endpoint), (node_id, endpoint))

    def mqtt_snapshot(self, endpoints):
        result = []
        identities = set()
        for ep in endpoints:
            physical = (ep.node_id, ep.endpoint)
            if physical in self.to_physical and self.to_physical[physical] != physical:
                raise ValueError("Endpoint alias reserves the identity of a discovered Matter endpoint")
            key = self.to_logical.get(physical, physical)
            if key in identities:
                raise ValueError("Endpoint alias collides with another discovered Matter endpoint")
            identities.add(key)
            result.append(replace(ep, node_id=key[0], endpoint=key[1]))
        return result
