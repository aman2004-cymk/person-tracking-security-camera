"""Shared, testable protocol for the computer-to-ESP32 safety boundary.

The PC may request an angle and indicator state. The ESP32 owns final safety:
range clamping, emergency-stop handling, and command-watchdog stop behavior.
"""
from dataclasses import dataclass
import re

PAN_MIN, PAN_MAX = -90.0, 90.0
TILT_MIN, TILT_MAX = -30.0, 60.0
COMMAND_PATTERN = re.compile(
    r"^CMD,(\d+),(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),([01]),([01])$"
)
STOP_PATTERN = re.compile(r"^STOP,(\d+)$")
LED_PATTERN = re.compile(r"^LED,([01]),([01])$")


@dataclass(frozen=True)
class MotorCommand:
    sequence: int
    pan_deg: float
    tilt_deg: float
    lock_led: bool
    alert_led: bool


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def make_command(sequence: int, pan_deg: float, tilt_deg: float, lock_led: bool, alert_led: bool) -> str:
    """Create one newline-terminated command; clamp PC values before sending."""
    if sequence < 0:
        raise ValueError("sequence must be non-negative")
    pan = clamp(float(pan_deg), PAN_MIN, PAN_MAX)
    tilt = clamp(float(tilt_deg), TILT_MIN, TILT_MAX)
    return f"CMD,{sequence},{pan:.2f},{tilt:.2f},{int(lock_led)},{int(alert_led)}\n"


def parse_command(line: str) -> MotorCommand:
    """Parse a command for protocol tests or a PC-side monitor."""
    match = COMMAND_PATTERN.fullmatch(line.strip())
    if not match:
        raise ValueError("invalid motor command")
    sequence, pan, tilt, lock, alert = match.groups()
    return MotorCommand(
        int(sequence), clamp(float(pan), PAN_MIN, PAN_MAX), clamp(float(tilt), TILT_MIN, TILT_MAX), bool(int(lock)), bool(int(alert))
    )


def make_stop(sequence: int) -> str:
    """Create an immediate no-motion request; it must not re-centre the mount."""
    if sequence < 0:
        raise ValueError("sequence must be non-negative")
    return f"STOP,{sequence}\n"


def make_led(lock_led: bool, alert_led: bool) -> str:
    """Update indicators without attaching or moving either servo."""
    return f"LED,{int(lock_led)},{int(alert_led)}\n"
