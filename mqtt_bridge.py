"""MQTT bridge implementation."""

import asyncio
import logging
from typing import Optional

import paho.mqtt.client as mqtt

from constants import (
    MQTT_CMD_TOPIC_PATTERN,
    MQTT_KEEPALIVE,
    MQTT_QOS,
)
from models import MqttCommand

logger = logging.getLogger(__name__)

# These will be set after config is loaded in matter2mqtt_app
MQTT_HOST = "localhost"
MQTT_PORT = 1883


class MqttBridge:
    """Bridge between MQTT and asyncio event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop, cmd_queue: asyncio.Queue[MqttCommand]):
        self.loop = loop
        self.cmd_queue = cmd_queue
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def connect(self):
        """Connect to MQTT broker."""
        try:
            self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=MQTT_KEEPALIVE)
            self.client.loop_start()
            logger.info(f"Connected to MQTT broker at {MQTT_HOST}:{MQTT_PORT}")
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            raise

    def close(self):
        """Close MQTT connection."""
        try:
            self.client.loop_stop()
        finally:
            self.client.disconnect()

    def publish_retained(self, topic: str, payload: str):
        """Publish a retained message."""
        # retained makes Domoticz / others see last state after restart
        self.client.publish(topic, payload=payload, qos=MQTT_QOS, retain=True)
        logger.debug(f"Published to {topic}: {payload}")

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        """Handle MQTT connection."""
        if reason_code == 0:
            logger.info("Connected to MQTT broker successfully")
        else:
            logger.warning(f"Connected to MQTT broker with code {reason_code}")
        client.subscribe(MQTT_CMD_TOPIC_PATTERN, qos=MQTT_QOS)
        logger.info(f"Subscribed to: {MQTT_CMD_TOPIC_PATTERN}")

    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT message."""
        try:
            topic = msg.topic  # matter/<node>/<ep>/set
            payload = (msg.payload or b"").decode("utf-8", errors="replace").strip().upper()

            parts = topic.split("/")
            if len(parts) != 4 or parts[0] != "matter" or parts[3] != "set":
                logger.debug(f"Ignoring malformed topic: {topic}")
                return

            node_id = int(parts[1])
            endpoint = int(parts[2])

            if payload in ("ON", "1", "TRUE"):
                action = "on"
            elif payload in ("OFF", "0", "FALSE"):
                action = "off"
            elif payload in ("TOGGLE", "T"):
                action = "toggle"
            else:
                logger.warning(f"Unknown payload '{payload}' on {topic}")
                return

            logger.info(f"Received command from MQTT: node {node_id} endpoint {endpoint} action {action}")
            cmd = MqttCommand(node_id=node_id, endpoint=endpoint, action=action)
            # push into asyncio loop safely from MQTT thread
            self.loop.call_soon_threadsafe(self.cmd_queue.put_nowait, cmd)

        except Exception as e:
            logger.error(f"Error handling MQTT message: {e}", exc_info=True)
