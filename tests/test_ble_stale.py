"""Characterize the existing stale-data defect; passing is NOT a fix.

Run: python tests/test_ble_stale.py -v
Trace: python tests/test_ble_stale.py --trace

No physical BLE, HA server, network, real sleep or household configuration.
"""
import asyncio
import json
import sys
import unittest

from ble_stale_doubles import Harness


class StaleAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = self.enterContext(Harness())

    async def connect_and_sample(self, watts=100):
        await self.h.coordinator._connect()
        self.h.emit(watts)
        self.assertTrue(self.h.power.available)
        self.assertEqual(self.h.power.native_value, watts)
        self.assertEqual(self.h.coordinator.publications, 1)

    async def drop_and_reconnect(self):
        self.h.clients[-1].drop()
        self.assertFalse(self.h.coordinator.connected)
        self.assertEqual(len(self.h.hass.tasks), 1)
        await self.h.hass.tasks[0].run()
        self.assertTrue(self.h.coordinator.connected)
        self.assertEqual(self.h.clock.sleeps, [self.h.const.RECONNECT_INTERVAL_S])

    async def test_startup_without_data_is_unavailable_even_when_connected(self):
        for connected in (False, True):
            if connected:
                await self.h.coordinator._connect()
            self.assertEqual(self.h.coordinator.connected, connected)
            self.assertFalse(self.h.power.available)
            self.assertIsNone(self.h.power.native_value)
            self.assertEqual(self.h.coordinator.publications, 0)
            self.assertIn("Indisponível", self.h.projection())

    async def test_disconnect_keeps_all_previously_available_sensors_and_projection(self):
        await self.connect_and_sample()
        before = {key: (sensor.available, sensor.native_value)
                  for key, sensor in self.h.sensors.items()}
        data = self.h.coordinator.data
        self.h.clients[-1].drop()
        self.assertFalse(self.h.coordinator.connected)
        self.assertIs(self.h.coordinator.data, data)
        self.assertEqual(before, {key: (sensor.available, sensor.native_value)
                                  for key, sensor in self.h.sensors.items()})
        self.assertTrue(self.h.power.available)
        self.assertEqual(self.h.power.native_value, 100)
        self.assertEqual(self.h.coordinator.publications, 1)
        self.assertEqual(len(self.h.hass.tasks), 1)
        self.assertEqual(self.h.state_history, ["100.0"])
        self.assertIn("72,0 kWh/mês", self.h.projection())

    async def test_reconnect_without_new_sample_keeps_stale_data_and_resets_timer(self):
        await self.connect_and_sample()
        data = self.h.coordinator.data
        old_time = self.h.coordinator._last_data_time
        await self.drop_and_reconnect()
        self.assertIs(self.h.coordinator.data, data)
        self.assertGreater(self.h.coordinator._last_data_time, old_time)
        self.assertTrue(self.h.power.available)
        self.assertEqual(self.h.power.native_value, 100)
        self.assertEqual(self.h.coordinator.publications, 1)
        self.assertIn("72,0 kWh/mês", self.h.projection())

    async def test_new_valid_sample_after_reconnect_replaces_retained_power(self):
        await self.connect_and_sample()
        data = self.h.coordinator.data
        await self.drop_and_reconnect()
        self.h.emit(200)
        self.assertIsNot(self.h.coordinator.data, data)
        self.assertTrue(self.h.power.available)
        self.assertEqual(self.h.power.native_value, 200)
        self.assertEqual(self.h.coordinator.publications, 2)
        self.assertEqual(self.h.state_history, ["100.0", "200.0"])
        self.assertIn("144,0 kWh/mês", self.h.projection())

    async def test_invalid_packet_after_reconnect_does_not_replace_data(self):
        await self.connect_and_sample()
        data = self.h.coordinator.data
        await self.drop_and_reconnect()
        last_time = self.h.coordinator._last_data_time
        self.h.clock.now += 1
        self.h.emit(200, notification_type=0x11)
        self.assertIs(self.h.coordinator.data, data)
        self.assertEqual(self.h.coordinator._last_data_time, last_time)
        self.assertEqual(self.h.coordinator.publications, 1)
        self.assertTrue(self.h.power.available)
        self.assertIn("72,0 kWh/mês", self.h.projection())

    async def test_mode_change_makes_power_unavailable_not_zero(self):
        await self.connect_and_sample()
        await self.drop_and_reconnect()
        self.h.emit(mode=self.h.const.MODE_DC)
        self.assertFalse(self.h.power.available)
        self.assertIsNone(self.h.power.native_value)
        self.assertEqual(self.h.published_state, "unavailable")
        self.assertIn("Indisponível", self.h.projection())
        self.assertNotIn("0,0 kWh/mês", self.h.projection())

    async def test_available_override_ignores_coordinator_failure_flag(self):
        await self.connect_and_sample()
        self.h.coordinator.last_update_success = False
        self.assertTrue(self.h.power.available)
        self.assertEqual(self.h.power.native_value, 100)

    async def test_watchdog_at_exact_timeout_does_not_reconnect(self):
        await self.connect_and_sample()
        self.h.clock.sleep_budget = 1
        await self.h.coordinator._watchdog_loop()
        self.assertEqual(self.h.clock.sleeps, [self.h.const.NOTIFICATION_TIMEOUT_S])
        self.assertEqual(len(self.h.clients), 1)
        self.assertTrue(self.h.power.available)
        self.assertEqual(self.h.coordinator.publications, 1)

    async def test_watchdog_after_timeout_reconnects_but_keeps_stale_power(self):
        await self.connect_and_sample()
        self.h.clock.now += 1
        self.h.clock.sleep_budget = 2
        await self.h.coordinator._watchdog_loop()
        self.assertEqual(self.h.clock.sleeps, [self.h.const.NOTIFICATION_TIMEOUT_S,
                                            self.h.const.RECONNECT_INTERVAL_S])
        self.assertEqual(len(self.h.clients), 2)
        self.assertFalse(self.h.clients[0].is_connected)
        self.assertTrue(self.h.coordinator.connected)
        self.assertTrue(self.h.power.available)
        self.assertEqual(self.h.power.native_value, 100)
        self.assertEqual(self.h.coordinator.publications, 1)
        self.assertIn("72,0 kWh/mês", self.h.projection())


class ThrottledAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_sample_can_change_native_value_without_publishing(self):
        with Harness(update_interval=60) as h:
            await h.coordinator._connect()
            h.emit(100)
            first_update = h.coordinator._last_update_time
            h.clients[-1].drop()
            await h.hass.tasks[0].run()
            h.emit(200)
            self.assertTrue(h.power.available)
            self.assertEqual(h.power.native_value, 200)
            self.assertEqual(h.coordinator.publications, 1)
            self.assertEqual(h.published_state, "100.0")
            self.assertIn("72,0 kWh/mês", h.projection())
            h.clock.now = first_update + 60
            h.emit(300)
            self.assertEqual(h.coordinator.publications, 2)
            self.assertEqual(h.published_state, "300.0")
            self.assertIn("216,0 kWh/mês", h.projection())


class ProjectionControlTests(unittest.TestCase):
    def test_non_numeric_and_nonfinite_published_states_are_unavailable(self):
        with Harness() as h:
            for state in ("unknown", "unavailable", "text", "", "nan", "inf", "-inf"):
                with self.subTest(state=state):
                    h.published_state = state
                    self.assertIn("Indisponível", h.projection())
                    self.assertNotIn("kWh/mês", h.projection())
            h.published_state = "0"
            self.assertIn("0,0 kWh/mês", h.projection())


async def trace():
    with Harness() as h:
        rows = [h.snapshot("startup")]
        await h.coordinator._connect()
        rows.append(h.snapshot("connected_no_sample"))
        h.emit(100)
        rows.append(h.snapshot("valid_100_w"))
        h.clients[-1].drop()
        rows.append(h.snapshot("disconnected"))
        await h.hass.tasks[0].run()
        rows.append(h.snapshot("reconnected_no_sample"))
        h.emit(200)
        rows.append(h.snapshot("reconnected_valid_200_w"))
        return rows


if __name__ == "__main__":
    if sys.argv[1:] == ["--trace"]:
        print(json.dumps(asyncio.run(trace()), ensure_ascii=False, indent=2))
    else:
        unittest.main()
