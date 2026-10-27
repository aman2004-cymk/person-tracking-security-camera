"""Integration checks for the PC-side hardware heartbeat, without a serial port."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hardware_bridge import Esp32Bridge, SafeEsp32Controller


class FakeSerial:
    def __init__(self):
        self.writes = []
    def write(self, payload): self.writes.append(payload)
    def flush(self): pass


class FakeBridge:
    def __init__(self):
        self.serial = FakeSerial()
        self.sent = []
        self.stopped = 0
        self.closed = False
    def send(self, *state): self.sent.append(state)
    def safe_stop(self): self.stopped += 1
    def set_indicators(self, lock_led, alert_led):
        self.serial.write(f"LED,{int(lock_led)},{int(alert_led)}\n".encode("ascii"))
    def close(self): self.closed = True


class RespondingSerial:
    def __init__(self, response):
        self.response = response
        self.writes = []
        self.cleared = 0
    def reset_input_buffer(self): self.cleared += 1
    def write(self, payload): self.writes.append(payload)
    def flush(self): pass
    def readline(self): return self.response
    def close(self): pass


def wait_for(predicate, timeout=0.4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class HardwareHeartbeatTests(unittest.TestCase):
    def test_real_bridge_requires_matching_firmware_acknowledgement(self):
        serial_port = RespondingSerial(b"STATUS,0,12.00,-4.00,0,0\r\n")
        fake_serial_module = SimpleNamespace(Serial=lambda *args, **kwargs: serial_port)
        with patch.dict("sys.modules", {"serial": fake_serial_module}), patch("hardware_bridge.time.sleep"):
            bridge = Esp32Bridge("COM3")
            command = bridge.send(12.0, -4.0, True, False)
        self.assertEqual((command.pan_deg, command.tilt_deg), (12.0, -4.0))
        self.assertEqual(serial_port.writes, [b"CMD,0,12.00,-4.00,1,0\n"])
        self.assertEqual(serial_port.cleared, 2)

    def test_real_bridge_surfaces_missing_or_invalid_acknowledgement(self):
        for response, error in (
            (b"", TimeoutError),
            (b"SAFE_STOP,ESTOP\r\n", RuntimeError),
            (b"STATUS,8,12.00,-4.00,0,0\r\n", RuntimeError),
        ):
            with self.subTest(response=response):
                serial_port = RespondingSerial(response)
                fake_serial_module = SimpleNamespace(Serial=lambda *args, **kwargs: serial_port)
                with patch.dict("sys.modules", {"serial": fake_serial_module}), patch("hardware_bridge.time.sleep"):
                    bridge = Esp32Bridge("COM3")
                    with self.assertRaises(error):
                        bridge.send(12.0, -4.0, True, False)

    def test_motion_is_heartbeated_then_stopped_without_recentering(self):
        bridge = FakeBridge()
        controller = SafeEsp32Controller("unused", interval_seconds=0.01, bridge=bridge)
        try:
            controller.set_state(12.0, -4.0, True, True, True)
            self.assertTrue(wait_for(lambda: len(bridge.sent) >= 2))
            self.assertEqual(bridge.sent[-1], (12.0, -4.0, True, True))
            controller.set_state(12.0, -4.0, False, True, False)
            self.assertTrue(wait_for(lambda: bridge.stopped >= 1 and b"LED,0,1\n" in bridge.serial.writes))
            self.assertIsNone(controller.error)
        finally:
            controller.close()
        self.assertTrue(bridge.closed)


if __name__ == "__main__":
    unittest.main()
