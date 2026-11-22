"""Main Matter2MQTT bridge application."""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import yaml

from constants import (
    DEFAULT_CONFIG_FILE,
    SNAPSHOT_REFRESH_INTERVAL,
)
from models import EndpointInfo, MqttCommand
import mqtt_bridge as mqtt_bridge_mod
from mqtt_bridge import MqttBridge
from matter_ws import MatterWS
from matter_commander import MatterCommander
from matter_helpers import extract_onoff_endpoints_from_node
from home_assistant import build_discovery_payload
from topics import topic_state, topic_available

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
mqtt_bridge_mod.MQTT_HOST = str(config['mqtt']['host'])
mqtt_bridge_mod.MQTT_PORT = int(config['mqtt']['port'])
MQTT_DISCOVERY_PREFIX = str(config.get("mqtt", {}).get("discovery_prefix", "homeassistant"))
COMMANDS_ENABLED = config.get("matter_ws", {}).get("commands_enabled", False)
if not isinstance(COMMANDS_ENABLED, bool):
    raise ValueError("matter_ws.commands_enabled must be a YAML boolean")


class Matter2MQTT:
    """Main bridge application."""

    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.cmd_queue: asyncio.Queue[MqttCommand] = asyncio.Queue()

        self.commands_enabled = COMMANDS_ENABLED
        self.mqtt = MqttBridge(self.loop, self.cmd_queue, commands_enabled=self.commands_enabled)
        self.matter_ws = MatterWS(MATTER_WS_URL)
        self.matter_cmd = MatterCommander(MATTER_WS_URL) if self.commands_enabled else None

        # Track last published states to avoid spamming
        self.last_state: Dict[Tuple[int, int], Optional[bool]] = {}
        self.last_avail: Dict[Tuple[int, int], Optional[bool]] = {}
        self.last_mqtt_generation = 0

        self.running = True

    async def start(self):
        """Start the bridge."""
        await self.mqtt.connect()
        logger.info("MQTT bridge connected")

        hello = await self.matter_ws.connect()
        logger.info(
            f"Matter WebSocket connected. schema={hello.get('schema_version')} "
            f"sdk={hello.get('sdk_version')}"
        )

        if self.commands_enabled:
            await self.matter_cmd.connect()
            logger.info("Matter commander connected (can send OnOff commands)")
        else:
            logger.info("Status-only mode: Matter relay commands disabled")

        await self.refresh_snapshot()

        self._tasks = [
            asyncio.create_task(self.periodic_refresh_task(), name="refresh"),
        ]
        if self.commands_enabled:
            self._tasks.append(asyncio.create_task(self.command_consumer_task(), name="cmd_consumer"))

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
            if self.matter_cmd is not None:
                await self.matter_cmd.close()
        except Exception:
            pass

    def publish_ha_discovery(self, ep: EndpointInfo):
        """Publish Home Assistant discovery message."""
        discovery = build_discovery_payload(ep, MQTT_DISCOVERY_PREFIX)
        self.mqtt.publish_retained(
            discovery.topic,
            discovery.payload,
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
                logger.error("Snapshot refresh failed; retrying on next interval: %s", e)

    async def refresh_snapshot(self):
        """Refresh snapshot and publish state."""
        nodes = await self.matter_ws.snapshot_nodes()
        if not self.mqtt.connected.is_set():
            logger.warning("MQTT disconnected; snapshot will be published after reconnection")
            return
        generation = self.mqtt.connection_generation
        force_publish = generation != self.last_mqtt_generation
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
            if force_publish or self.last_avail.get(key) != ep.available:
                avail_value = "true" if ep.available else "false"
                self.mqtt.publish_retained(topic_available(ep.node_id, ep.endpoint), avail_value)
                self.last_avail[key] = ep.available
                logger.info(
                    f"Published availability for node {ep.node_id} endpoint {ep.endpoint}: {avail_value}"
                )

            # state
            if ep.onoff is not None and (force_publish or self.last_state.get(key) != ep.onoff):
                state_value = "ON" if ep.onoff else "OFF"
                self.mqtt.publish_retained(topic_state(ep.node_id, ep.endpoint), state_value)
                self.last_state[key] = ep.onoff
                logger.info(
                    f"Published state for node {ep.node_id} endpoint {ep.endpoint}: {state_value}"
                )

        self.last_mqtt_generation = generation
        # Log discovered endpoints
        discovered = ", ".join([f"{e.node_id}/{e.endpoint}" for e in endpoints])
        logger.info(f"Discovered OnOff endpoints: {discovered}")

    async def command_consumer_task(self):
        """Consume commands from MQTT queue and send to Matter."""
        if not self.commands_enabled:
            return
        while self.running:
            cmd = await self.cmd_queue.get()
            logger.info(f"Processing MQTT command: node {cmd.node_id} endpoint {cmd.endpoint} action {cmd.action}")

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
