"""Read-only Matter 1.3 electrical measurement parsing and MQTT discovery."""

import json
import math

from ha_common import build_availability_fields, build_device_info
from home_assistant import DiscoveryPayload
from matter_helpers import _parse_attr_key
from models import ElectricalEndpointInfo

# Standard units are mV, mA, mW and mWh. Keep energy as the device's cumulative
# counter; do not invent a total from power samples or substitute zero for null.
METRICS = {
    'power': (144, 8, 1000, 'Power', 'power', 'W', 'measurement'),
    'voltage': (144, 4, 1000, 'Voltage', 'voltage', 'V', 'measurement'),
    'current': (144, 5, 1000, 'Current', 'current', 'A', 'measurement'),
    'energy_imported': (145, 1, 1000000, 'Energy imported', 'energy', 'kWh', 'total_increasing'),
    'energy_exported': (145, 2, 1000000, 'Energy exported', 'energy', 'kWh', 'total_increasing'),
}


def _measurement(value, divisor, *, energy=False):
    if energy and isinstance(value, dict):
        # Matter Server's decoded structs normally use numeric field tags.
        value = value.get('0', value.get(0, value.get('energy', value.get('Energy'))))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = value / divisor
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(result) or (energy and result < 0):
        return None
    return result


def extract_electrical_endpoints(node):
    attrs = node.get('attributes') or {}
    endpoints = set()
    for key in attrs:
        parsed = _parse_attr_key(key)
        if parsed and parsed[1] in (144, 145):
            endpoints.add(parsed[0])
    result = []
    for endpoint in sorted(endpoints):
        values = {}
        for metric, (cluster, attribute, divisor, *_metadata) in METRICS.items():
            path = f'{endpoint}/{cluster}/{attribute}'
            supported = attrs.get(f'{endpoint}/{cluster}/65531', [])
            if path not in attrs and (not isinstance(supported, list) or attribute not in supported):
                continue
            values[metric] = _measurement(attrs.get(path), divisor, energy=cluster == 145)
        if values:
            result.append(ElectricalEndpointInfo(
                int(node['node_id']), endpoint, bool(node.get('available', False)), values,
            ))
    return result


def measurement_state_topic(ep, metric):
    return f'matter/{ep.node_id}/{ep.endpoint}/{metric}/state'


def measurement_availability_topic(ep, metric):
    return f'matter/{ep.node_id}/{ep.endpoint}/{metric}/available'


def build_measurement_discovery(ep, metric, discovery_prefix):
    _, _, _, name, device_class, unit, state_class = METRICS[metric]
    identity = f'matter_{ep.node_id}_{ep.endpoint}_{metric}'
    payload = {
        'name': f'Matter {ep.node_id}/{ep.endpoint} {name}',
        'unique_id': identity,
        'state_topic': measurement_state_topic(ep, metric),
        'device_class': device_class,
        'unit_of_measurement': unit,
        'state_class': state_class,
        'device': build_device_info(
            identifiers=[f'matter_node_{ep.node_id}'],
            name=f'Matter Node {ep.node_id}', manufacturer='Matter', model='OnOff Device',
        ),
    }
    payload.update(build_availability_fields(
        availability_topic=measurement_availability_topic(ep, metric),
        payload_available='true', payload_not_available='false',
    ))
    return DiscoveryPayload(
        topic=f'{discovery_prefix}/sensor/{identity}/config',
        payload=json.dumps(payload, separators=(',', ':')),
    )
