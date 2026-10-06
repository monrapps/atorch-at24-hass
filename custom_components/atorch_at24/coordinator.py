"""BLE coordinator for Atorch AT24 Energy Meter."""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient, BleakGATTCharacteristic
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CHARACTERISTIC_UUID,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    NOTIFICATION_TIMEOUT_S,
    RECONNECT_INTERVAL_S,
)
from .parser import AtorchMeterData, parse_notification

_LOGGER = logging.getLogger(__name__)

CHUNK_TOTAL_SIZE = 36


class AtorchBLECoordinator(DataUpdateCoordinator[AtorchMeterData | None]):
    """Manage BLE sessions, sample freshness and throttled HA publication."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        update_interval: int = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        """Initialize the coordinator without an available sample."""
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_{address}")
        self.address = address
        self._device_name = name
        self._update_interval = update_interval
        self.last_update_success = False
        self._last_update_time: float | None = None
        self._last_data_time: float | None = None
        self._connected_since: float | None = None
        self._client: BleakClient | None = None
        self._buffer = bytearray()
        self._session_id = 0
        self._stopping = False
        self._expired = False
        self._connect_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._closing_tasks: dict[BleakClient, asyncio.Task] = {}
        self._expiry_handle: asyncio.TimerHandle | None = None
        self._publish_handle: asyncio.TimerHandle | None = None
        self._expiry_generation = 0
        self._publish_generation = 0

    @property
    def update_interval_seconds(self) -> int:
        """Return the current state update interval."""
        return self._update_interval

    @update_interval_seconds.setter
    def update_interval_seconds(self, value: int) -> None:
        """Recalculate a pending publication, never the sample deadline."""
        self._update_interval = value
        if self._publish_handle is not None:
            self._schedule_publish()

    @property
    def device_name(self) -> str:
        """Return the device name."""
        return self._device_name

    @property
    def connected(self) -> bool:
        """Return whether the current BLE client is connected."""
        return self._client is not None and self._client.is_connected

    @property
    def has_fresh_data(self) -> bool:
        """Check freshness even if the event loop has delayed the expiry timer."""
        return (
            not self._stopping
            and self.connected
            and self.data is not None
            and self._last_data_time is not None
            and asyncio.get_event_loop().time()
            < self._last_data_time + NOTIFICATION_TIMEOUT_S
        )

    def _cancel_expiry(self) -> None:
        self._expiry_generation += 1
        if self._expiry_handle is not None:
            self._expiry_handle.cancel()
            self._expiry_handle = None

    def _cancel_publish(self) -> None:
        self._publish_generation += 1
        if self._publish_handle is not None:
            self._publish_handle.cancel()
            self._publish_handle = None

    def _invalidate(self, reason: str) -> None:
        """Retain the snapshot, but invalidate it before notifying listeners."""
        self._last_data_time = None
        self._buffer.clear()
        self._cancel_expiry()
        self._cancel_publish()
        # HA notifies only on the success -> error transition. Do not pre-clear it.
        self.async_set_update_error(UpdateFailed(reason))

    def _end_session(self, reason: str) -> BleakClient | None:
        """Fence queued BLE callbacks before any asynchronous cleanup."""
        client, self._client = self._client, None
        self._session_id += 1
        self._connected_since = None
        self._expired = False
        self._invalidate(reason)
        return client

    async def async_start(self) -> None:
        """Start the coordinator: connect and subscribe."""
        await self._connect()
        if not self._stopping and self._watchdog_task is None:
            self._watchdog_task = self.hass.async_create_task(self._watchdog_loop())

    async def async_stop(self) -> None:
        """Invalidate first, then cancel and drain all owned tasks and timers."""
        self._stopping = True
        client = self._end_session("BLE coordinator stopped")
        tasks = [task for task in (
            self._watchdog_task, self._reconnect_task, self._connect_task
        ) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._watchdog_task = self._reconnect_task = self._connect_task = None
        await self._close_client(client)
        # Reconnect may have been cancelled after detaching a client, during its
        # BLE cleanup. These shielded tasks still belong to us and must be drained.
        if self._closing_tasks:
            await asyncio.gather(*self._closing_tasks.values())
            self._closing_tasks.clear()

    async def _connect(self) -> None:
        """Share one tracked connection attempt, including during stop/unload."""
        if self._stopping or self.connected:
            return
        if self._connect_task is None or self._connect_task.done():
            self._connect_task = self.hass.async_create_task(self._async_connect())
        assert self._connect_task is not None
        await asyncio.shield(self._connect_task)

    async def _async_connect(self) -> None:
        self._session_id += 1
        session = self._session_id
        self._invalidate("Waiting for a current BLE sample")
        self._connected_since = None
        self._expired = False
        client = None
        subscribed = False
        try:
            device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if device is None:
                _LOGGER.warning("Device %s not found via Bluetooth", self.address)
                return
            client = await establish_connection(
                BleakClient,
                device,
                self.address,
                disconnected_callback=lambda c: self._on_disconnect(c, session),
            )
            if self._stopping or session != self._session_id:
                return
            self._client = client
            self._connected_since = asyncio.get_event_loop().time()
            # Install session metadata BEFORE subscribing: notifications can arrive
            # during start_notify. Never overwrite their timestamps afterwards.
            await client.start_notify(
                CHARACTERISTIC_UUID,
                lambda char, data: self._on_notification(char, data, session),
            )
            subscribed = (
                not self._stopping and session == self._session_id and self.connected
            )
            if subscribed:
                _LOGGER.info("Connected to Atorch AT24 at %s", self.address)
        except asyncio.CancelledError:
            if session == self._session_id:
                self._end_session("BLE connection cancelled")
            raise
        except Exception:
            if session == self._session_id:
                self._end_session("BLE connection failed")
            _LOGGER.exception("Failed to connect to %s", self.address)
        finally:
            if not subscribed:
                await self._close_client(client)

    async def _close_client(self, client: BleakClient | None) -> None:
        """Retain ownership across cancellation of the task requesting cleanup."""
        if client is None:
            return
        task = self._closing_tasks.get(client)
        if task is None:
            if not client.is_connected:
                return
            task = self.hass.async_create_task(self._async_close_client(client))
            self._closing_tasks[client] = task
        try:
            await asyncio.shield(task)
        finally:
            if task.done():
                self._closing_tasks.pop(client, None)

    async def _async_close_client(self, client: BleakClient) -> None:
        """Close a detached client; its callbacks cannot affect the live session."""
        if client.is_connected:
            try:
                await client.stop_notify(CHARACTERISTIC_UUID)
            except Exception:
                _LOGGER.debug("Error stopping notifications", exc_info=True)
            try:
                await client.disconnect()
            except Exception:
                _LOGGER.debug("Error disconnecting", exc_info=True)

    async def _disconnect(self) -> None:
        """Invalidate synchronously before starting transport cleanup."""
        client = self._end_session("BLE disconnected")
        await self._close_client(client)

    @callback
    def _on_disconnect(self, client: BleakClient, session: int) -> None:
        """Ignore old clients and publish loss before scheduling recovery."""
        if self._stopping or session != self._session_id or client is not self._client:
            return
        _LOGGER.warning("Disconnected from %s", self.address)
        self._end_session("BLE disconnected")
        self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if not self._stopping and (
            self._reconnect_task is None or self._reconnect_task.done()
        ):
            self._reconnect_task = self.hass.async_create_task(self._reconnect())

    def _transport_stale(self) -> bool:
        since = self._last_data_time
        if since is None:
            since = self._connected_since
        return self._expired or (
            since is not None
            and asyncio.get_event_loop().time() >= since + NOTIFICATION_TIMEOUT_S
        )

    async def _reconnect(self) -> None:
        """Recheck before disconnecting: a new sample may have recovered the link."""
        if self._stopping:
            return
        if self.connected:
            if not self._transport_stale():
                return
            await self._disconnect()
        await asyncio.sleep(RECONNECT_INTERVAL_S)
        await self._connect()

    def _arm_expiry(self) -> None:
        assert self._last_data_time is not None
        self._cancel_expiry()
        self._expiry_handle = asyncio.get_event_loop().call_at(
            self._last_data_time + NOTIFICATION_TIMEOUT_S,
            self._expire, self._session_id, self._expiry_generation,
        )

    @callback
    def _expire(self, session: int, generation: int) -> None:
        if (
            self._stopping or session != self._session_id
            or generation != self._expiry_generation
        ):
            return
        if self.has_fresh_data:
            # call_at may fire slightly early; use the original exact deadline.
            self._arm_expiry()
            return
        self._expired = True
        self._invalidate("BLE sample expired")

    def _publish(self) -> None:
        self._cancel_publish()
        self._last_update_time = asyncio.get_event_loop().time()
        self.async_set_updated_data(self.data)

    def _schedule_publish(self) -> None:
        self._cancel_publish()
        if not self.has_fresh_data:
            self._expired = True
            self._invalidate("BLE sample expired")
            return
        if not self.last_update_success:
            # A refresh/coordinator error is recovered by a new frame, not a flush.
            return
        assert self._last_update_time is not None
        deadline = self._last_update_time + self._update_interval
        if asyncio.get_event_loop().time() >= deadline:
            self._publish()
        else:
            self._publish_handle = asyncio.get_event_loop().call_at(
                deadline, self._flush, self._session_id, self._publish_generation,
            )

    @callback
    def _flush(self, session: int, generation: int) -> None:
        if (
            self._stopping or session != self._session_id
            or generation != self._publish_generation
        ):
            return
        # Reschedule an early callback or invalidate stale data. Publication never
        # renews sample time; expiry wins even if this callback runs first.
        self._schedule_publish()

    def _on_notification(
        self,
        _characteristic: BleakGATTCharacteristic,
        data: bytearray,
        session: int,
    ) -> None:
        """Reassemble 20+16 byte chunks only within the active BLE session."""
        if self._stopping or session != self._session_id or not self.connected:
            return
        if data[:2] == b"\xff\x55":
            self._buffer = bytearray(data)
        else:
            self._buffer.extend(data)
        if len(self._buffer) < CHUNK_TOTAL_SIZE:
            return
        parsed = parse_notification(bytes(self._buffer[:CHUNK_TOTAL_SIZE]))
        self._buffer.clear()
        if parsed is None:
            return

        was_fresh = self.has_fresh_data and self.last_update_success
        mode_changed = self.data is not None and self.data.mode != parsed.mode
        now = asyncio.get_event_loop().time()
        self._last_data_time = now
        self._expired = False
        self.data = parsed
        self._arm_expiry()
        if (
            not was_fresh or mode_changed or self._last_update_time is None
            or now >= self._last_update_time + self._update_interval
        ):
            self._publish()
        else:
            self._schedule_publish()

    async def _update_method(self) -> AtorchMeterData | None:
        """Not a HA refresh hook — data arrives via BLE notifications."""
        return self.data

    def _watchdog_check(self) -> None:
        """Transport recovery is independent of the exact sample TTL timer."""
        if not self._stopping and (not self.connected or self._transport_stale()):
            self._schedule_reconnect()

    async def _watchdog_loop(self) -> None:
        try:
            while not self._stopping:
                await asyncio.sleep(NOTIFICATION_TIMEOUT_S)
                self._watchdog_check()
        except asyncio.CancelledError:
            pass

    async def async_send_command(self, command_bytes: bytes) -> bool:
        """Refuse commands based on a retained/expired device mode."""
        if not self.has_fresh_data or not self.last_update_success:
            _LOGGER.warning("Cannot send command: no current BLE sample")
            return False
        assert self._client is not None
        try:
            await self._client.write_gatt_char(
                CHARACTERISTIC_UUID, command_bytes, response=False,
            )
            return True
        except Exception:
            _LOGGER.exception("Failed to send command")
            return False
