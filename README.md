<p align="center">
  <img src="atorch-logo-png.png" alt="Atorch AT24" width="200">
</p>

# Atorch AT24 Energy Meter — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Custom integration for the **ATORCH AT24 Energy Meter** (J7-H and compatible models), providing real-time energy monitoring via Bluetooth Low Energy.

## Features

- **Auto-discovery** — Automatically detects nearby Atorch AT24 devices via BLE
- **Real-time monitoring** — Receives measurements via BLE notifications (push)
- **Multi-mode support** — AC (Mode 1 & 2) and DC (Mode 3) measurement modes
- **Energy Dashboard** — Compatible with Home Assistant's Energy dashboard
- **Device commands** — Clear accumulated energy, capacity, and time counters

### Sensors

| Sensor | Unit | Modes |
| --- | --- | --- |
| Voltage | V | 1, 2, 3 |
| Current | A | 1, 2, 3 |
| Power | W | 1 |
| Energy | Wh/kWh | 1, 2, 3 |
| Charge | Ah | 2, 3 |
| Frequency | Hz | 1 |
| Power Factor | — | 1 |
| Temperature | °C | 1, 2, 3 |
| D+ Voltage | V | 3 |
| D- Voltage | V | 3 |
| On Time | s | 3 |

### Buttons

| Button | Description |
| --- | --- |
| Clear All | Reset accumulated power and charge |
| Clear Capacity | Reset accumulated capacity |
| Clear Time | Reset accumulated time |

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add this repository URL and select **Integration** as category
4. Search for "Atorch AT24" and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/atorch_at24` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Setup

The integration supports two setup methods:

- **Automatic** — If Bluetooth is enabled, the device will be discovered automatically. A notification will appear to confirm setup.
- **Manual** — Go to Settings → Devices & Services → Add Integration → search "Atorch AT24" and enter the device's Bluetooth MAC address.

## Protocol

Based on the [reverse engineering notes](https://github.com/devanlai/webvoltmeter/blob/master/REVERSE.md) by [@devanlai](https://github.com/devanlai).

## License

MIT
