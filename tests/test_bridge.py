"""Regression checks use local fakes only; they never send relay commands."""

import asyncio
import contextlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matter_ws import MatterWS
from matter_commander import MatterCommander
from endpoint_mapping import EndpointMap
from models import EndpointInfo, MqttCommand
from mqtt_bridge import MqttBridge
from electrical_measurements import extract_electrical_endpoints, build_measurement_discovery

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


def metered_node(*, available=True, attributes=None):
    return {
        'node_id': 6, 'available': available,
        'attributes': attributes if attributes is not None else {
            '1/6/0': False, '1/144/4': 230125, '1/144/5': 2345,
            '1/144/8': 500125, '1/145/1': {'0': 12345678},
        },
    }


class ElectricalParsingTests(unittest.TestCase):
    def test_standard_units_are_converted_to_watts_volts_amps_and_kwh(self):
        ep, = extract_electrical_endpoints(metered_node())
        self.assertEqual(ep.values, {
            'power': 500.125, 'voltage': 230.125, 'current': 2.345,
            'energy_imported': 12.345678,
        })
        self.assertNotIn('energy_exported', ep.values)

    def test_zero_is_a_real_measurement_and_negative_power_is_preserved(self):
        ep, = extract_electrical_endpoints(metered_node(attributes={
            '1/144/8': -150000, '1/144/5': 0, '1/145/1': {'0': 0},
        }))
        self.assertEqual(ep.values, {'power': -150.0, 'current': 0.0, 'energy_imported': 0.0})

    def test_current_plugs_without_measurement_clusters_create_no_sensors(self):
        self.assertEqual(extract_electrical_endpoints(metered_node(attributes={
            '1/6/0': True, '1/29/1': [3,4,6,29], '0/54/4': -40,
        })), [])

    def test_null_and_invalid_values_are_unavailable_and_not_zero(self):
        for value in (None, True, '123', float('nan'), float('inf'), {}, 10**400):
            with self.subTest(value=type(value).__name__):
                ep, = extract_electrical_endpoints(metered_node(attributes={'1/144/8':value}))
                self.assertIsNone(ep.values['power'])

    def test_missing_supported_attribute_is_unavailable(self):
        ep, = extract_electrical_endpoints(metered_node(attributes={'1/144/65531':[8,65531]}))
        self.assertEqual(ep.values, {'power':None})

    def test_energy_structs_and_exported_energy(self):
        for field in ('0', 0, 'energy', 'Energy'):
            with self.subTest(field=field):
                ep, = extract_electrical_endpoints(metered_node(attributes={'1/145/2':{field:2500000}}))
                self.assertEqual(ep.values, {'energy_exported':2.5})
        ep, = extract_electrical_endpoints(metered_node(attributes={'1/145/1':{'0':-1}}))
        self.assertIsNone(ep.values['energy_imported'])

    def test_multiple_measurement_endpoints_do_not_mix_their_values(self):
        result = extract_electrical_endpoints(metered_node(attributes={
            '1/144/8':1000, '2/144/8':2000, '2/145/1':{'0':3000000}, 'bad':0,
        }))
        self.assertEqual([(e.endpoint,e.values) for e in result], [
            (1,{'power':1.0}), (2,{'power':2.0,'energy_imported':3.0}),
        ])

    def test_discovery_has_units_availability_and_no_command_topic(self):
        ep, = extract_electrical_endpoints(metered_node())
        for metric, unit in [('power','W'),('voltage','V'),('current','A'),('energy_imported','kWh')]:
            d=build_measurement_discovery(ep,metric,'custom')
            payload=json.loads(d.payload)
            self.assertEqual(d.topic,f'custom/sensor/matter_6_1_{metric}/config')
            self.assertEqual(payload['unit_of_measurement'],unit)
            self.assertEqual(payload['unique_id'],f'matter_6_1_{metric}')
            self.assertNotIn('command_topic',payload)
            self.assertEqual(payload['availability_topic'],f'matter/6/1/{metric}/available')
            self.assertEqual(payload['state_class'],'total_increasing' if metric.startswith('energy') else 'measurement')


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
        self.frames = []
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
                self.frames.append(frame)
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
                    result = NODES
                    if frame["command"] == "device_command":
                        result = None
                    elif frame["command"] == "read_attribute":
                        result = {frame["args"]["attribute_path"]: True}
                    await ws.send_json({"message_id": frame["message_id"], "result": result})
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

    async def test_onoff_json_matches_server_api_and_reads_back_state(self):
        commander = MatterCommander(self.client.url)
        try:
            for action, name in [("on", "On"), ("off", "Off"), ("toggle", "Toggle")]:
                await commander.set_onoff(3, 1, action)
                self.assertEqual(self.frames[-1]["command"], "device_command")
                self.assertEqual(self.frames[-1]["args"], {
                    "node_id": 3, "endpoint_id": 1, "cluster_id": 6,
                    "command_name": name, "payload": {},
                })
            self.assertTrue(await commander.read_onoff(3, 1))
            self.assertEqual(self.frames[-1]["args"], {
                "node_id": 3, "attribute_path": "1/6/0",
            })
        finally:
            await commander.close()

    async def test_uncertain_toggle_is_not_replayed_after_connection_drop(self):
        commander = MatterCommander(self.client.url)
        self.drop_next = True
        try:
            with self.assertRaises(ConnectionError):
                await commander.set_onoff(3, 1, "toggle")
            self.assertEqual(self.commands, ["device_command"])
            self.assertEqual(self.connections, 1)
            # The next independent read can reconnect, without resending Toggle.
            self.assertTrue(await commander.read_onoff(3, 1))
            self.assertEqual(self.commands, ["device_command", "read_attribute"])
        finally:
            await commander.close()

    async def test_invalid_action_never_opens_a_connection(self):
        commander = MatterCommander(self.client.url)
        try:
            with self.assertRaises(ValueError):
                await commander.set_onoff(3, 1, "invalid")
            self.assertEqual(self.connections, 0)
        finally:
            await commander.close()

    async def test_command_connections_do_not_remain_idle_between_user_actions(self):
        commander = MatterCommander(self.client.url)
        try:
            await commander.connect()
            self.assertIsNone(commander.client.ws)
            self.assertEqual(self.commands, [])
            await commander.set_onoff(3, 1, "off")
            self.assertIsNone(commander.client.ws)
            await commander.read_onoff(3, 1)
            self.assertIsNone(commander.client.ws)
            self.assertEqual(self.connections, 3)
            self.assertEqual(self.commands, ["device_command", "read_attribute"])
        finally:
            await commander.close()


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

    async def electrical_snapshot(self, node):
        async def snapshot():
            return [node]
        self.app.matter_ws.snapshot_nodes = snapshot
        await self.app.refresh_snapshot()

    async def test_measurements_keep_legacy_identity_without_adding_relay(self):
        self.app.endpoint_map = EndpointMap({'6/1':'1/1'})
        await self.electrical_snapshot(metered_node())
        published = dict(self.app.mqtt.messages)
        self.assertEqual(published['matter/1/1/power/state'],'500.125')
        self.assertEqual(published['matter/1/1/energy_imported/state'],'12.345678')
        self.assertEqual(json.loads(published['homeassistant/sensor/matter_1_1_power/config'])['unique_id'],'matter_1_1_power')
        self.assertIn('homeassistant/light/matter_1_1/config',published)
        self.assertFalse(any('/6/1/' in topic or topic.endswith('/set') for topic in published))

    async def test_measurement_reconnect_republishes_unchanged_values(self):
        await self.electrical_snapshot(metered_node())
        first = self.app.mqtt.messages.copy()
        self.app.mqtt.messages.clear()
        await self.app.refresh_snapshot()
        # Stable measurements still need fresh publications so Domoticz does not
        # time out a correctly reporting zero-watt plug after an idle hour.
        published=dict(self.app.mqtt.messages)
        self.assertEqual(published['matter/6/1/power/state'],'500.125')
        self.assertEqual(published['matter/6/1/voltage/state'],'230.125')
        self.assertEqual(published['matter/6/1/current/state'],'2.345')
        self.assertEqual(published['matter/6/1/energy_imported/state'],'12.345678')
        self.app.mqtt.messages.clear()
        self.app.mqtt.connection_generation += 1
        await self.app.refresh_snapshot()
        self.assertEqual(self.app.mqtt.messages,first)

    async def test_null_measurement_marks_unavailable_without_false_zero(self):
        await self.electrical_snapshot(metered_node(attributes={'1/6/0':False,'1/144/8':None}))
        published=dict(self.app.mqtt.messages)
        self.assertEqual(published['matter/6/1/power/available'],'false')
        self.assertNotIn('matter/6/1/power/state',published)

    async def test_measurement_on_unavailable_node_does_not_publish_cached_value(self):
        await self.electrical_snapshot(metered_node(available=False))
        published=dict(self.app.mqtt.messages)
        self.assertEqual(published['matter/6/1/power/available'],'false')
        self.assertNotIn('matter/6/1/power/state',published)

    async def test_missing_value_recovery_publishes_again_even_if_unchanged(self):
        await self.electrical_snapshot(metered_node())
        await self.electrical_snapshot(metered_node(attributes={'1/6/0':False,'1/144/8':None}))
        self.app.mqtt.messages.clear()
        await self.electrical_snapshot(metered_node())
        self.assertIn(('matter/6/1/power/state','500.125'),self.app.mqtt.messages)
        self.assertIn(('matter/6/1/power/available','true'),self.app.mqtt.messages)

    async def test_electrical_endpoint_without_relay_is_supported(self):
        await self.electrical_snapshot(metered_node(attributes={'2/144/8':1000}))
        self.assertIn(('matter/6/2/power/state','1.0'),self.app.mqtt.messages)
        self.assertFalse(any('/light/' in topic for topic,_ in self.app.mqtt.messages))

    async def test_failed_measurement_publish_is_retried_on_next_refresh(self):
        # A measurement-only node reaches the measurement state publication before
        # FakeMQTT rejects it. No value may be remembered as successfully sent.
        self.app.mqtt.reject_states=True
        with self.assertRaises(ConnectionError):
            await self.electrical_snapshot(metered_node(attributes={'2/144/8':1000}))
        self.assertEqual(self.app.last_measurements,{})
        self.assertEqual(self.app.last_mqtt_generation,0)
        self.app.mqtt.reject_states=False
        await self.app.refresh_snapshot()
        self.assertIn(('matter/6/2/power/state','1.0'),self.app.mqtt.messages)

    async def test_status_only_mode_does_not_construct_a_commander(self):
        self.assertFalse(self.app.commands_enabled)
        self.assertIsNone(self.app.matter_cmd)
        await self.app.command_consumer_task()

    async def test_failed_command_restores_reported_state_without_optimistic_on(self):
        self.app.commands_enabled = True
        self.app.matter_cmd = types.SimpleNamespace(
            set_onoff=AsyncMock(side_effect=RuntimeError("fake device error")),
            read_onoff=AsyncMock(),
        )
        # The known physical state is already OFF; it must be republished anyway.
        self.app.last_state = {(n, 1): False for n in (3, 4, 5)}
        self.app.last_mqtt_generation = 1
        task = asyncio.create_task(self.app.command_consumer_task())
        try:
            with self.assertLogs("matter2mqtt_app", level="ERROR"):
                await self.app.cmd_queue.put(MqttCommand(3, 1, "on"))
                await asyncio.wait_for(self.app.cmd_queue.join(), 2)
            self.assertIn(("matter/3/1/state", "OFF"), self.app.mqtt.messages)
            self.assertNotIn(("matter/3/1/state", "ON"), self.app.mqtt.messages)
            self.app.matter_cmd.read_onoff.assert_not_awaited()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_successful_command_reads_back_before_publishing(self):
        self.app.commands_enabled = True
        self.app.matter_cmd = types.SimpleNamespace(
            set_onoff=AsyncMock(), read_onoff=AsyncMock(return_value=False),
        )
        task = asyncio.create_task(self.app.command_consumer_task())
        try:
            await self.app.cmd_queue.put(MqttCommand(3, 1, "off"))
            await asyncio.wait_for(self.app.cmd_queue.join(), 2)
            self.app.matter_cmd.set_onoff.assert_awaited_once_with(3, 1, "off", timeout=15.0)
            self.app.matter_cmd.read_onoff.assert_awaited_once_with(3, 1)
            self.assertIn(("matter/3/1/state", "OFF"), self.app.mqtt.messages)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_recommissioned_endpoint_keeps_legacy_discovery_and_state_identity(self):
        self.app.endpoint_map = EndpointMap({"6/1": "1/1"})
        physical_nodes = NODES + [{"node_id": 6, "available": True, "attributes": {"1/6/0": True}}]

        async def snapshot():
            return physical_nodes

        self.app.matter_ws.snapshot_nodes = snapshot
        await self.app.refresh_snapshot()
        published = dict(self.app.mqtt.messages)
        discovery = json.loads(published["homeassistant/light/matter_1_1/config"])
        self.assertEqual(discovery["unique_id"], "matter_1_1")
        self.assertEqual(discovery["command_topic"], "matter/1/1/set")
        self.assertEqual(published["matter/1/1/state"], "ON")
        self.assertEqual(published["matter/1/1/available"], "true")
        self.assertNotIn("homeassistant/light/matter_6_1/config", published)
        self.assertNotIn("matter/6/1/state", published)
        self.assertIn("matter/3/1/state", published)

    async def test_legacy_ui_command_routes_to_new_physical_endpoint(self):
        self.app.endpoint_map = EndpointMap({"6/1": "1/1"})
        self.app.commands_enabled = True
        self.app.matter_cmd = types.SimpleNamespace(
            set_onoff=AsyncMock(), read_onoff=AsyncMock(return_value=False),
        )
        task = asyncio.create_task(self.app.command_consumer_task())
        try:
            await self.app.cmd_queue.put(MqttCommand(1, 1, "off"))
            await asyncio.wait_for(self.app.cmd_queue.join(), 2)
            self.app.matter_cmd.set_onoff.assert_awaited_once_with(6, 1, "off", timeout=15.0)
            self.app.matter_cmd.read_onoff.assert_awaited_once_with(6, 1)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


class EndpointMappingTests(unittest.TestCase):
    def test_no_mapping_preserves_the_existing_endpoints(self):
        endpoint = EndpointInfo(3, 1, True, False)
        mapping = EndpointMap()
        self.assertEqual(mapping.mqtt_snapshot([endpoint]), [endpoint])
        self.assertEqual(mapping.matter_target(3, 1), (3, 1))

    def test_duplicate_logical_identities_are_rejected(self):
        with self.assertRaises(ValueError):
            EndpointMap({"6/1": "1/1", "7/1": "1/1"})

    def test_alias_cannot_take_over_a_live_unmapped_device(self):
        mapping = EndpointMap({"6/1": "1/1"})
        with self.assertRaises(ValueError):
            mapping.mqtt_snapshot([EndpointInfo(1, 1, True, False)])

    def test_malformed_mapping_is_rejected_before_startup(self):
        for value in [["6/1"], {"node/1": "1/1"}, {"6/1": "0/1"}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                EndpointMap(value)


class CommandGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_environment_credentials_are_used(self):
        client = Mock()
        with patch.dict(os.environ, {'MQTT_USERNAME': 'fixture-user', 'MQTT_PASSWORD': 'fixture-password'}), patch('mqtt_bridge.mqtt.Client', return_value=client):
            MqttBridge(asyncio.get_running_loop(), asyncio.Queue())
        client.username_pw_set.assert_called_once_with('fixture-user', 'fixture-password')

    async def test_missing_password_fails_before_connection(self):
        with patch.dict(os.environ, {'MQTT_USERNAME': 'fixture-user', 'MQTT_PASSWORD': ''}), self.assertRaises(ValueError):
            MqttBridge(asyncio.get_running_loop(), asyncio.Queue())

    async def test_no_credentials_preserves_public_development_broker_support(self):
        client = Mock()
        with patch.dict(os.environ, {'MQTT_USERNAME': '', 'MQTT_PASSWORD': ''}), patch('mqtt_bridge.mqtt.Client', return_value=client):
            MqttBridge(asyncio.get_running_loop(), asyncio.Queue())
        client.username_pw_set.assert_not_called()

    async def test_enabled_mode_subscribes_and_enqueues_nonretained_ui_commands(self):
        queue = asyncio.Queue()
        bridge = MqttBridge(asyncio.get_running_loop(), queue, commands_enabled=True)
        client = Mock()
        bridge._on_connect(client, None, None, 0, None)
        bridge._on_message(client, None, types.SimpleNamespace(
            topic="matter/4/1/set", payload=b"OFF", retain=False,
        ))
        await asyncio.sleep(0)
        client.subscribe.assert_called_once_with("matter/+/+/set", qos=1)
        self.assertEqual(await asyncio.wait_for(queue.get(), 1), MqttCommand(4, 1, "off"))

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
