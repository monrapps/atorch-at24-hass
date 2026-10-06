"""Deterministic availability policy P01-P37: real AT24 code, offline boundaries.

python -W error tests/test_ble_stale.py -v
ATORCH_SOURCE_ROOT=/path/to/baseline python -W error tests/test_ble_stale.py <case>
No real sleep, BLE, HA server, household configuration or frontend is used.
"""
import asyncio
import json
import sys
import unittest
from unittest.mock import patch

from ble_stale_doubles import Harness, UpdateFailed, pump


class AvailabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = self.enterContext(Harness(update_interval=60))
        self.c = self.h.coordinator

    async def asyncTearDown(self):
        await self.c.async_stop()
        # Also clean up untracked baseline tasks when running the negative control.
        for task in self.h.hass.tasks:
            task.cancel()
        await asyncio.gather(*self.h.hass.tasks, return_exceptions=True)

    async def sample(self, watts=100, mode=1):
        await self.c._connect()
        self.h.emit(watts, mode)

    def assert_unavailable(self, h=None):
        h = h or self.h
        self.assertEqual(len(h.sensors), 11)
        self.assertEqual(len(h.buttons), 3)
        for key, sensor in h.sensors.items():
            self.assertFalse(sensor.available, key)
            self.assertIsNone(sensor.native_value, key)
        for key, button in h.buttons.items():
            self.assertFalse(button.available, key)
        self.assertIn("Indisponível", h.projection())

    async def test_P01_initial_state(self):
        self.assert_unavailable()
        self.assertFalse(self.c.last_update_success)
        self.assertEqual(self.c.publications, 0)

    async def test_P02_connection_is_not_a_sample(self):
        await self.c._connect()
        self.assert_unavailable()
        self.assertIsNone(self.c._last_data_time)
        self.assertEqual(self.c._connected_since, 0)
        self.assertEqual(self.c.publications, 0)

    async def test_P03_first_sample_at_zero_bypasses_every_throttle(self):
        for interval in (0, 5, 60):
            with self.subTest(interval=interval):
                async with Harness(interval) as h:
                    await h.coordinator._connect()
                    h.emit()
                    self.assertEqual(h.coordinator._last_data_time, 0)
                    self.assertTrue(h.power.available)
                    self.assertEqual(h.coordinator.publications, 1)
                    self.assertIn("72,0 kWh/mês", h.projection())
                    self.assertEqual(h.coordinator._expiry_handle.when, 60)

    async def test_P04_disconnect_invalidates_before_reconnect(self):
        # t=1000 also lets the defective baseline publish its first frame at I=60.
        self.h.clock.now = 1000
        await self.sample()
        snapshot = self.c.data
        self.h.clock.now += 0.001
        self.h.clients[-1].drop()
        self.assert_unavailable()
        self.assertIs(self.c.data, snapshot)
        self.assertEqual(self.c.publications, 2)
        self.assertEqual(self.h.state_history, ["100.0", "unavailable"])
        self.assertEqual(len(self.h.clients), 1)  # no reconnect task has run yet

    async def test_P05_reconnection_without_frame_never_restores_cache(self):
        self.h.clock.now = 1000
        await self.sample()
        self.h.clients[-1].drop()
        await self.c._connect()
        await self.h.advance(1059)
        self.assertTrue(self.c.connected)
        self.assert_unavailable()
        self.assertIsNone(self.c._last_data_time)
        self.assertEqual(self.c.publications, 2)

    async def test_P06_recovery_inside_previous_throttle_publishes_once(self):
        self.h.clock.now = 1000
        await self.sample()
        self.h.clients[-1].drop()
        await self.c._connect()
        self.h.clock.now = 1001
        self.h.emit(200)
        self.assertEqual(self.h.published_state, "200.0")
        self.assertIn("144,0 kWh/mês", self.h.projection())
        self.assertEqual(self.c.publications, 3)
        self.assertEqual(self.c._expiry_handle.when, 1061)

    async def test_P07_partial_and_invalid_frames_do_not_recover(self):
        await self.sample()
        self.h.clients[-1].drop()
        await self.c._connect()
        notify = self.h.clients[-1].notification_callback
        for packet in (self.h.packet(notification_type=0x11),
                       self.h.packet(mode=4), b"\x00" * 36):
            notify(None, packet)
            self.assert_unavailable()
            self.assertIsNone(self.c._last_data_time)
        packet = self.h.packet(200)
        notify(None, packet[:20])
        self.assertIsNone(self.c._last_data_time)
        self.assertIsNone(self.c._expiry_handle)
        notify(None, packet[20:])
        self.assertTrue(self.h.power.available)
        self.assertEqual(self.h.published_state, "200.0")

    async def test_P08_exact_timeout_publishes_once_without_BLE(self):
        self.h.clock.now = 1000
        await self.sample()
        await self.h.advance(1059.999)
        self.assertTrue(self.h.power.available)
        await self.h.advance(1060)
        self.assert_unavailable()
        self.assertEqual(self.c.publications, 2)
        await self.h.advance(1060.001)
        self.assertEqual(self.c.publications, 2)

    async def test_P09_old_deadline_cannot_expire_a_new_sample(self):
        await self.sample()
        old = self.c._expiry_handle
        await self.h.advance(59.999)
        self.h.emit(200)
        self.h.clock.now = 60
        old.fire()  # emulate an already-queued, now cancelled callback
        self.assertTrue(self.h.power.available)
        self.assertAlmostEqual(self.c._expiry_handle.when, 119.999)
        await self.h.advance(119.998)
        self.assertTrue(self.h.power.available)
        await self.h.advance(119.999)
        self.assert_unavailable()

    async def test_P10_recovery_before_queued_watchdog_work_keeps_link(self):
        await self.sample()
        await self.h.advance(60)
        self.c._watchdog_check()
        recovery = self.c._reconnect_task
        self.h.emit(200)  # same session, before recovery task begins
        await pump()
        self.assertTrue(recovery.done())
        self.assertEqual(len(self.h.clients), 1)
        self.assertTrue(self.c.connected)
        self.assertEqual(self.h.published_state, "200.0")
        self.assertEqual(self.c._expiry_handle.when, 120)

    async def test_P11_timer_then_watchdog_does_not_republish_cache(self):
        await self.sample()
        await self.h.advance(60)
        self.assert_unavailable()
        self.c._watchdog_check()
        await pump()
        self.assertFalse(self.c.connected)
        await self.h.advance(65)
        self.assertTrue(self.c.connected)
        self.assertEqual(len(self.h.clients), 2)
        self.assert_unavailable()
        self.assertEqual(self.c.publications, 2)

    async def test_P12_normal_1s_cadence_for_180s_all_throttles(self):
        for interval in (0, 5, 60):
            with self.subTest(interval=interval):
                async with Harness(interval) as h:
                    await h.coordinator._connect()
                    h.emit()
                    for second in range(1, 181):
                        await h.advance(second)
                        h.emit()
                        self.assertTrue(h.power.available)
                    self.assertNotIn("unavailable", h.state_history)
                    self.assertEqual(h.coordinator._last_data_time, 180)
                    self.assertEqual(h.coordinator._expiry_handle.when, 240)

    async def test_P13_normal_variable_cadence_is_independent_of_throttle(self):
        for interval in (0, 5, 60):
            for gap in (1, 2.5, 5, 10, 59.999):
                with self.subTest(interval=interval, gap=gap):
                    async with Harness(interval) as h:
                        await h.coordinator._connect()
                        h.emit()
                        for _ in range(3):
                            await h.advance(h.clock.now + gap)
                            self.assertTrue(h.power.available)
                            h.emit()
                        self.assertNotIn("unavailable", h.state_history)
                        await h.advance(h.clock.now + 60)
                        self.assert_unavailable(h)

    async def test_P14_identical_frames_renew_TTL(self):
        await self.sample()
        for second in (10, 30, 50):
            await self.h.advance(second)
            self.h.emit()
        await self.h.advance(109.999)
        self.assertTrue(self.h.power.available)
        await self.h.advance(110)
        self.assert_unavailable()

    async def test_P15_junk_ACK_and_partial_traffic_do_not_renew_TTL(self):
        await self.sample()
        for second in (10, 20, 30, 40, 50):
            await self.h.advance(second)
            self.h.emit(notification_type=0x11)
            self.h.emit(mode=4)
            self.h.clients[-1].notification_callback(None, self.h.packet()[:20])
            self.assertEqual(self.c._last_data_time, 0)
        await self.h.advance(60)
        self.assert_unavailable()
        self.assertEqual(self.c._buffer, b"")

    async def test_P16_watchdog_recovers_connection_without_first_sample(self):
        await self.c.async_start()
        await pump()
        await self.h.advance(60)
        self.assert_unavailable()
        self.assertIsNone(self.c._last_data_time)
        self.assertFalse(self.c.connected)
        await self.h.advance(65)
        self.assertEqual(len(self.h.clients), 2)
        self.assertTrue(self.c.connected)
        self.assert_unavailable()
        self.assertEqual(self.c.publications, 0)

    async def test_P17_mode_changes_bypass_throttle_for_all_sensors(self):
        await self.c._connect()
        for publication, mode in enumerate((1, 3, 1), start=1):
            self.h.emit(mode=mode)
            self.assertEqual(self.c.publications, publication)
            for sensor in self.h.sensors.values():
                self.assertEqual(sensor.available,
                                 mode in sensor.entity_description.available_modes)
                if not sensor.available:
                    self.assertIsNone(sensor.native_value)
            self.assertTrue(all(b.available for b in self.h.buttons.values()))
            if mode == 3:
                self.assertIn("Indisponível", self.h.projection())
            else:
                self.assertIn("72,0 kWh/mês", self.h.projection())

    async def test_P18_disconnect_and_expiry_preserve_counters_in_all_modes(self):
        for mode in (1, 2, 3):
            for reason in ("disconnect", "expiry"):
                with self.subTest(mode=mode, reason=reason):
                    async with Harness() as h:
                        await h.coordinator._connect()
                        packet = h.packet(mode=mode)
                        packet[13:17] = (12345).to_bytes(4, "big")
                        h.clients[-1].emit(packet)
                        snapshot = h.coordinator.data
                        if reason == "disconnect":
                            h.clients[-1].drop()
                        else:
                            await h.advance(60)
                        self.assert_unavailable(h)
                        self.assertIs(h.coordinator.data, snapshot)
                        self.assertTrue(all(not available and value is None
                                            for available, value in h.entity_history[-1].values()))
                        await h.coordinator._connect()
                        h.clients[-1].emit(packet)
                        self.assertEqual(h.coordinator.data, snapshot)
                        # A real lower counter is accepted, not fabricated on loss.
                        packet[13:17] = (123).to_bytes(4, "big")
                        h.clients[-1].emit(packet)
                        self.assertEqual(h.coordinator.data, h.parser.parse_notification(packet))

    async def test_P19_zero_is_valid_and_invalid_template_inputs_are_not_zero(self):
        await self.sample(0)
        self.assertTrue(self.h.power.available)
        self.assertIn("0,0 kWh/mês", self.h.projection())
        for state in ("unknown", "unavailable", "text", "", "nan", "inf", "-inf"):
            self.h.published_state = state
            self.assertIn("Indisponível", self.h.projection())
            self.assertNotIn("kWh/mês", self.h.projection())

    async def test_P20_flush_latest_sample_without_another_frame(self):
        self.h.clock.now = 1000
        await self.sample()
        await self.h.advance(1059)
        self.h.emit(200)
        self.assertEqual(self.h.published_state, "100.0")
        await self.h.advance(1060)
        self.assertEqual(self.h.published_state, "200.0")
        self.assertIn("144,0 kWh/mês", self.h.projection())
        self.assertEqual(self.c._last_data_time, 1059)
        await self.h.advance(1119)
        self.assert_unavailable()

    async def test_P21_expiry_wins_over_flush_even_if_flush_runs_first(self):
        await self.sample()
        self.h.emit(200)  # same reception time -> flush and expiry both at 60
        flush = self.c._publish_handle
        expiry = self.c._expiry_handle
        self.h.clock.now = 60
        flush.fire()
        expiry.fire()
        self.assert_unavailable()
        self.assertEqual(self.c.publications, 2)
        self.assertEqual(self.h.state_history, ["100.0", "unavailable"])

    async def test_P22_cancelled_flush_cannot_publish_after_disconnect_or_stop(self):
        for stop in (False, True):
            async with Harness(60) as h:
                await h.coordinator._connect()
                h.emit()
                h.clock.now = 1
                h.emit(200)
                flush = h.coordinator._publish_handle
                expiry = h.coordinator._expiry_handle
                if stop:
                    await h.coordinator.async_stop()
                else:
                    h.clients[-1].drop()
                self.assertTrue(flush.cancelled)
                self.assertTrue(expiry.cancelled)
                h.clock.now = 60
                flush.fire()
                expiry.fire()
                self.assert_unavailable(h)
                self.assertEqual(h.coordinator.publications, 2)

    async def test_P23_simultaneous_frame_and_expiry_both_orders(self):
        for frame_first in (True, False):
            async with Harness(60) as h:
                await h.coordinator._connect()
                h.emit()
                expiry = h.coordinator._expiry_handle
                h.clock.now = 60
                if frame_first:
                    h.emit(200)
                    expiry.fire()
                    self.assertEqual(h.state_history, ["100.0", "200.0"])
                else:
                    expiry.fire()
                    h.emit(200)
                    self.assertEqual(h.state_history, ["100.0", "unavailable", "200.0"])
                self.assertTrue(h.power.available)
                self.assertEqual(h.coordinator._expiry_handle.when, 120)

    async def test_P24_early_timer_rearms_and_late_loop_cannot_keep_availability(self):
        await self.sample()
        early = self.c._expiry_handle
        self.h.clock.now = 59.999
        early.fire()
        self.assertTrue(self.h.power.available)
        self.assertEqual(self.c._expiry_handle.when, 60)
        delayed = self.c._expiry_handle
        self.h.clock.now = 60
        for entity in (*self.h.sensors.values(), *self.h.buttons.values()):
            self.assertFalse(entity.available)
        for sensor in self.h.sensors.values():
            self.assertIsNone(sensor.native_value)
        self.assertEqual(self.h.published_state, "100.0")  # loop has not run yet
        self.h.clock.now = 65
        delayed.fire()
        self.assert_unavailable()
        self.assertEqual(self.c.publications, 2)

    async def test_P25_chunks_cannot_cross_sessions(self):
        await self.c._connect()
        packet = self.h.packet()
        old_notify = self.h.clients[-1].notification_callback
        old_notify(None, packet[:20])
        self.h.clients[-1].drop()
        await self.c._connect()
        old_notify(None, packet[20:])
        self.assert_unavailable()
        self.assertEqual(self.c._buffer, b"")
        # Even a continuation delivered by the new client cannot use old prefix.
        self.h.clients[-1].notification_callback(None, packet[20:])
        self.assertIsNone(self.c.data)
        self.h.emit(200)
        self.assertEqual(self.h.published_state, "200.0")

    async def test_P26_old_client_callbacks_cannot_modify_current_session(self):
        await self.sample()
        old_client = self.h.clients[-1]
        old_notify = old_client.notification_callback
        old_client.drop()
        await self.c._connect()
        self.h.emit(200)
        snapshot = self.c.data
        timestamp = self.c._last_data_time
        publications = self.c.publications
        old_client.drop()
        old_notify(None, self.h.packet(300))
        self.assertIs(self.c._client, self.h.clients[-1])
        self.assertIs(self.c.data, snapshot)
        self.assertEqual(self.c._last_data_time, timestamp)
        self.assertEqual(self.c.publications, publications)
        self.assertEqual(self.h.published_state, "200.0")

    async def test_P27_frame_during_subscription_success_and_failure(self):
        for fail in (False, True):
            async with Harness(60) as h:
                async def notify_hook(client):
                    h.clock.now = 1
                    client.emit(h.packet())
                    h.clock.now = 2
                    if fail:
                        raise RuntimeError("synthetic subscribe failure")
                h.notify_hook = notify_hook
                await h.coordinator._connect()
                if fail:
                    self.assert_unavailable(h)
                    self.assertFalse(h.clients[-1].is_connected)
                    self.assertIsNone(h.coordinator._expiry_handle)
                    self.assertEqual(h.state_history, ["100.0", "unavailable"])
                else:
                    self.assertEqual(h.coordinator._last_data_time, 1)
                    self.assertEqual(h.coordinator._expiry_handle.when, 61)
                    self.assertEqual(h.state_history, ["100.0"])

    async def test_P28_single_reconnect_for_drop_watchdog_and_intentional_close(self):
        await self.sample()
        old = self.h.clients[-1]
        old.drop()
        task = self.c._reconnect_task
        self.c._watchdog_check()
        old.drop()
        self.assertIs(self.c._reconnect_task, task)
        await pump()
        self.assertEqual(self.h.clock.sleeps, [5])
        await self.h.advance(5)
        self.assertEqual(len(self.h.clients), 2)
        await self.h.advance(65)  # connected but no data
        self.c._watchdog_check()
        task = self.c._reconnect_task
        await pump()  # intentional close must not create another task
        self.c._watchdog_check()
        self.assertIs(self.c._reconnect_task, task)
        await self.h.advance(70)
        self.assertEqual(len(self.h.clients), 3)
        self.assertEqual(self.c.publications, 2)

    async def test_P29_stop_drains_tasks_and_handles_and_is_idempotent(self):
        for pending_reconnect in (False, True):
            async with Harness(60) as h:
                await h.coordinator.async_start()
                h.emit()
                h.clock.now = 1
                h.emit(200)
                callbacks = list(h.clock.active)
                if pending_reconnect:
                    h.clients[-1].drop()
                await pump()
                await h.coordinator.async_stop()
                await h.coordinator.async_stop()
                self.assertTrue(all(t.done() for t in h.hass.tasks))
                self.assertFalse(h.clock.active)
                self.assertIsNone(h.coordinator._expiry_handle)
                self.assertIsNone(h.coordinator._publish_handle)
                clients = len(h.clients)
                publications = h.coordinator.publications
                for timer in callbacks:
                    # Only integration callbacks, not cancelled Future setters.
                    if getattr(timer.callback, "__self__", None) is h.coordinator:
                        timer.fire()
                await h.advance(1000)
                self.assertEqual(len(h.clients), clients)
                self.assertEqual(h.coordinator.publications, publications)
                self.assert_unavailable(h)
                await h.coordinator.async_start()
                self.assertEqual(len(h.clients), clients)
                self.assertFalse(h.clock.active)

    async def test_P29b_stop_waits_for_inflight_transport_cleanup(self):
        for operation in ("stop_notify", "disconnect"):
            async with Harness(60) as h:
                await h.coordinator.async_start()
                h.emit()
                client = h.clients[-1]
                original = getattr(client, operation)
                entered = asyncio.Event()
                release = asyncio.Event()

                async def delayed_close(*args):
                    entered.set()
                    await release.wait()
                    await original(*args)

                setattr(client, operation, delayed_close)
                await h.advance(60)  # watchdog detaches client and starts closing
                await entered.wait()
                stopping = asyncio.create_task(h.coordinator.async_stop())
                try:
                    await pump()
                    self.assertFalse(stopping.done(), "stop abandoned pending BLE close")
                    self.assert_unavailable(h)
                finally:
                    release.set()
                    await stopping
                self.assertFalse(client.is_connected)
                self.assertEqual(client.close_count, 1)
                self.assertTrue(all(t.done() for t in h.hass.tasks))
                self.assertFalse(h.clock.active)

    async def test_P30_stop_while_connecting_closes_late_returned_client(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        async def delayed_connection(client):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                # Simulate a connector returning a client despite cancellation.
                await release.wait()
        self.h.establish_hook = delayed_connection
        starting = asyncio.create_task(self.c.async_start())
        await entered.wait()
        stopping = asyncio.create_task(self.c.async_stop())
        await pump()
        self.assert_unavailable()
        self.assertFalse(stopping.done())
        release.set()
        await asyncio.gather(starting, stopping)
        self.assertFalse(self.h.clients[-1].is_connected)
        self.assertIsNone(self.h.clients[-1].notification_callback)
        self.assertFalse(self.c.connected)
        self.assertTrue(all(t.done() for t in self.h.hass.tasks))
        self.assertFalse(self.h.clock.active)
        self.assertEqual(self.c.publications, 0)

    async def test_P31_connect_and_subscribe_failures_then_valid_recovery(self):
        for failure in ("establish", "subscribe"):
            async with Harness() as h:
                async def fail(client):
                    # An establish failure owns its own not-yet-returned client.
                    if failure == "establish":
                        client.is_connected = False
                    raise RuntimeError("synthetic connection failure")
                if failure == "establish":
                    h.establish_hook = fail
                else:
                    h.notify_hook = fail
                await h.coordinator._connect()
                self.assert_unavailable(h)
                self.assertFalse(h.coordinator.connected)
                self.assertFalse(h.clients[-1].is_connected)
                h.establish_hook = h.notify_hook = None
                await h.coordinator._connect()
                self.assert_unavailable(h)
                h.emit(200)
                self.assertEqual(h.published_state, "200.0")

    async def test_P32_option_change_recalculates_flush_not_TTL(self):
        await self.sample()
        self.h.clock.now = 1
        self.h.emit(200)
        expiry = self.c._expiry_handle
        original = self.c._publish_handle
        self.c.update_interval_seconds = 5
        self.assertTrue(original.cancelled)
        self.assertEqual(self.c._publish_handle.when, 5)
        self.assertIs(self.c._expiry_handle, expiry)
        self.c.update_interval_seconds = 0
        self.assertIsNone(self.c._publish_handle)
        self.assertEqual(self.h.published_state, "200.0")
        self.assertEqual(self.c._last_data_time, 1)
        self.assertIs(self.c._expiry_handle, expiry)
        self.c.update_interval_seconds = 60
        self.h.emit(300)
        self.h.clock.now = 61  # delayed loop, pending data is no longer usable
        self.c.update_interval_seconds = 0
        self.assert_unavailable()
        self.assertNotIn("300.0", self.h.state_history)

    async def test_P33_buttons_and_direct_send_require_current_mode(self):
        for phase in ("initial", "connected", "fresh", "expired", "recovered", "disconnected"):
            if phase == "connected":
                await self.c._connect()
            elif phase in ("fresh", "recovered"):
                self.h.emit(mode=2)
            elif phase == "expired":
                self.h.clock.now = 60  # no expiry callback yet
            elif phase == "disconnected":
                self.h.clients[-1].drop()
            count = sum(len(c.writes) for c in self.h.clients)
            for button in self.h.buttons.values():
                await button.async_press()
            direct = await self.c.async_send_command(b"synthetic-direct")
            fresh = phase in ("fresh", "recovered")
            self.assertEqual(direct, fresh)
            writes = [write for client in self.h.clients for write in client.writes]
            self.assertEqual(len(writes) - count, 4 if fresh else 0)
            if fresh:
                for button, (_, command, response) in zip(self.h.buttons.values(), writes[-4:]):
                    desc = button.entity_description
                    self.assertEqual(command, self.h.parser.build_command(
                        adu=2, a2=desc.command_a2, a3=desc.command_a3,
                        a4=desc.command_a4, a5=desc.command_a5))
                    self.assertFalse(response)

    async def test_P34_coordinator_error_is_respected_and_idempotent(self):
        self.h.clock.now = 1000
        await self.sample()
        self.c.async_set_update_error(UpdateFailed("synthetic error"))
        self.assert_unavailable()
        self.c.async_set_update_error(UpdateFailed("same error"))
        self.assertEqual(self.c.publications, 2)
        self.h.emit(200)
        self.assertEqual(self.c.publications, 3)
        self.assertEqual(self.h.published_state, "200.0")

    async def test_P35_wall_clock_changes_do_not_affect_monotonic_deadlines(self):
        with patch("time.time", return_value=-1_000_000):
            await self.sample()
        with patch("time.time", return_value=1_000_000_000_000):
            await self.h.advance(59.999)
            self.assertTrue(self.h.power.available)
        with patch("time.time", return_value=0):
            await self.h.advance(60)
            self.assert_unavailable()

    async def test_P36_superseded_flush_is_inert_within_same_BLE_session(self):
        for superseding in ("mode", "option", "error_recovery"):
            async with Harness(60) as h:
                c = h.coordinator
                await c._connect()
                h.emit()
                h.clock.now = 1
                h.emit(200)
                old = c._publish_handle
                if superseding == "mode":
                    h.emit(mode=3)
                    h.emit(300, mode=3)  # now pending for 61
                elif superseding == "option":
                    c.update_interval_seconds = 5  # now pending for 5
                else:
                    c.async_set_update_error(UpdateFailed("synthetic error"))
                    h.emit(300)
                    h.emit(400)  # now pending for 61
                current = c._publish_handle
                count = c.publications
                old.fire()
                self.assertEqual(c.publications, count)
                self.assertIs(c._publish_handle, current)
                # Keep sample fresh until new pending deadline (for I=60).
                if current.when > 5:
                    h.clock.now = 2
                    h.emit(500, mode=3 if superseding == "mode" else 1)
                await h.advance(current.when)
                self.assertEqual(c.publications, count + 1)
                self.assertIsNone(c._publish_handle)

    async def test_P37_actual_refresh_hook_cannot_restore_cache(self):
        await self.sample()
        self.h.clients[-1].drop()
        await self.c._connect()
        await self.c.async_request_refresh()  # HA public path, not _update_method
        self.assert_unavailable()
        self.assertIsNone(self.c._last_data_time)
        self.h.emit(200)
        self.assertEqual(self.h.published_state, "200.0")
        self.assertTrue(self.c.last_update_success)
        self.assertEqual(self.c.publications, 3)
        # Refresh error while a flush is pending must not be undone by the flush.
        self.h.clock.now = 1
        self.h.emit(300)
        await self.c.async_request_refresh()
        await self.h.advance(60)
        self.assert_unavailable()
        self.h.emit(400)
        self.assertEqual(self.h.published_state, "400.0")


async def trace():
    async with Harness(60) as h:
        rows = [h.snapshot("startup")]
        await h.coordinator._connect()
        rows.append(h.snapshot("connected_no_sample"))
        h.emit()
        rows.append(h.snapshot("valid_100_w"))
        h.clients[-1].drop()
        rows.append(h.snapshot("disconnected"))
        await h.advance(5)
        rows.append(h.snapshot("reconnected_no_sample"))
        h.emit(200)
        rows.append(h.snapshot("reconnected_valid_200_w"))
        await h.advance(65)
        rows.append(h.snapshot("expired"))
        h.emit(0)
        rows.append(h.snapshot("valid_zero_w"))
        return rows


if __name__ == "__main__":
    if sys.argv[1:] == ["--trace"]:
        print(json.dumps(asyncio.run(trace()), ensure_ascii=False, indent=2))
    else:
        unittest.main()
