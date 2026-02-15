"""Button entities for Atorch AT24 Energy Meter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CMD_CLEAR_ALL, CMD_CLEAR_CAPACITY, CMD_CLEAR_TIME, DOMAIN
from .coordinator import AtorchBLECoordinator
from .parser import build_command


@dataclass(frozen=True, kw_only=True)
class AtorchButtonEntityDescription(ButtonEntityDescription):
    """Button entity description for Atorch AT24."""

    command_a2: int
    command_a3: int = 0
    command_a4: int = 0
    command_a5: int = 0


BUTTON_DESCRIPTIONS: tuple[AtorchButtonEntityDescription, ...] = (
    AtorchButtonEntityDescription(
        key="clear_all",
        translation_key="clear_all",
        icon="mdi:delete-sweep",
        command_a2=CMD_CLEAR_ALL,
    ),
    AtorchButtonEntityDescription(
        key="clear_capacity",
        translation_key="clear_capacity",
        icon="mdi:battery-remove",
        command_a2=CMD_CLEAR_CAPACITY,
    ),
    AtorchButtonEntityDescription(
        key="clear_time",
        translation_key="clear_time",
        icon="mdi:timer-off",
        command_a2=CMD_CLEAR_TIME,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Atorch AT24 button entities."""
    coordinator: AtorchBLECoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[AtorchButtonEntity] = [
        AtorchButtonEntity(coordinator, entry, description)
        for description in BUTTON_DESCRIPTIONS
    ]

    async_add_entities(entities)


class AtorchButtonEntity(
    CoordinatorEntity[AtorchBLECoordinator],
    ButtonEntity,
):
    """Button entity to send commands to an Atorch AT24."""

    entity_description: AtorchButtonEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AtorchBLECoordinator,
        entry: ConfigEntry,
        description: AtorchButtonEntityDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{entry.data['address']}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"])},
            name=coordinator.device_name,
            manufacturer="ATORCH",
            model="AT24",
        )

    async def async_press(self) -> None:
        """Handle button press: send BLE command."""
        desc = self.entity_description
        # Use the current device mode (adu) from coordinator data, default to 3
        adu = 3
        data = self.coordinator.data
        if data is not None:
            adu = data.mode

        command = build_command(
            adu=adu,
            a2=desc.command_a2,
            a3=desc.command_a3,
            a4=desc.command_a4,
            a5=desc.command_a5,
        )
        await self.coordinator.async_send_command(command)
