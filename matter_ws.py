"""Serialized, reconnectable Matter WebSocket snapshot client."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from constants import MATTER_WS_CONNECT_TIMEOUT, MATTER_SEND_COMMAND_TIMEOUT

logger = logging.getLogger(__name__)


class MatterWS:
    """Poll get_nodes without opening an unsolicited event stream."""

    def __init__(self, url: str):
        self.url = url
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self._msg_id = 0
        self._request_lock = asyncio.Lock()

    async def connect(self) -> Dict[str, Any]:
        await self.close()
        try:
            self.session = aiohttp.ClientSession()
            self.ws = await asyncio.wait_for(
                self.session.ws_connect(self.url), MATTER_WS_CONNECT_TIMEOUT
            )
            hello = await self._recv_json(MATTER_WS_CONNECT_TIMEOUT)
            logger.info("Matter WebSocket connection established")
            return hello
        except (Exception, asyncio.CancelledError):
            await self.close()
            raise

    async def close(self):
        if self.ws is not None:
            await self.ws.close()
            self.ws = None
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def _recv_json(self, timeout: float) -> Dict[str, Any]:
        if self.ws is None:
            raise ConnectionError("Matter WebSocket not connected")
        msg = await self.ws.receive(timeout=timeout)
        if msg.type != aiohttp.WSMsgType.TEXT:
            raise ConnectionError(f"Unexpected WebSocket message type: {msg.type}")
        result = json.loads(msg.data)
        if not isinstance(result, dict):
            raise ValueError("Matter WebSocket response is not an object")
        return result

    async def send_command(
        self, command: str, args: Dict[str, Any],
        timeout: float = MATTER_SEND_COMMAND_TIMEOUT,
    ) -> Dict[str, Any]:
        async with self._request_lock:
            try:
                if self.ws is None or self.ws.closed:
                    await self.connect()
                self._msg_id += 1
                message_id = str(self._msg_id)
                frame = {"message_id": message_id, "command": command, "args": args}
                async with asyncio.timeout(timeout):
                    await self.ws.send_json(frame)
                    while True:
                        reply = await self._recv_json(timeout)
                        if reply.get("message_id") != message_id:
                            continue
                        if "error_code" in reply:
                            raise RuntimeError(
                                f"Matter {command} failed ({reply['error_code']}): "
                                f"{reply.get('details', '')}"
                            )
                        return reply
            except (Exception, asyncio.CancelledError):
                # The next snapshot establishes a fresh connection.
                await self.close()
                raise

    async def snapshot_nodes(self) -> List[Dict[str, Any]]:
        response = await self.send_command("get_nodes", {})
        nodes = response.get("result")
        if not isinstance(nodes, list) or not all(isinstance(n, dict) for n in nodes):
            raise ValueError("Matter get_nodes returned an invalid node list")
        return nodes
