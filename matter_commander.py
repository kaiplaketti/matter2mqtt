"""OnOff commands through the Python Matter Server WebSocket API."""

from constants import DEVICE_COMMAND_TIMEOUT, ONOFF_CLUSTER_ID
from matter_ws import MatterWS


class MatterCommander:
    """Use the server's JSON API; no local CHIP SDK is needed."""

    def __init__(self, ws_url: str):
        self.client = MatterWS(ws_url)

    async def connect(self):
        # The handshake is read-only; commands are sent only by set_onoff.
        return await self.client.connect()

    async def close(self):
        await self.client.close()

    async def set_onoff(
        self, node_id: int, endpoint: int, action: str,
        timeout: float = DEVICE_COMMAND_TIMEOUT,
    ):
        command_name = {"on": "On", "off": "Off", "toggle": "Toggle"}.get(action)
        if command_name is None:
            raise ValueError(f"Invalid OnOff action: {action}")
        # Never retry a dispatched command: a lost response does not prove that
        # the device did not execute it, especially for Toggle.
        reply = await self.client.send_command(
            "device_command",
            {
                "node_id": node_id,
                "endpoint_id": endpoint,
                "cluster_id": ONOFF_CLUSTER_ID,
                "command_name": command_name,
                "payload": {},
            },
            timeout=timeout,
        )
        return reply.get("result")

    async def read_onoff(self, node_id: int, endpoint: int) -> bool:
        path = f"{endpoint}/{ONOFF_CLUSTER_ID}/0"
        reply = await self.client.send_command(
            "read_attribute", {"node_id": node_id, "attribute_path": path}
        )
        result = reply.get("result")
        if not isinstance(result, dict) or not isinstance(result.get(path), bool):
            raise ValueError("Matter read_attribute did not return a boolean OnOff state")
        return result[path]
