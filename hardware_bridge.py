"""PC-to-ESP32 serial bridge for the physical camera mount.

It sends bounded target angles and indicator states; the ESP32 independently
enforces final limits, the emergency stop, and its command watchdog.
"""
from __future__ import annotations

import math
import threading
import time

from hardware_protocol import MotorCommand, make_command, make_led, make_stop


class Esp32Bridge:
    """Best-effort transport; errors are surfaced so the caller can stop tracking."""
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.25):
        try:
            import serial
        except ImportError as error:
            raise RuntimeError("Install pyserial before using physical hardware: py -m pip install pyserial") from error
        self.serial = serial.Serial(port, baudrate=baudrate, timeout=timeout, write_timeout=timeout)
        time.sleep(1.5)  # ESP32 commonly resets when the serial port opens.
        self.serial.reset_input_buffer()
        self.sequence = 0

    def send(self, pan_deg: float, tilt_deg: float, lock_led: bool, alert_led: bool) -> MotorCommand:
        line = make_command(self.sequence, pan_deg, tilt_deg, lock_led, alert_led)
        self.serial.reset_input_buffer()
        self.serial.write(line.encode("ascii"))
        self.serial.flush()
        response = self.serial.readline().decode("ascii", errors="replace").strip()
        fields = response.split(",")
        if not response:
            raise TimeoutError(f"ESP32 did not acknowledge command {self.sequence} before timeout")
        if len(fields) < 4 or fields[0] != "STATUS":
            raise RuntimeError(f"ESP32 rejected command {self.sequence}: {response}")
        try:
            acknowledged_sequence = int(fields[1])
            actual_pan = float(fields[2])
            actual_tilt = float(fields[3])
        except ValueError as error:
            raise RuntimeError(f"Invalid ESP32 response for command {self.sequence}: {response}") from error
        if acknowledged_sequence != self.sequence:
            raise RuntimeError(
                f"ESP32 acknowledged command {acknowledged_sequence}, expected {self.sequence}"
            )
        if not math.isfinite(actual_pan) or not math.isfinite(actual_tilt):
            raise RuntimeError(f"ESP32 returned non-finite angles for command {self.sequence}")
        command = MotorCommand(self.sequence, actual_pan, actual_tilt, lock_led, alert_led)
        self.sequence += 1
        return command

    def safe_stop(self) -> None:
        """Detach motor output; do not command the mount to return to centre."""
        line = make_stop(self.sequence)
        self.serial.write(line.encode("ascii"))
        self.serial.flush()
        self.sequence += 1

    def set_indicators(self, lock_led: bool, alert_led: bool) -> None:
        """Update LEDs while the mount is detached; this never moves a servo."""
        self.serial.write(make_led(lock_led, alert_led).encode("ascii"))
        self.serial.flush()

    def close(self) -> None:
        try:
            self.safe_stop()
        finally:
            self.serial.close()


class SafeEsp32Controller:
    """Heartbeat controller that separates motor safety from slow vision inference."""
    def __init__(self, port: str, interval_seconds: float = 0.10, bridge: Esp32Bridge | None = None):
        # An injected bridge keeps transport tests independent of serial hardware.
        self.bridge = bridge or Esp32Bridge(port)
        self.interval = interval_seconds
        self._lock = threading.Lock()
        self._state = (0.0, 0.0, False, False, False)  # pan, tilt, lock, alert, motion active
        self.error: Exception | None = None
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)
        self._thread.start()

    def set_state(self, pan_deg: float, tilt_deg: float, lock_led: bool, alert_led: bool, motion_active: bool) -> None:
        with self._lock:
            self._state = (pan_deg, tilt_deg, lock_led, alert_led, motion_active)

    def _heartbeat(self) -> None:
        was_active = False
        while not self._stop_event.is_set():
            with self._lock:
                pan, tilt, lock_led, alert_led, active = self._state
            try:
                if active:
                    self.bridge.send(pan, tilt, lock_led, alert_led)
                else:
                    if was_active:
                        self.bridge.safe_stop()
                    # Alerts remain visible while the mount is safely detached.
                    self.bridge.set_indicators(False, alert_led)
                tick = getattr(self.bridge, "tick", None)
                if tick:
                    tick()
                was_active = active
            except Exception as error:  # The caller must stop live tracking on this fault.
                self.error = error
                return
            self._stop_event.wait(self.interval)

    def close(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=1.0)
        self.bridge.close()
