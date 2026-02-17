"""Constants for the Atorch AT24 Energy Meter integration."""

DOMAIN = "atorch_at24"

# BLE UUIDs
SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

# BLE device name
DEVICE_NAME = "JDY-19"

# Notification header
HEADER_BYTE_0 = 0xFF
HEADER_BYTE_1 = 0x55

# Notification type identifiers
NOTIFICATION_TYPE = 0x01  # Byte 2 in notification
COMMAND_TYPE = 0x11  # Byte 2 in commands

# Device modes (adu values)
MODE_AC_FULL = 1  # AC: voltage, current, power, energy, freq, PF, temp
MODE_AC_SIMPLE = 2  # AC simplified: voltage, current, charge, energy, temp
MODE_DC = 3  # DC/USB: voltage, current, charge, energy, D+/D-, temp, time

# --- Mode 1 (AC Full) field definitions ---
# (offset, width_bytes, name, resolution, unit)
MODE1_FIELDS = {
    "voltage": (4, 3, 0.1, "V"),
    "current": (7, 3, 0.001, "A"),
    "power": (10, 3, 0.1, "W"),
    "energy": (13, 4, 0.01, "kWh"),
    "price": (17, 3, 1, "cents"),
    "frequency": (20, 2, 0.1, "Hz"),
    "power_factor": (22, 2, 0.001, ""),
    "temperature": (24, 2, 1, "°C"),
    "backlight": (30, 1, 1, "s"),
}

# --- Mode 2 (AC Simplified) field definitions ---
MODE2_FIELDS = {
    "voltage": (4, 3, 0.1, "V"),
    "current": (7, 3, 0.001, "A"),
    "charge": (10, 3, 0.01, "Ah"),
    "energy": (13, 4, 0.01, "kWh"),
    "price": (17, 3, 1, "cents"),
    "temperature": (24, 2, 1, "°C"),
    "backlight": (30, 1, 1, "s"),
}

# --- Mode 3 (DC) field definitions ---
MODE3_FIELDS = {
    "voltage": (4, 3, 0.01, "V"),
    "current": (7, 3, 0.01, "A"),
    "charge": (10, 3, 0.001, "Ah"),
    "energy": (13, 4, 0.01, "Wh"),
    "d_plus_voltage": (17, 2, 0.01, "V"),
    "d_minus_voltage": (19, 2, 0.01, "V"),
    "temperature": (21, 2, 1, "°C"),
    "on_time": (23, 2, 1, "s"),
    "backlight": (30, 1, 1, "s"),
}

# BLE command bytes (a2 values)
CMD_CLEAR_ALL = 0x01
CMD_CLEAR_CAPACITY = 0x02
CMD_CLEAR_TIME = 0x03
CMD_SET_BACKLIGHT = 0x21
CMD_SET_PRICE = 0x22
CMD_BUTTON_SET = 0x31
CMD_BUTTON_OK = 0x32
CMD_BUTTON_PLUS = 0x33
CMD_BUTTON_MINUS = 0x34

# Checksum XOR constant
CHECKSUM_XOR = 0x44

# Reconnection / timeout
NOTIFICATION_TIMEOUT_S = 60
RECONNECT_INTERVAL_S = 5

# Update interval (throttle)
CONF_UPDATE_INTERVAL = "update_interval"
DEFAULT_UPDATE_INTERVAL = 0  # seconds (0 = no throttle, instant updates)
