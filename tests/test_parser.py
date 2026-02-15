"""Unit tests for the Atorch AT24 BLE notification parser."""

import sys
import os
import importlib.util

# Load modules directly to avoid triggering __init__.py (which imports homeassistant)
_BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "atorch_at24")

def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# Load const first (parser depends on it via relative import)
_const_mod = _load_module(
    "custom_components.atorch_at24.const",
    os.path.join(_BASE, "const.py"),
)
# Fake the package so relative imports work
sys.modules["custom_components"] = type(sys)("custom_components")
sys.modules["custom_components.atorch_at24"] = type(sys)("custom_components.atorch_at24")
sys.modules["custom_components.atorch_at24"].const = _const_mod

_parser_mod = _load_module(
    "custom_components.atorch_at24.parser",
    os.path.join(_BASE, "parser.py"),
)

# Pull symbols into local namespace
AtorchMeterData = _parser_mod.AtorchMeterData
build_command = _parser_mod.build_command
parse_notification = _parser_mod.parse_notification

CMD_CLEAR_ALL = _const_mod.CMD_CLEAR_ALL
CMD_CLEAR_CAPACITY = _const_mod.CMD_CLEAR_CAPACITY
CMD_CLEAR_TIME = _const_mod.CMD_CLEAR_TIME
MODE_AC_FULL = _const_mod.MODE_AC_FULL
MODE_AC_SIMPLE = _const_mod.MODE_AC_SIMPLE
MODE_DC = _const_mod.MODE_DC


# ---- Sample packets from the reverse engineering docs ----

# Sample from docs: Mode 3 (DC/USB)
# FF 55 01 03 00 01 FC 00 00 00 00 00 00 00 00 00 00 00 08 00
# 07 00 13 00 00 00 06 3C 0D AC 00 00 03 E8 64 29
SAMPLE_MODE3_CHUNK1 = bytes([
    0xFF, 0x55, 0x01, 0x03, 0x00, 0x01, 0xFC, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x08, 0x00,
])
SAMPLE_MODE3_CHUNK2 = bytes([
    0x07, 0x00, 0x13, 0x00, 0x00, 0x00, 0x06, 0x3C,
    0x0D, 0xAC, 0x00, 0x00, 0x03, 0xE8, 0x64, 0x29,
])
SAMPLE_MODE3 = SAMPLE_MODE3_CHUNK1 + SAMPLE_MODE3_CHUNK2


def test_parse_mode3_sample():
    """Test parsing the sample Mode 3 packet from the docs."""
    result = parse_notification(SAMPLE_MODE3)
    assert result is not None
    assert result.mode == MODE_DC

    # Voltage: bytes 4-6 = 0x00, 0x01, 0xFC = 508, * 0.01 = 5.08 V
    assert result.voltage == round(508 * 0.01, 4)

    # Current: bytes 7-9 = 0x00, 0x00, 0x00 = 0, * 0.01 = 0.0 A
    assert result.current == 0.0

    # Charge: bytes 10-12 = 0x00, 0x00, 0x00 = 0, * 0.001 = 0.0 Ah
    assert result.charge == 0.0

    # Energy: bytes 13-16 = 0x00, 0x00, 0x00, 0x00 = 0, * 0.01 = 0.0 Wh
    assert result.energy == 0.0

    # D+ Voltage: bytes 17-18 = 0x00, 0x08 = 8, * 0.01 = 0.08 V
    # Wait, re-examine: at offset 17, width 2 in mode3, that's bytes at indices 17 and 18
    # Byte 17 = 0x00, Byte 18 = 0x08 => value = 8, * 0.01 = 0.08
    # Actually, we need to recheck indices: chunk1 is indices 0-19, chunk2 is indices 20-35
    # Byte 17 = SAMPLE_MODE3[17] = 0x00, Byte 18 = SAMPLE_MODE3[18] = 0x08
    assert result.d_plus_voltage == round(8 * 0.01, 4)

    # D- Voltage: bytes 19-20 = 0x00, 0x07 = 7 * 0.01 = 0.07 V
    assert result.d_minus_voltage == round(7 * 0.01, 4)

    # Temperature: bytes 21-22 = 0x00, 0x13 = 19 °C
    assert result.temperature == 19

    # On Time: bytes 23-24 = 0x00, 0x00 = 0 seconds
    assert result.on_time == 0

    # Mode 3 should not have power, frequency, power_factor
    assert result.power is None
    assert result.frequency is None
    assert result.power_factor is None


def test_parse_mode1():
    """Test parsing a synthetic Mode 1 (AC Full) packet."""
    data = bytearray(36)
    data[0] = 0xFF
    data[1] = 0x55
    data[2] = 0x01
    data[3] = MODE_AC_FULL  # adu = 1

    # Voltage: offset 4, width 3, resolution 0.1 V
    # Set to 2200 => 220.0 V
    data[4] = 0x00
    data[5] = 0x08
    data[6] = 0x98  # 0x898 = 2200

    # Current: offset 7, width 3, resolution 0.001 A
    # Set to 1500 => 1.5 A
    data[7] = 0x00
    data[8] = 0x05
    data[9] = 0xDC  # 0x5DC = 1500

    # Power: offset 10, width 3, resolution 0.1 W
    # Set to 3300 => 330.0 W
    data[10] = 0x00
    data[11] = 0x0C
    data[12] = 0xE4  # 0xCE4 = 3300

    # Energy: offset 13, width 4, resolution 0.01 kWh
    # Set to 12345 => 123.45 kWh
    data[13] = 0x00
    data[14] = 0x00
    data[15] = 0x30
    data[16] = 0x39  # 0x3039 = 12345

    # Frequency: offset 20, width 2, resolution 0.1 Hz
    # Set to 600 => 60.0 Hz
    data[20] = 0x02
    data[21] = 0x58  # 0x258 = 600

    # Power Factor: offset 22, width 2, resolution 0.001
    # Set to 950 => 0.95
    data[22] = 0x03
    data[23] = 0xB6  # 0x3B6 = 950

    # Temperature: offset 24, width 2, resolution 1 °C
    # Set to 25
    data[24] = 0x00
    data[25] = 0x19  # 25

    # Backlight: offset 30, width 1
    data[30] = 30

    result = parse_notification(bytes(data))
    assert result is not None
    assert result.mode == MODE_AC_FULL
    assert result.voltage == round(2200 * 0.1, 4)
    assert result.current == round(1500 * 0.001, 4)
    assert result.power == round(3300 * 0.1, 4)
    assert result.energy == round(12345 * 0.01, 4)
    assert result.frequency == round(600 * 0.1, 4)
    assert result.power_factor == round(950 * 0.001, 4)
    assert result.temperature == 25
    assert result.backlight == 30
    # Mode 1 should not have charge, d+/d-, on_time
    assert result.charge is None
    assert result.d_plus_voltage is None
    assert result.d_minus_voltage is None
    assert result.on_time is None


def test_parse_mode2():
    """Test parsing a synthetic Mode 2 (AC Simplified) packet."""
    data = bytearray(36)
    data[0] = 0xFF
    data[1] = 0x55
    data[2] = 0x01
    data[3] = MODE_AC_SIMPLE  # adu = 2

    # Voltage: offset 4, width 3, resolution 0.1 V => 1200 => 120.0 V
    data[4] = 0x00
    data[5] = 0x04
    data[6] = 0xB0  # 0x4B0 = 1200

    # Current: offset 7, width 3, resolution 0.001 A => 500 => 0.5 A
    data[7] = 0x00
    data[8] = 0x01
    data[9] = 0xF4  # 0x1F4 = 500

    # Charge: offset 10, width 3, resolution 0.01 Ah => 250 => 2.5 Ah
    data[10] = 0x00
    data[11] = 0x00
    data[12] = 0xFA  # 0xFA = 250

    # Temperature: offset 24, width 2 => 30 °C
    data[24] = 0x00
    data[25] = 0x1E

    result = parse_notification(bytes(data))
    assert result is not None
    assert result.mode == MODE_AC_SIMPLE
    assert result.voltage == round(1200 * 0.1, 4)
    assert result.current == round(500 * 0.001, 4)
    assert result.charge == round(250 * 0.01, 4)
    assert result.temperature == 30
    # Mode 2 should not have power, frequency, power_factor, d+/d-, on_time
    assert result.power is None
    assert result.frequency is None
    assert result.power_factor is None


def test_parse_invalid_header():
    """Test that an invalid header returns None."""
    data = bytearray(36)
    data[0] = 0x00  # Wrong header
    data[1] = 0x55
    data[2] = 0x01
    data[3] = 0x03
    assert parse_notification(bytes(data)) is None


def test_parse_wrong_type():
    """Test that a wrong type byte returns None."""
    data = bytearray(36)
    data[0] = 0xFF
    data[1] = 0x55
    data[2] = 0x11  # This is command type, not notification
    data[3] = 0x03
    assert parse_notification(bytes(data)) is None


def test_parse_short_data():
    """Test that data shorter than 36 bytes returns None."""
    assert parse_notification(bytes(10)) is None
    assert parse_notification(bytes(35)) is None
    assert parse_notification(b"") is None


def test_parse_unknown_mode():
    """Test that an unknown mode returns None."""
    data = bytearray(36)
    data[0] = 0xFF
    data[1] = 0x55
    data[2] = 0x01
    data[3] = 99  # Unknown mode
    assert parse_notification(bytes(data)) is None


def test_build_command_clear_all():
    """Test building a clear all command for Mode 3."""
    cmd = build_command(adu=3, a2=CMD_CLEAR_ALL)
    assert len(cmd) == 10
    assert cmd[0] == 0xFF
    assert cmd[1] == 0x55
    assert cmd[2] == 0x11
    assert cmd[3] == 3  # adu
    assert cmd[4] == CMD_CLEAR_ALL
    assert cmd[5] == 0
    assert cmd[6] == 0
    assert cmd[7] == 0
    assert cmd[8] == 0

    # Verify checksum: sum(bytes[2:9]) ^ 0x44
    expected_checksum = (sum(cmd[2:9]) ^ 0x44) & 0xFF
    assert cmd[9] == expected_checksum


def test_build_command_clear_capacity():
    """Test building a clear capacity command."""
    cmd = build_command(adu=3, a2=CMD_CLEAR_CAPACITY)
    assert len(cmd) == 10
    assert cmd[4] == CMD_CLEAR_CAPACITY
    expected_checksum = (sum(cmd[2:9]) ^ 0x44) & 0xFF
    assert cmd[9] == expected_checksum


def test_build_command_clear_time():
    """Test building a clear time command."""
    cmd = build_command(adu=1, a2=CMD_CLEAR_TIME)
    assert len(cmd) == 10
    assert cmd[3] == 1
    assert cmd[4] == CMD_CLEAR_TIME
    expected_checksum = (sum(cmd[2:9]) ^ 0x44) & 0xFF
    assert cmd[9] == expected_checksum


def test_build_command_with_params():
    """Test building a command with additional parameters."""
    cmd = build_command(adu=2, a2=0x22, a3=0x10, a4=0x20, a5=0x30)
    assert len(cmd) == 10
    assert cmd[3] == 2
    assert cmd[4] == 0x22
    assert cmd[5] == 0x10
    assert cmd[6] == 0  # Always zero
    assert cmd[7] == 0x20
    assert cmd[8] == 0x30
    expected_checksum = (sum(cmd[2:9]) ^ 0x44) & 0xFF
    assert cmd[9] == expected_checksum


if __name__ == "__main__":
    # Simple test runner
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
            print(f"  ✓ {test_fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {test_fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {test_fn.__name__}: {type(e).__name__}: {e}")

    print(f"\n{passed} passed, {failed} failed, {passed + failed} total")
    if failed > 0:
        sys.exit(1)
