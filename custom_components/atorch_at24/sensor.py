"""Sensor entities for Atorch AT24 Energy Meter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_AC_FULL, MODE_AC_SIMPLE, MODE_DC
from .coordinator import AtorchBLECoordinator
from .parser import AtorchMeterData


@dataclass(frozen=True, kw_only=True)
class AtorchSensorEntityDescription(SensorEntityDescription):
    """Sensor entity description for Atorch AT24."""

    value_fn: Callable[[AtorchMeterData], Any]
    available_modes: tuple[int, ...] = (MODE_AC_FULL, MODE_AC_SIMPLE, MODE_DC)


SENSOR_DESCRIPTIONS: tuple[AtorchSensorEntityDescription, ...] = (
    AtorchSensorEntityDescription(
        key="voltage",
        translation_key="voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: d.voltage,
        available_modes=(MODE_AC_FULL, MODE_AC_SIMPLE, MODE_DC),
    ),
    AtorchSensorEntityDescription(
        key="current",
        translation_key="current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: d.current,
        available_modes=(MODE_AC_FULL, MODE_AC_SIMPLE, MODE_DC),
    ),
    AtorchSensorEntityDescription(
        key="power",
        translation_key="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.power,
        available_modes=(MODE_AC_FULL,),
    ),
    AtorchSensorEntityDescription(
        key="energy",
        translation_key="energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: d.energy,
        available_modes=(MODE_AC_FULL, MODE_AC_SIMPLE, MODE_DC),
    ),
    AtorchSensorEntityDescription(
        key="charge",
        translation_key="charge",
        native_unit_of_measurement="Ah",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        icon="mdi:battery-charging",
        value_fn=lambda d: d.charge,
        available_modes=(MODE_AC_SIMPLE, MODE_DC),
    ),
    AtorchSensorEntityDescription(
        key="frequency",
        translation_key="frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.frequency,
        available_modes=(MODE_AC_FULL,),
    ),
    AtorchSensorEntityDescription(
        key="power_factor",
        translation_key="power_factor",
        native_unit_of_measurement=None,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: d.power_factor,
        available_modes=(MODE_AC_FULL,),
    ),
    AtorchSensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.temperature,
        available_modes=(MODE_AC_FULL, MODE_AC_SIMPLE, MODE_DC),
    ),
    AtorchSensorEntityDescription(
        key="d_plus_voltage",
        translation_key="d_plus_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:usb",
        value_fn=lambda d: d.d_plus_voltage,
        available_modes=(MODE_DC,),
    ),
    AtorchSensorEntityDescription(
        key="d_minus_voltage",
        translation_key="d_minus_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:usb",
        value_fn=lambda d: d.d_minus_voltage,
        available_modes=(MODE_DC,),
    ),
    AtorchSensorEntityDescription(
        key="on_time",
        translation_key="on_time",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:timer-outline",
        value_fn=lambda d: d.on_time,
        available_modes=(MODE_DC,),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Atorch AT24 sensor entities."""
    coordinator: AtorchBLECoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[AtorchSensorEntity] = [
        AtorchSensorEntity(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    ]

    async_add_entities(entities)


class AtorchSensorEntity(
    CoordinatorEntity[AtorchBLECoordinator],
    SensorEntity,
):
    """Sensor entity for an Atorch AT24 measurement."""

    entity_description: AtorchSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AtorchBLECoordinator,
        entry: ConfigEntry,
        description: AtorchSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.data['address']}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"])},
            name=coordinator.device_name,
            manufacturer="ATORCH",
            model="AT24",
        )

    @property
    def available(self) -> bool:
        """Return whether this sensor has received data in the current mode."""
        data = self.coordinator.data
        if data is None:
            return False
        return data.mode in self.entity_description.available_modes

    @property
    def native_value(self) -> float | int | None:
        """Return the sensor value."""
        data = self.coordinator.data
        if data is None:
            return None
        return self.entity_description.value_fn(data)
