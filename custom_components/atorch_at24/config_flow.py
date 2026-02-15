"""Config flow for Atorch AT24 Energy Meter."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, SERVICE_UUID

_LOGGER = logging.getLogger(__name__)

DEVICE_NAME_PREFIX = "JDY-19"


class AtorchAT24ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Atorch AT24."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._address: str | None = None
        self._name: str | None = None
        self._discovered_devices: dict[str, str] = {}

    async def async_step_bluetooth(
        self,
        discovery_info: BluetoothServiceInfoBleak,
    ) -> FlowResult:
        """Handle bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._discovery_info = discovery_info
        self._address = discovery_info.address
        self._name = discovery_info.name or "Atorch AT24"

        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Confirm Bluetooth discovery."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._name or "Atorch AT24",
                data={
                    "address": self._address,
                    "name": self._name,
                },
            )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._name},
        )

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle user-initiated setup: scan for devices or enter manually."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input.get("address", "")

            if address == "__manual__":
                return await self.async_step_manual()

            # User picked a discovered device
            name = self._discovered_devices.get(address, "Atorch AT24")
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=name,
                data={
                    "address": address,
                    "name": name,
                },
            )

        # Scan for BLE devices
        self._discovered_devices = {}
        configured_addresses = {
            entry.data["address"]
            for entry in self._async_current_entries()
            if "address" in entry.data
        }

        # Get all discovered bluetooth devices from HA's scanner
        compatible: dict[str, str] = {}
        other: dict[str, str] = {}

        # Try both connectable and non-connectable to find all devices
        seen_addresses: set[str] = set()
        for connectable in (True, False):
            try:
                service_infos = bluetooth.async_discovered_service_info(
                    self.hass, connectable=connectable
                )
            except Exception:
                continue
            for info in service_infos:
                if info.address in configured_addresses:
                    continue
                if info.address in seen_addresses:
                    continue
                seen_addresses.add(info.address)

                if not info.name or info.name.strip() == "":
                    label = f"Unknown ({info.address})"
                else:
                    label = f"{info.name} ({info.address})"

                # Check if this looks like a compatible device
                is_compatible = False
                if info.name and DEVICE_NAME_PREFIX in info.name:
                    is_compatible = True
                for uuid in info.service_uuids:
                    if SERVICE_UUID.lower() in str(uuid).lower():
                        is_compatible = True

                if is_compatible:
                    compatible[info.address] = f"⚡ {label}"
                else:
                    other[info.address] = label

        # Compatible devices first, then others
        self._discovered_devices = {**compatible, **other}

        # Always show the form — manual option is always available
        device_options = dict(self._discovered_devices)
        device_options["__manual__"] = "✏️ Enter MAC address manually..."

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("address"): vol.In(device_options),
                }
            ),
            errors=errors,
        )

    async def async_step_manual(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle manual MAC address entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input["address"].upper().strip()
            name = user_input.get("name", "Atorch AT24").strip()

            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=name,
                data={
                    "address": address,
                    "name": name,
                },
            )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required("address"): str,
                    vol.Optional("name", default="Atorch AT24"): str,
                }
            ),
            errors=errors,
        )
