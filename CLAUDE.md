# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Home Assistant custom integration for the **ATORCH AT24 Energy Meter** — a BLE (Bluetooth Low Energy) energy meter. Distributed via HACS. All integration code lives in `custom_components/atorch_at24/`.

## Running Tests

Tests use a custom module loader to avoid Home Assistant dependencies:

```bash
python3 tests/test_parser.py
```

There is no pytest/tox setup. The test file loads `const.py` and `parser.py` directly with fake package stubs to satisfy relative imports.

## Architecture

**Data flow:** BLE device → `coordinator.py` (notification callback) → `parser.py` (36-byte binary decode) → `sensor.py` / `button.py` (HA entities)

### Key modules

- **`coordinator.py`** — `AtorchBLECoordinator(DataUpdateCoordinator)`: manages BLE connection via bleak, subscribes to GATT notifications, reassembles 20+16 byte chunks into 36-byte payloads, implements watchdog/reconnection. Push-based (not polled). Has configurable state update throttle (`update_interval_seconds`).
- **`parser.py`** — `parse_notification()`: decodes binary payloads using field definitions from `const.py`. `build_command()`: constructs 10-byte BLE commands with XOR checksum.
- **`const.py`** — BLE UUIDs, protocol constants, and field definitions for 3 device modes (AC Full, AC Simple, DC). Each mode has different field offsets/resolutions defined in `MODE1_FIELDS`, `MODE2_FIELDS`, `MODE3_FIELDS`.
- **`config_flow.py`** — Three setup paths (BLE auto-discovery, scan+select, manual MAC entry) plus `OptionsFlow` for runtime configuration. All paths go through a `settings` step before creating the entry.
- **`sensor.py`** — 11 sensors defined via `AtorchSensorEntityDescription` with per-mode availability (`available_modes` tuple). Uses `CoordinatorEntity` pattern.
- **`button.py`** — 3 command buttons (clear all/capacity/time) that send BLE GATT writes.

### BLE Protocol

- Service UUID: `0000ffe0-...`, Characteristic: `0000ffe1-...`
- Notifications: header `FF 55`, type byte, mode byte, then mode-specific fields
- Commands: 10 bytes, `FF 55 11 <mode> <cmd> ...`, XOR `0x44` checksum
- Device advertises as "JDY-19"

## Integration Branding

Icons in `custom_components/atorch_at24/` are for the repo only. For icons to appear in the HA UI, they must be submitted to [home-assistant/brands](https://github.com/home-assistant/brands) under `custom_integrations/atorch_at24/`.
