"""Matter WebSocket client."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from constants import MATTER_WS_CONNECT_TIMEOUT, MATTER_SEND_COMMAND_TIMEOUT

logger = logging.getLogger(__name__)


class MatterWS:
    """
    Raw WS client for:
      - HELLO
      - start_listening (snapshot)
    """

    def __init__(self, url: str):
        self.url = url
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self._msg_id = 0

    def _next_id(self) -> str:
        self._msg_id += 1
        return str(self._msg_id)

    async def connect(self) -> Dict[str, Any]:
        """Connect to Matter WebSocket server."""
        try:
            self.session = aiohttp.ClientSession()
            self.ws = await self.session.ws_connect(self.url)
            logger.info(f"Connecting to Matter WebSocket at {self.url}")
            hello = await self._recv_json(timeout=MATTER_WS_CONNECT_TIMEOUT)
            logger.info("Matter WebSocket connection established")
            return hello
        except Exception as e:
            logger.error(f"Failed to connect to Matter WebSocket: {e}")
            raise

    async def close(self):
        """Close WebSocket connection."""
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()

    async def _recv_json(self, timeout: float) -> Dict[str, Any]:
        """Receive and parse JSON message."""
        if self.ws is None:
            raise RuntimeError("WebSocket not connected")
        msg = await self.ws.receive(timeout=timeout)
        if msg.type == aiohttp.WSMsgType.TEXT:
            return json.loads(msg.data)
        raise RuntimeError(f"Unexpected WebSocket message type: {msg.type}")

    async def send_command(self, command: str, args: Dict[str, Any], timeout: float = MATTER_SEND_COMMAND_TIMEOUT) -> Dict[str, Any]:
        """
        Sends a {"message_id","command","args"} frame and waits one response.
        """
        if self.ws is None:
            raise RuntimeError("WebSocket not connected")
        frame = {"message_id": self._next_id(), "command": command, "args": args}
        logger.debug(f"Sending Matter command: {command}")
        await self.ws.send_str(json.dumps(frame))
        return await self._recv_json(timeout=timeout)

    async def snapshot_nodes(self) -> List[Dict[str, Any]]:
        """Get snapshot of all nodes."""
        resp = await self.send_command("start_listening", {}, timeout=MATTER_SEND_COMMAND_TIMEOUT)
        # resp: { "message_id": "...", "result": [ ...nodes... ] }
        nodes = resp.get("result", [])
        if not isinstance(nodes, list):
            logger.warning("Unexpected response format from Matter WebSocket")
            return []
        logger.debug(f"Retrieved {len(nodes)} nodes from Matter")
        return nodes
