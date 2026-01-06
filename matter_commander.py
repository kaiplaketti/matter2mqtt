"""Matter command client."""

import asyncio
import logging
from typing import Any, Dict, Optional

import aiohttp

from constants import DEVICE_COMMAND_TIMEOUT

logger = logging.getLogger(__name__)

# Optional: used for sending commands via MatterClient if it works in your environment.
try:
    from matter_server.client import MatterClient
    from chip.clusters import Objects as Clusters
except ImportError as e:
    logger.debug(f"MatterClient not available: {e}")
    MatterClient = None
    Clusters = None


class MatterCommander:
    """Send commands to Matter devices."""

    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self.session: Optional[aiohttp.ClientSession] = None
        self.client = None
        self._lock = asyncio.Lock()

    async def connect(self):
        """Connect to Matter server."""
        if MatterClient is None or Clusters is None:
            raise RuntimeError("MatterClient/Clusters not installed. Install matter-server package.")
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        self.client = MatterClient(self.ws_url, self.session)
        try:
            await self.client.connect()
            logger.info("Matter commander connected")
        except Exception as e:
            logger.error(f"Failed to connect Matter commander: {e}")
            raise

    async def ensure_connected(self):
        """Ensure client is connected."""
        async with self._lock:
            if self.client is None:
                await self.connect()
                return
            # Some versions expose .connected, others don't; try a cheap call
            try:
                # If your version has server_info, this is a quick sanity check
                _ = getattr(self.client, "server_info", None)
            except Exception:
                pass

    async def close(self):
        """Close Matter connection."""
        try:
            if self.client:
                await self.client.disconnect()
        finally:
            self.client = None
            if self.session and not self.session.closed:
                await self.session.close()
            self.session = None

    async def set_onoff(self, node_id: int, endpoint: int, action: str, timeout: float = DEVICE_COMMAND_TIMEOUT):
        """Send OnOff command to device."""
        await self.ensure_connected()
        if self.client is None:
            raise RuntimeError("Matter client not connected")
        
        if action not in ("on", "off", "toggle"):
            raise ValueError(f"Invalid action: {action}")

        cmd_obj = {
            "on": Clusters.OnOff.Commands.On(),
            "off": Clusters.OnOff.Commands.Off(),
            "toggle": Clusters.OnOff.Commands.Toggle(),
        }[action]

        async def _try(coro):
            return await asyncio.wait_for(coro, timeout=timeout)

        try:
            # common signature
            logger.debug(f"Sending {action} command to node {node_id} endpoint {endpoint}")
            return await _try(self.client.send_device_command(node_id, endpoint, cmd_obj))
        except Exception as e:
            # If disconnected, reconnect once and retry
            if "Not connected" in str(e) or "InvalidState" in type(e).__name__:
                logger.info(f"Reconnecting to Matter client after disconnection")
                await self._reconnect_and_retry(node_id, endpoint, cmd_obj, timeout)
                return
            logger.error(f"Failed to send command to node {node_id}: {e}")
            raise

    async def _reconnect_and_retry(self, node_id: int, endpoint: int, cmd_obj, timeout: float):
        """Reconnect and retry command."""
        async with self._lock:
            try:
                if self.client:
                    await self.client.disconnect()
            except Exception as e:
                logger.debug(f"Error disconnecting during reconnect: {e}")
            self.client = None
            await self.connect()

        async def _try(coro):
            return await asyncio.wait_for(coro, timeout=timeout)

        # retry after reconnect
        logger.info(f"Retrying command to node {node_id} endpoint {endpoint}")
        return await _try(self.client.send_device_command(node_id, endpoint, cmd_obj))
