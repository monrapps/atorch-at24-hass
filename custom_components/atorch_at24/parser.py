"""BLE notification parser for Atorch AT24 Energy Meter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .const import (
    CHECKSUM_XOR,
    COMMAND_TYPE,
    HEADER_BYTE_0,
    HEADER_BYTE_1,
    MODE1_FIELDS,
    MODE2_FIELDS,
    MODE3_FIELDS,
    MODE_AC_FULL,
    MODE_AC_SIMPLE,
    MODE_DC,
    NOTIFICATION_TYPE,
)


@dataclass
class AtorchMeterData:
    """Parsed data from an Atorch energy meter notification."""

    mode: int = 0
    voltage: Optional[float] = None
    current: Optional[float] = None
    power: Optional[float] = None
    energy: Optional[float] = None
    charge: Optional[float] = None
    frequency: Optional[float] = None
    power_factor: Optional[float] = None
    temperature: Optional[int] = None
    price: Optional[float] = None
    d_plus_voltage: Optional[float] = None
    d_minus_voltage: Optional[float] = None
    on_time: Optional[int] = None
    backlight: Optional[int] = None
    raw: bytes = field(default_factory=bytes, repr=False)


def _read_int(data: bytes, offset: int, width: int) -> int:
    """Read a big-endian integer of given width from data at offset."""
    value = 0
    for i in range(width):
        value = (value << 8) | data[offset + i]
    return value


def _parse_fields(
    data: bytes,
    fields: dict[str, tuple[int, int, float, str]],
) -> dict[str, float | int]:
    """Parse fields from notification data using field definitions."""
    result: dict[str, float | int] = {}
    for name, (offset, width, resolution, _unit) in fields.items():
        if offset + width > len(data):
            continue
        raw_value = _read_int(data, offset, width)
        if resolution == 1:
            result[name] = raw_value
        else:
            result[name] = round(raw_value * resolution, 4)
    return result


def parse_notification(data: bytes) -> AtorchMeterData | None:
    """Parse a complete 36-byte notification payload.

    The notification is sent as two BLE chunks (20 + 16 bytes).
    This function expects the reassembled 36-byte payload.

    Returns None if the data is invalid.
    """
    if len(data) < 36:
        return None

    # Validate header
    if data[0] != HEADER_BYTE_0 or data[1] != HEADER_BYTE_1:
        return None

    # Validate notification type
    if data[2] != NOTIFICATION_TYPE:
        return None

    mode = data[3]

    # Select field definitions based on mode
    if mode == MODE_AC_FULL:
        fields = MODE1_FIELDS
    elif mode == MODE_AC_SIMPLE:
        fields = MODE2_FIELDS
    elif mode == MODE_DC:
        fields = MODE3_FIELDS
    else:
        return None

    parsed = _parse_fields(data, fields)
    result = AtorchMeterData(mode=mode, raw=data)

    # Map parsed values to dataclass fields
    for key, value in parsed.items():
        if hasattr(result, key):
            setattr(result, key, value)

    return result


def build_command(adu: int, a2: int, a3: int = 0, a4: int = 0, a5: int = 0) -> bytes:
    """Build a 10-byte BLE command.

    Format: FF 55 11 <adu> <a2> <a3> 00 <a4> <a5> <checksum>

    The checksum is computed by summing bytes 2-8 and XOR'ing with 0x44.
    """
    cmd = bytes([
        HEADER_BYTE_0,
        HEADER_BYTE_1,
        COMMAND_TYPE,
        adu & 0xFF,
        a2 & 0xFF,
        a3 & 0xFF,
        0x00,
        a4 & 0xFF,
        a5 & 0xFF,
    ])
    # Checksum: sum of bytes 2 through 8, then XOR with 0x44
    checksum = (sum(cmd[2:9]) ^ CHECKSUM_XOR) & 0xFF
    return cmd + bytes([checksum])
