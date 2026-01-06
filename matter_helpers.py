"""Helper functions for parsing Matter attributes."""

from typing import Any, Dict, List, Optional, Set, Tuple

from constants import ONOFF_CLUSTER_ID, ONOFF_ATTRIBUTE_ID
from models import EndpointInfo


def _parse_attr_key(key: str) -> Optional[Tuple[int, int, int]]:
    """
    Matter-server attribute dict keys look like: "1/6/0" meaning:
      endpoint/cluster/attribute
    Returns (endpoint, cluster, attribute) as ints.
    """
    try:
        ep_s, cl_s, at_s = key.split("/", 2)
        return int(ep_s), int(cl_s), int(at_s)
    except Exception:
        return None


def extract_onoff_endpoints_from_node(node: Dict[str, Any]) -> List[EndpointInfo]:
    """
    Given one node dict from start_listening result, find endpoints that have OnOff cluster (6)
    and read OnOff attribute (attribute 0) if present.
    """
    node_id = int(node.get("node_id"))
    available = bool(node.get("available", False))
    attrs: Dict[str, Any] = node.get("attributes", {}) or {}

    # endpoints that have cluster 6 in any attribute
    endpoints_with_onoff: Set[int] = set()

    # OnOff state stored at "<ep>/6/0"
    onoff_state_by_ep: Dict[int, bool] = {}

    for k, v in attrs.items():
        parsed = _parse_attr_key(k)
        if not parsed:
            continue
        ep, cl, at = parsed
        if cl == ONOFF_CLUSTER_ID:
            endpoints_with_onoff.add(ep)
            if at == ONOFF_ATTRIBUTE_ID:
                # spec: OnOff attribute is boolean
                try:
                    onoff_state_by_ep[ep] = bool(v)
                except (ValueError, TypeError):
                    pass

    infos: List[EndpointInfo] = []
    for ep in sorted(endpoints_with_onoff):
        infos.append(
            EndpointInfo(
                node_id=node_id,
                endpoint=ep,
                available=available,
                onoff=onoff_state_by_ep.get(ep),
            )
        )
    return infos
