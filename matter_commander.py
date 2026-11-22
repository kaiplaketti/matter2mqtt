"""OnOff commands through the Python Matter Server WebSocket API."""

import asyncio

from constants import DEVICE_COMMAND_TIMEOUT, ONOFF_CLUSTER_ID
from matter_ws import MatterWS


class MatterCommander:
    """Use the server's JSON API; no local CHIP SDK is needed."""

    def __init__(self, ws_url: str):
        self.client = MatterWS(ws_url)
        self._lock = asyncio.Lock()

    async def connect(self):
        # The handshake is read-only; commands are sent only by set_onoff.
        async with self._lock:
            try:
                return await self.client.connect()
            finally:
                await self.client.close()

    async def close(self):
        await self.client.close()

    async def _request(self, command, args, timeout=DEVICE_COMMAND_TIMEOUT):
        async with self._lock:
            # No idle command socket: server heartbeat frames would otherwise
            # go unread while we are waiting for the next MQTT command.
            await self.client.close()
            try:
                return await self.client.send_command(command, args, timeout=timeout)
            finally:
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
        reply = await self._request(
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
        reply = await self._request(
            "read_attribute", {"node_id": node_id, "attribute_path": path}
        )
        result = reply.get("result")
        if not isinstance(result, dict) or not isinstance(result.get(path), bool):
            raise ValueError("Matter read_attribute did not return a boolean OnOff state")
        return result[path]
