"""Regression checks use local fakes only; they never send relay commands."""

import asyncio
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock

from aiohttp import web

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matter_ws import MatterWS
from mqtt_bridge import MqttBridge

# App configuration is loaded at import time. Use an isolated fixture so a clean
# checkout needs no local config and tests never read deployment settings.
with tempfile.TemporaryDirectory() as config_directory:
    pathlib.Path(config_directory, "matter2mqtt.yaml").write_text(
        "mqtt: {host: localhost, port: 1883}\n"
        "matter_ws: {url: 'ws://localhost:5580/ws', commands_enabled: false}\n",
        encoding="utf-8",
    )
    previous_directory = os.getcwd()
    try:
        os.chdir(config_directory)
        from matter2mqtt_app import Matter2MQTT
    finally:
        os.chdir(previous_directory)


NODES = [
    {"node_id": n, "available": n != 4, "attributes": {"1/6/0": False}}
    for n in (3, 4, 5)
]


class LifecycleTests(unittest.TestCase):
    def test_startup_failure_exits_nonzero_after_cleanup(self):
        entry = pathlib.Path(__file__).resolve().parents[1] / "matter2mqtt.py"
        script = '''
import sys, types, runpy
class FailedApp:
    async def start(self):
        raise ConnectionRefusedError("fake broker not ready")
    async def stop(self):
        print("CLEANUP_COMPLETED")
sys.modules["matter2mqtt_app"] = types.SimpleNamespace(Matter2MQTT=FailedApp)
runpy.run_path(sys.argv[1], run_name="__main__")
'''
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(entry)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CLEANUP_COMPLETED", result.stdout)
        self.assertIn("fake broker not ready", result.stderr)
        self.assertNotIn("Task exception was never retrieved", result.stderr)


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.commands = []
        self.connections = 0
        self.drop_next = False
        self.error_next = False

        async def handler(request):
            self.connections += 1
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"schema_version": 11})
            async for message in ws:
                frame = message.json()
                self.commands.append(frame["command"])
                if self.drop_next:
                    self.drop_next = False
                    await ws.close()
                    break
                await ws.send_json({"event": "attribute_updated", "data": []})
                await ws.send_json({"message_id": "unrelated", "result": []})
                if self.error_next:
                    self.error_next = False
                    await ws.send_json({"message_id": frame["message_id"], "error_code": 99, "details": "fake error"})
                else:
                    await ws.send_json({"message_id": frame["message_id"], "result": NODES})
            return ws

        app = web.Application()
        app.router.add_get("/ws", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.client = MatterWS(f"http://127.0.0.1:{port}/ws")

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()

    async def test_repeated_and_concurrent_snapshots_match_response_ids(self):
        results = await asyncio.gather(*(self.client.snapshot_nodes() for _ in range(3)))
        self.assertEqual(results, [NODES, NODES, NODES])
        self.assertEqual(self.commands, ["get_nodes"] * 3)
        self.assertEqual(self.connections, 1)

    async def test_closed_connection_is_reestablished_on_next_snapshot(self):
        self.drop_next = True
        with self.assertRaises(ConnectionError):
            await self.client.snapshot_nodes()
        self.assertEqual(await self.client.snapshot_nodes(), NODES)
        self.assertEqual(self.connections, 2)

    async def test_server_error_is_not_treated_as_an_empty_snapshot(self):
        self.error_next = True
        with self.assertRaisesRegex(RuntimeError, "fake error"):
            await self.client.snapshot_nodes()
        self.assertEqual(await self.client.snapshot_nodes(), NODES)


class FakeMQTT:
    def __init__(self):
        self.connected = asyncio.Event()
        self.connected.set()
        self.connection_generation = 1
        self.messages = []
        self.reject_states = False

    def publish_retained(self, topic, payload):
        if self.reject_states and topic.endswith("/state"):
            raise ConnectionError("fake publication failure")
        self.messages.append((topic, payload))


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = Matter2MQTT()
        self.app.mqtt = FakeMQTT()

        async def snapshot():
            return NODES

        self.app.matter_ws = types.SimpleNamespace(snapshot_nodes=snapshot)

    async def test_reconnect_republishes_all_nodes_even_when_states_are_unchanged(self):
        await self.app.refresh_snapshot()
        first = self.app.mqtt.messages.copy()
        self.assertEqual(len(first), 9)
        self.assertIn(("matter/4/1/available", "false"), first)
        await self.app.refresh_snapshot()
        self.assertEqual(len(self.app.mqtt.messages), 12)  # Only discovery repeats.
        self.app.mqtt.connection_generation += 1
        await self.app.refresh_snapshot()
        self.assertEqual(self.app.mqtt.messages[-9:], first)
        self.assertFalse(any(topic.endswith("/set") for topic, _ in self.app.mqtt.messages))

    async def test_failed_publication_does_not_mark_state_as_published(self):
        self.app.mqtt.reject_states = True
        with self.assertRaises(ConnectionError):
            await self.app.refresh_snapshot()
        self.assertNotIn((3, 1), self.app.last_state)
        self.assertEqual(self.app.last_mqtt_generation, 0)
        self.app.mqtt.reject_states = False
        await self.app.refresh_snapshot()
        self.assertIn(("matter/3/1/state", "OFF"), self.app.mqtt.messages)

    async def test_status_only_mode_does_not_construct_a_commander(self):
        self.assertFalse(self.app.commands_enabled)
        self.assertIsNone(self.app.matter_cmd)
        await self.app.command_consumer_task()


class CommandGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_only_mode_never_subscribes_or_enqueues_commands(self):
        queue = asyncio.Queue()
        bridge = MqttBridge(asyncio.get_running_loop(), queue, commands_enabled=False)
        client = Mock()
        bridge._on_connect(client, None, None, 0, None)
        bridge._on_message(client, None, types.SimpleNamespace(
            topic="matter/3/1/set", payload=b"ON", retain=False,
        ))
        await asyncio.sleep(0)
        client.subscribe.assert_not_called()
        self.assertTrue(queue.empty())
        self.assertEqual(bridge.connection_generation, 1)

    async def test_retained_commands_are_ignored_even_when_control_is_enabled(self):
        queue = asyncio.Queue()
        bridge = MqttBridge(asyncio.get_running_loop(), queue, commands_enabled=True)
        bridge._on_message(None, None, types.SimpleNamespace(
            topic="matter/3/1/set", payload=b"ON", retain=True,
        ))
        await asyncio.sleep(0)
        self.assertTrue(queue.empty())


if __name__ == "__main__":
    unittest.main()
