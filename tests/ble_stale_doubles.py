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
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []
        self.sleep_budget: int | None = None

    def time(self):
        return self.now

    async def sleep(self, seconds):
        if self.sleep_budget is not None:
            if self.sleep_budget == 0:
                raise asyncio.CancelledError
            self.sleep_budget -= 1
        self.sleeps.append(seconds)
        self.now += seconds


class DeferredTask:
    """Queue tasks without executing reconnects until the test requests it."""
    def __init__(self, coroutine):
        self.coroutine = coroutine

    def cancel(self):
        if self.coroutine is not None:
            self.coroutine.close()
            self.coroutine = None

    async def run(self):
        coroutine, self.coroutine = self.coroutine, None
        assert coroutine is not None, "task already consumed or cancelled"
        return await coroutine


class HassDouble:
    def __init__(self):
        self.tasks = []

    def async_create_task(self, coroutine):
        task = DeferredTask(coroutine)
        self.tasks.append(task)
        return task


class ClientDouble:
    def __init__(self, disconnected_callback):
        self.is_connected = True
        self.disconnected_callback = disconnected_callback
        self.notification_callback = None

    async def start_notify(self, uuid, callback):
        self.notification_callback = callback

    async def stop_notify(self, uuid):
        self.notification_callback = None

    def drop(self):
        self.is_connected = False
        self.disconnected_callback(self)

    async def disconnect(self):
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

    async def establish_connection(self, client_class, device, address, *, disconnected_callback):
        client = ClientDouble(disconnected_callback)
        self.clients.append(client)
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
            for name in ("const", "parser", "coordinator", "sensor"):
                full_name = f"_atorch_ble_stale.{name}"
                spec = importlib.util.spec_from_file_location(
                    full_name, ROOT / "custom_components" / "atorch_at24" / f"{name}.py"
                )
                assert spec is not None and spec.loader is not None
                obj = importlib.util.module_from_spec(spec)
                sys.modules[full_name] = obj
                spec.loader.exec_module(obj)
                loaded[name] = obj
            self.const = loaded["const"]
            coordinator_module = loaded["coordinator"]
            # Replace this module's asyncio reference only, never the test runner's loop.
            coordinator_module.asyncio = SimpleNamespace(
                Lock=asyncio.Lock, CancelledError=asyncio.CancelledError,
                get_event_loop=lambda: self.clock, sleep=self.clock.sleep,
            )
            self.coordinator = coordinator_module.AtorchBLECoordinator(
                self.hass, "synthetic-device", "Synthetic AT24", self.update_interval
            )
            entry = SimpleNamespace(data={"address": "synthetic-device"})
            self.sensors = {
                desc.key: loaded["sensor"].AtorchSensorEntity(self.coordinator, entry, desc)
                for desc in loaded["sensor"].SENSOR_DESCRIPTIONS
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

    def publish_power(self):
        value = self.power.native_value
        self.published_state = (
            "unavailable" if not self.power.available
            else "unknown" if value is None else str(value)
        )
        self.state_history.append(self.published_state)

    def emit(self, watts=100, mode=1, notification_type=1):
        # Synthetic protocol packet: real reassembly, parse_notification and sensors.
        packet = bytearray(36)
        packet[:4] = bytes((0xFF, 0x55, notification_type, mode))
        packet[10:13] = round(watts * 10).to_bytes(3, "big")
        self.clients[-1].emit(packet)

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
