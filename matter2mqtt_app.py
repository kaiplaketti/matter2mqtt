"""Main Matter2MQTT bridge application."""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import yaml

from constants import (
    DEFAULT_CONFIG_FILE,
    SNAPSHOT_REFRESH_INTERVAL,
)
from models import EndpointInfo, MqttCommand
from mqtt_bridge import MqttBridge
from matter_ws import MatterWS
from matter_commander import MatterCommander
from matter_helpers import extract_onoff_endpoints_from_node
from topics import topic_state, topic_available, ha_discovery_topic

logger = logging.getLogger(__name__)


def _load_config() -> Dict[str, Any]:
    """Load and validate configuration file."""
    if not os.path.exists(DEFAULT_CONFIG_FILE):
        raise FileNotFoundError(
            f"Configuration file '{DEFAULT_CONFIG_FILE}' not found. "
            f"Please copy '{DEFAULT_CONFIG_FILE}.example' to '{DEFAULT_CONFIG_FILE}' "
            f"and update with your settings."
        )
    
    try:
        with open(DEFAULT_CONFIG_FILE, 'r') as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in '{DEFAULT_CONFIG_FILE}': {e}")
    
    if not config:
        raise ValueError(f"'{DEFAULT_CONFIG_FILE}' is empty")
    
    # Validate required sections
    if 'mqtt' not in config:
        raise ValueError("Missing 'mqtt' section in configuration")
    if 'matter_ws' not in config:
        raise ValueError("Missing 'matter_ws' section in configuration")
    
    # Validate required keys
    mqtt_config = config.get('mqtt', {})
    if 'host' not in mqtt_config:
        raise ValueError("Missing 'mqtt.host' in configuration")
    if 'port' not in mqtt_config:
        raise ValueError("Missing 'mqtt.port' in configuration")
    
    matter_config = config.get('matter_ws', {})
    if 'url' not in matter_config:
        raise ValueError("Missing 'matter_ws.url' in configuration")
    
    return config


try:
    config = _load_config()
except (FileNotFoundError, ValueError, yaml.YAMLError) as e:
    logger.error(f"Configuration error: {e}")
    raise

MATTER_WS_URL = config['matter_ws']['url']


class Matter2MQTT:
    """Main bridge application."""

    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.cmd_queue: asyncio.Queue[MqttCommand] = asyncio.Queue()

        self.mqtt = MqttBridge(self.loop, self.cmd_queue)
        self.matter_ws = MatterWS(MATTER_WS_URL)
        self.matter_cmd = MatterCommander(MATTER_WS_URL)

        # Track last published states to avoid spamming
        self.last_state: Dict[Tuple[int, int], Optional[bool]] = {}
        self.last_avail: Dict[Tuple[int, int], Optional[bool]] = {}

        self.running = True

    async def start(self):
        """Start the bridge."""
        self.mqtt.connect()
        logger.info("MQTT bridge connected")

        hello = await self.matter_ws.connect()
        logger.info(
            f"Matter WebSocket connected. schema={hello.get('schema_version')} "
            f"sdk={hello.get('sdk_version')}"
        )

        try:
            await self.matter_cmd.connect()
            logger.info("Matter commander connected (can send OnOff commands)")
        except Exception as e:
            logger.warning(f"Matter commander not available, commands disabled: {e}")

        await self.refresh_snapshot()

        self._tasks = [
            asyncio.create_task(self.periodic_refresh_task(), name="refresh"),
            asyncio.create_task(self.command_consumer_task(), name="cmd_consumer"),
        ]

        # Wait until tasks finish (they won't until stopped)
        await asyncio.gather(*self._tasks)

    async def stop(self):
        """Stop the bridge."""
        if not self.running:
            return
        self.running = False

        # Cancel tasks first
        tasks = getattr(self, "_tasks", [])
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        # Then close resources
        try:
            self.mqtt.close()
        except Exception:
            pass
        try:
            await self.matter_ws.close()
        except Exception:
            pass
        try:
            await self.matter_cmd.close()
        except Exception:
            pass

    def publish_ha_discovery(self, ep: EndpointInfo):
        """Publish Home Assistant discovery message."""
        payload = {
            "name": f"Matter {ep.node_id}/{ep.endpoint}",
            "state_topic": topic_state(ep.node_id, ep.endpoint),
            "command_topic": f"matter/{ep.node_id}/{ep.endpoint}/set",
            "availability_topic": topic_available(ep.node_id, ep.endpoint),
            "payload_on": "ON",
            "payload_off": "OFF",
            "unique_id": f"matter_{ep.node_id}_{ep.endpoint}",
            "device": {
                "identifiers": [f"matter_node_{ep.node_id}"],
                "name": f"Matter Node {ep.node_id}",
                "manufacturer": "Matter",
                "model": "OnOff Device",
            },
        }

        self.mqtt.publish_retained(
            ha_discovery_topic(ep.node_id, ep.endpoint),
            json.dumps(payload),
        )
        logger.debug(f"Home Assistant discovery published for node {ep.node_id} endpoint {ep.endpoint}")

    async def periodic_refresh_task(self):
        """Periodically refresh snapshot from Matter."""
        while self.running:
            await asyncio.sleep(SNAPSHOT_REFRESH_INTERVAL)
            if not self.running:
                break
            try:
                await self.refresh_snapshot()
            except Exception as e:
                if not self.running:
                    break
                # Ignore shutdown/closing transport noise
                msg = str(e)
                if "closing transport" in msg or "Cannot write to closing transport" in msg:
                    logger.debug("WebSocket closing during shutdown")
                    break
                logger.error(f"Snapshot refresh error: {e}", exc_info=True)

    async def refresh_snapshot(self):
        """Refresh snapshot and publish state."""
        nodes = await self.matter_ws.snapshot_nodes()
        # Extract onoff endpoints for all nodes
        endpoints: List[EndpointInfo] = []
        for n in nodes:
            endpoints.extend(extract_onoff_endpoints_from_node(n))

        if not endpoints:
            logger.debug("No OnOff endpoints found")
            return

        for ep in endpoints:
            self.publish_ha_discovery(ep)

        # Publish availability + state
        for ep in endpoints:
            key = (ep.node_id, ep.endpoint)

            # availability
            if self.last_avail.get(key) != ep.available:
                self.last_avail[key] = ep.available
                avail_value = "true" if ep.available else "false"
                self.mqtt.publish_retained(topic_available(ep.node_id, ep.endpoint), avail_value)
                logger.info(
                    f"Published availability for node {ep.node_id} endpoint {ep.endpoint}: {avail_value}"
                )

            # state
            if ep.onoff is not None and self.last_state.get(key) != ep.onoff:
                self.last_state[key] = ep.onoff
                state_value = "ON" if ep.onoff else "OFF"
                self.mqtt.publish_retained(topic_state(ep.node_id, ep.endpoint), state_value)
                logger.info(
                    f"Published state for node {ep.node_id} endpoint {ep.endpoint}: {state_value}"
                )

        # Log discovered endpoints
        discovered = ", ".join([f"{e.node_id}/{e.endpoint}" for e in endpoints])
        logger.info(f"Discovered OnOff endpoints: {discovered}")

    async def command_consumer_task(self):
        """Consume commands from MQTT queue and send to Matter."""
        while self.running:
            cmd = await self.cmd_queue.get()
            logger.info(f"Processing MQTT command: node {cmd.node_id} endpoint {cmd.endpoint} action {cmd.action}")

            # Optimistically publish desired state immediately (optional)
            if cmd.action in ("on", "off"):
                desired = True if cmd.action == "on" else False
                state_value = "ON" if desired else "OFF"
                self.mqtt.publish_retained(topic_state(cmd.node_id, cmd.endpoint), state_value)

            # Send to Matter
            try:
                if self.matter_cmd.client is None:
                    raise RuntimeError("Matter commander not connected. Commands unavailable.")

                await self.matter_cmd.set_onoff(cmd.node_id, cmd.endpoint, cmd.action, timeout=15.0)
                logger.info(f"Command sent successfully to node {cmd.node_id} endpoint {cmd.endpoint}")
                # Refresh snapshot to confirm real state
                await asyncio.sleep(1)
                await self.refresh_snapshot()

            except Exception as e:
                logger.error(
                    f"Failed to send command to node {cmd.node_id} endpoint {cmd.endpoint}: {e}",
                    exc_info=True,
                )
