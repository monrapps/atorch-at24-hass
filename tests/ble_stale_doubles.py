"""Offline boundary doubles; the AT24 coordinator/parser/sensors remain real.

Not an HA runtime or a physical BLE test. Only the small push/listener contract
used by this reproduction is modeled. Imports are isolated from other tests.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from dataclasses import dataclass
import importlib.util
import math
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(os.environ.get("ATORCH_SOURCE_ROOT", ROOT))


class Timer:
    """Keep callable/args so tests can execute an already-queued cancelled callback."""
    def __init__(self, when, callback, args):
        self.when = when
        self.callback = callback
        self.args = args
        self.cancelled = False
        self.fired = False

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.fired = True
        self.callback(*self.args)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
        self.timers = []

    def time(self):
        return self.now

    def call_at(self, when, callback, *args):
        timer = Timer(when, callback, args)
        self.timers.append(timer)
        return timer

    @property
    def active(self):
        return [t for t in self.timers if not t.cancelled and not t.fired]

    def advance(self, target):
        assert target >= self.now
        while due := [t for t in self.active if t.when <= target]:
            timer = min(due, key=lambda t: t.when)
            self.now = max(self.now, timer.when)
            timer.fire()
        self.now = target

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        future = asyncio.get_running_loop().create_future()
        handle = self.call_at(self.now + seconds, future.set_result, None)
        try:
            await future
        finally:
            handle.cancel()


async def pump():
    """Yield tasks/futures without sleeping in real time."""
    for _ in range(12):
        await asyncio.sleep(0)


class HassDouble:
    def __init__(self):
        self.tasks = []

    def async_create_task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task


class ClientDouble:
    def __init__(self, disconnected_callback):
        self.is_connected = True
        self.disconnected_callback = disconnected_callback
        self.notification_callback = None
        self.notify_hook = None
        self.writes = []
        self.close_count = 0

    async def start_notify(self, uuid, callback):
        self.notification_callback = callback
        if self.notify_hook is not None:
            await self.notify_hook(self)

    async def write_gatt_char(self, uuid, command, *, response):
        self.writes.append((uuid, command, response))

    async def stop_notify(self, uuid):
        self.notification_callback = None

    def drop(self):
        self.is_connected = False
        self.disconnected_callback(self)

    async def disconnect(self):
        self.close_count += 1
        self.drop()

    def emit(self, packet):
        assert self.is_connected and self.notification_callback is not None
        self.notification_callback(None, bytearray(packet[:20]))
        self.notification_callback(None, bytearray(packet[20:]))


class DataUpdateCoordinatorDouble:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, *, name):
        self.hass = hass
        self.data = None
        self.last_update_success = True
        self.listeners = []
        self.publications = 0

    def async_add_listener(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback)

    def async_set_updated_data(self, data):
        self.data = data
        self.last_update_success = True
        self.async_update_listeners()

    def async_set_update_error(self, error):
        self.last_exception = error
        was_successful = self.last_update_success
        self.last_update_success = False
        if was_successful:
            self.async_update_listeners()

    async def _async_update_data(self):
        # HA's actual default hook does not call a subclass's _update_method.
        raise NotImplementedError("push-only coordinator has no update_method")

    async def async_request_refresh(self):
        # HA 2026.9.4 _async_refresh re-raises NotImplementedError BEFORE its
        # listener block. Do not turn that path into async_set_update_error:
        # last_update_success can be false while listeners still hold a value.
        try:
            data = await self._async_update_data()
        except NotImplementedError as error:
            self.last_exception = error
            self.last_update_success = False
            raise
        except Exception as error:
            self.async_set_update_error(error)
        else:
            self.async_set_updated_data(data)

    def async_update_listeners(self):
        self.publications += 1
        for listener in tuple(self.listeners):
            listener()


class CoordinatorEntityDouble:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return self.coordinator.last_update_success


@dataclass(frozen=True, kw_only=True)
class SensorDescriptionDouble:
    key: str
    translation_key: str
    native_unit_of_measurement: str | None
    state_class: str
    device_class: str | None = None
    suggested_display_precision: int | None = None
    icon: str | None = None


@dataclass(frozen=True, kw_only=True)
class ButtonDescriptionDouble:
    key: str
    translation_key: str
    icon: str


class UpdateFailed(Exception):
    pass


def module(name, **attributes):
    result = ModuleType(name)
    result.__path__ = []
    result.__dict__.update(attributes)
    return result


def labels(*names):
    return SimpleNamespace(**{name: name for name in names})


def is_number(value):
    """Finite-number guard used by the published template's offline tests."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


class Harness:
    def __init__(self, update_interval=0):
        self.update_interval = update_interval
        self.stack = ExitStack()
        self.clock = Clock()
        self.hass = HassDouble()
        self.clients = []
        self.published_state = "unavailable"
        self.state_history = []
        self.entity_history = []
        self.notify_hook = None
        self.establish_hook = None

    async def establish_connection(self, client_class, device, address, *, disconnected_callback):
        client = ClientDouble(disconnected_callback)
        self.clients.append(client)
        client.notify_hook = self.notify_hook
        if self.establish_hook is not None:
            await self.establish_hook(client)
        return client

    def __enter__(self):
        dependencies = {
            "bleak": {"BleakClient": ClientDouble, "BleakGATTCharacteristic": object},
            "bleak_retry_connector": {"establish_connection": self.establish_connection},
            "homeassistant": {},
            "homeassistant.components": {},
            "homeassistant.components.bluetooth": {
                "async_ble_device_from_address": lambda *a, **kw: object(),
            },
            "homeassistant.components.sensor": {
                "SensorEntity": type("SensorEntityDouble", (), {}),
                "SensorEntityDescription": SensorDescriptionDouble,
                "SensorDeviceClass": labels("VOLTAGE", "CURRENT", "POWER", "ENERGY", "FREQUENCY", "POWER_FACTOR", "TEMPERATURE", "DURATION"),
                "SensorStateClass": labels("MEASUREMENT", "TOTAL_INCREASING"),
            },
            "homeassistant.components.button": {
                "ButtonEntity": type("ButtonEntityDouble", (), {}),
                "ButtonEntityDescription": ButtonDescriptionDouble,
            },
            "homeassistant.config_entries": {"ConfigEntry": object},
            "homeassistant.const": {
                "UnitOfElectricCurrent": SimpleNamespace(AMPERE="A"),
                "UnitOfElectricPotential": SimpleNamespace(VOLT="V"),
                "UnitOfEnergy": SimpleNamespace(WATT_HOUR="Wh"),
                "UnitOfFrequency": SimpleNamespace(HERTZ="Hz"),
                "UnitOfPower": SimpleNamespace(WATT="W"),
                "UnitOfTemperature": SimpleNamespace(CELSIUS="°C"),
                "UnitOfTime": SimpleNamespace(SECONDS="s"),
            },
            "homeassistant.core": {"HomeAssistant": HassDouble, "callback": lambda f: f},
            "homeassistant.helpers": {},
            "homeassistant.helpers.device_registry": {"DeviceInfo": dict},
            "homeassistant.helpers.entity_platform": {"AddEntitiesCallback": object},
            "homeassistant.helpers.update_coordinator": {
                "UpdateFailed": UpdateFailed,
                "DataUpdateCoordinator": DataUpdateCoordinatorDouble,
                "CoordinatorEntity": CoordinatorEntityDouble,
            },
            "_atorch_ble_stale": {},
        }
        stubs = {name: module(name, **attrs) for name, attrs in dependencies.items()}
        for name, stub in stubs.items():
            parent, _, child = name.rpartition(".")
            if parent in stubs:
                setattr(stubs[parent], child, stub)
        self.stack.enter_context(patch.dict(sys.modules, stubs))
        try:
            loaded = {}
            for name in ("const", "parser", "coordinator", "sensor", "button"):
                full_name = f"_atorch_ble_stale.{name}"
                spec = importlib.util.spec_from_file_location(
                    full_name, SOURCE_ROOT / "custom_components" / "atorch_at24" / f"{name}.py"
                )
                assert spec is not None and spec.loader is not None
                obj = importlib.util.module_from_spec(spec)
                sys.modules[full_name] = obj
                spec.loader.exec_module(obj)
                loaded[name] = obj
            self.const = loaded["const"]
            self.parser = loaded["parser"]
            coordinator_module = loaded["coordinator"]
            # Replace this module's asyncio reference only, never the test runner's loop.
            coordinator_module.asyncio = SimpleNamespace(
                Lock=asyncio.Lock, CancelledError=asyncio.CancelledError,
                get_event_loop=lambda: self.clock, sleep=self.clock.sleep,
                shield=asyncio.shield, gather=asyncio.gather,

            )
            self.coordinator = coordinator_module.AtorchBLECoordinator(
                self.hass, "synthetic-device", "Synthetic AT24", self.update_interval
            )
            entry = SimpleNamespace(data={"address": "synthetic-device"})
            self.sensors = {
                desc.key: loaded["sensor"].AtorchSensorEntity(self.coordinator, entry, desc)
                for desc in loaded["sensor"].SENSOR_DESCRIPTIONS
            }
            self.buttons = {
                desc.key: loaded["button"].AtorchButtonEntity(self.coordinator, entry, desc)
                for desc in loaded["button"].BUTTON_DESCRIPTIONS
            }
            self.power = self.sensors["power"]
            # This publisher is a double, not the real HA state machine/recorder.
            self.coordinator.async_add_listener(self.publish_power)
            env = Environment(undefined=StrictUndefined)
            env.globals.update(
                states=lambda entity: self.published_state,
                state_attr=lambda entity, attr: "W",
                is_number=is_number,
            )
            source = (ROOT / "tests/fixtures/monthly_estimate_pr1.jinja").read_text(encoding="utf-8")
            self.template = env.from_string(source)
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *exc):
        for task in self.hass.tasks:
            task.cancel()
        self.stack.close()

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *exc):
        try:
            await self.coordinator.async_stop()
            await pump()
            assert all(t.done() for t in self.hass.tasks), "owned task survived stop"
            assert not self.clock.active, "active timer survived stop"
        finally:
            self.__exit__(*exc)

    async def advance(self, target):
        await pump()
        self.clock.advance(target)
        await pump()

    def publish_power(self):
        value = self.power.native_value
        self.published_state = (
            "unavailable" if not self.power.available
            else "unknown" if value is None else str(value)
        )
        self.state_history.append(self.published_state)
        self.entity_history.append({
            key: (sensor.available, sensor.native_value)
            for key, sensor in self.sensors.items()
        })

    def emit(self, watts=100, mode=1, notification_type=1):
        # Synthetic protocol packet: real reassembly, parse_notification and sensors.
        self.clients[-1].emit(self.packet(watts, mode, notification_type))

    @staticmethod
    def packet(watts=100, mode=1, notification_type=1):
        packet = bytearray(36)
        packet[:4] = bytes((0xFF, 0x55, notification_type, mode))
        packet[10:13] = round(watts * 10).to_bytes(3, "big")
        return packet

    def projection(self):
        return self.template.render().splitlines()[0]

    def snapshot(self, stage):
        coordinator = self.coordinator
        return {
            "stage": stage,
            "time_s": self.clock.now,
            "connected": coordinator.connected,
            "has_data": coordinator.data is not None,
            "power_available": self.power.available,
            "power_native_w": self.power.native_value,
            "published_power": self.published_state,
            "listener_publications": coordinator.publications,
            "last_data_time_s": coordinator._last_data_time,
            "projection": self.projection(),
        }
