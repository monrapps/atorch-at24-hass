"""BLE coordinator for Atorch AT24 Energy Meter."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bleak import BleakClient, BleakGATTCharacteristic
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CHARACTERISTIC_UUID,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    NOTIFICATION_TIMEOUT_S,
    RECONNECT_INTERVAL_S,
    SERVICE_UUID,
)
from .parser import AtorchMeterData, parse_notification

_LOGGER = logging.getLogger(__name__)

CHUNK_FIRST_SIZE = 20
CHUNK_TOTAL_SIZE = 36


class AtorchBLECoordinator(DataUpdateCoordinator[AtorchMeterData | None]):
    """Coordinator that manages BLE connection and data from Atorch AT24."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        update_interval: int = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{address}",
        )
        self.address = address
        self._device_name = name
        self._update_interval = update_interval
        self._last_update_time: float = 0
        self._client: BleakClient | None = None
        self._buffer: bytearray = bytearray()
        self._expected_disconnect = False
        self._connect_lock = asyncio.Lock()
        self._watchdog_task: asyncio.Task | None = None
        self._last_data_time: float = 0

    @property
    def update_interval_seconds(self) -> int:
        """Return the current state update interval."""
        return self._update_interval

    @update_interval_seconds.setter
    def update_interval_seconds(self, value: int) -> None:
        """Set the state update interval."""
        self._update_interval = value

    @property
    def device_name(self) -> str:
        """Return the device name."""
        return self._device_name

    @property
    def connected(self) -> bool:
        """Return whether the BLE client is connected."""
        return self._client is not None and self._client.is_connected

    async def async_start(self) -> None:
        """Start the coordinator: connect and subscribe."""
        await self._connect()
        self._start_watchdog()

    async def async_stop(self) -> None:
        """Stop the coordinator: disconnect and cleanup."""
        self._expected_disconnect = True
        self._stop_watchdog()
        await self._disconnect()

    async def _connect(self) -> None:
        """Establish BLE connection and subscribe to notifications."""
        async with self._connect_lock:
            if self.connected:
                return

            device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if device is None:
                _LOGGER.warning(
                    "Device %s not found via Bluetooth", self.address
                )
                return

            try:
                self._client = await establish_connection(
                    BleakClient,
                    device,
                    self.address,
                    disconnected_callback=self._on_disconnect,
                )
                self._buffer.clear()
                await self._client.start_notify(
                    CHARACTERISTIC_UUID, self._on_notification
                )
                self._last_data_time = asyncio.get_event_loop().time()
                _LOGGER.info(
                    "Connected to Atorch AT24 at %s", self.address
                )
            except Exception:
                _LOGGER.exception(
                    "Failed to connect to %s", self.address
                )
                self._client = None

    async def _disconnect(self) -> None:
        """Disconnect from the BLE device."""
        if self._client and self._client.is_connected:
            try:
                await self._client.stop_notify(CHARACTERISTIC_UUID)
            except Exception:
                _LOGGER.debug("Error stopping notifications", exc_info=True)
            try:
                await self._client.disconnect()
            except Exception:
                _LOGGER.debug("Error disconnecting", exc_info=True)
        self._client = None

    @callback
    def _on_disconnect(self, client: BleakClient) -> None:
        """Handle unexpected disconnection."""
        _LOGGER.warning("Disconnected from %s", self.address)
        self._client = None
        if not self._expected_disconnect:
            self.hass.async_create_task(self._reconnect())

    async def _reconnect(self) -> None:
        """Attempt to reconnect after a delay."""
        await asyncio.sleep(RECONNECT_INTERVAL_S)
        _LOGGER.info("Attempting to reconnect to %s", self.address)
        await self._connect()

    def _on_notification(
        self,
        _characteristic: BleakGATTCharacteristic,
        data: bytearray,
    ) -> None:
        """Handle incoming BLE notification chunks.

        The device splits 36-byte payloads into 20 + 16 byte chunks.
        The first chunk starts with FF 55.
        """
        if len(data) >= 2 and data[0] == 0xFF and data[1] == 0x55:
            # Start of a new notification
            self._buffer = bytearray(data)
        else:
            # Continuation chunk
            self._buffer.extend(data)

        if len(self._buffer) >= CHUNK_TOTAL_SIZE:
            parsed = parse_notification(bytes(self._buffer[:CHUNK_TOTAL_SIZE]))
            self._buffer.clear()

            if parsed is not None:
                now = asyncio.get_event_loop().time()
                self._last_data_time = now
                # Always keep latest data available
                self.data = parsed
                # Only push state update if enough time has passed
                if (
                    self._update_interval <= 0
                    or now - self._last_update_time >= self._update_interval
                ):
                    self._last_update_time = now
                    self.async_set_updated_data(parsed)

    async def _update_method(self) -> AtorchMeterData | None:
        """Not used — data arrives via BLE notifications."""
        return self.data

    def _start_watchdog(self) -> None:
        """Start a watchdog that reconnects if data stops arriving."""
        if self._watchdog_task is not None:
            return
        self._watchdog_task = self.hass.async_create_task(
            self._watchdog_loop()
        )

    def _stop_watchdog(self) -> None:
        """Stop the watchdog task."""
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            self._watchdog_task = None

    async def _watchdog_loop(self) -> None:
        """Periodically check if data is still arriving."""
        try:
            while True:
                await asyncio.sleep(NOTIFICATION_TIMEOUT_S)
                if not self.connected:
                    _LOGGER.debug("Watchdog: not connected, attempting reconnect")
                    await self._connect()
                elif self._last_data_time > 0:
                    now = asyncio.get_event_loop().time()
                    elapsed = now - self._last_data_time
                    if elapsed > NOTIFICATION_TIMEOUT_S:
                        _LOGGER.warning(
                            "Watchdog: no data for %.0fs, reconnecting",
                            elapsed,
                        )
                        await self._disconnect()
                        await asyncio.sleep(RECONNECT_INTERVAL_S)
                        await self._connect()
        except asyncio.CancelledError:
            pass

    async def async_send_command(
        self,
        command_bytes: bytes,
    ) -> bool:
        """Send a command to the device via BLE write."""
        if not self.connected or self._client is None:
            _LOGGER.warning("Cannot send command: not connected")
            return False

        try:
            await self._client.write_gatt_char(
                CHARACTERISTIC_UUID,
                command_bytes,
                response=False,
            )
            return True
        except Exception:
            _LOGGER.exception("Failed to send command")
            return False
